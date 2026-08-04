"""
DeepSeek Web proof-of-work (`DeepSeekHashV1`) — pure Python, no WASM.

WHY THIS EXISTS
    chat.deepseek.com gates every message behind a hashcash-style challenge:
    fetch a challenge, find a nonce whose digest matches it, and send the nonce
    back in `X-Ds-Pow-Response`. Without it the completion endpoint refuses the
    request, so serving DeepSeek Web at all means solving this.

WHY NOT JUST CALL hashlib.sha3_256
    Because it is NOT SHA3-256, despite looking exactly like it. The upstream
    sponge is configured `{capacity: 256, padding: 6}`, which works out to a
    136-byte rate, a 32-byte output and a 0x06 domain byte — i.e. SHA3-256's
    exact profile. But the permutation runs

        for (let i = 1; i < 24; i++) { theta; rho_pi; chi; iota(i) }

    — **23 rounds starting at round constant 1**, where real Keccak-f[1600] runs
    24 starting at 0. Dropping RC[0] changes every digest, so hashlib disagrees
    with the upstream on the very first byte. That single off-by-one is the whole
    reason a stock hash function cannot be substituted here, and it is why
    OmniRoute ships a compiled WASM blob rather than calling a library.

    Verified against OmniRoute's own JS reference implementation
    (`open-sse/lib/deepseek-pow-solver.cjs`) — see tests/test_deepseek_pow.py for
    the shared vectors.

COST, AND WHY THERE ARE TWO SOLVERS
    Difficulty is typically ~144,000, so a message costs up to that many digests.
    Scalar Python manages ~1,400/s — 50s for an average solve, which is not a
    usable chat latency. The permutation is pure bit-twiddling over 25 lanes with
    no data dependence between candidates, so it vectorises perfectly: the numpy
    path runs the SAME 23 rounds across a whole batch of nonces at once and
    measures ~95,000/s, i.e. ~0.8s average and ~1.5s worst case. That is faster
    than OmniRoute's own JS fallback (5-6s) without needing their WASM blob.

    `solve()` uses the batched path when numpy is importable and falls back to
    the scalar one otherwise; both are checked against the same vectors.
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

try:  # numpy ships in the image already (transitive dep); never make it required
    import numpy as _np
except Exception:  # pragma: no cover - exercised only on a numpy-less install
    _np = None

# Keccak-f[1600] round constants. Index 0 is deliberately retained so the
# 1..23 slice below reads as "skip RC[0]" rather than a magic offset.
_RC: List[int] = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

_ROTC = [
    1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14,
    27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44,
]
_PIL = [
    10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4,
    15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1,
]

_MASK = (1 << 64) - 1

# SHA3-256 profile: rate = 200 - capacity/4 bytes, output = capacity/8 bytes.
_CAPACITY = 256
_RATE = 200 - _CAPACITY // 4      # 136
_OUTLEN = _CAPACITY // 8          # 32
_PAD = 0x06


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f(a: List[int]) -> None:
    """
    In-place Keccak-f[1600], rounds 1..23.

    The range is the deviation from the standard: upstream's loop is
    `for (i = 1; i < 24; i++)`, so RC[0] never contributes. Do not "fix" this to
    range(24) — it would produce correct SHA3 and wrong DeepSeek answers.
    """
    for rnd in range(1, 24):
        # θ
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        for x in range(5):
            d = c[(x + 4) % 5] ^ _rol(c[(x + 1) % 5], 1)
            for y in range(0, 25, 5):
                a[y + x] ^= d

        # ρ and π
        t = a[1]
        for i in range(24):
            j = _PIL[i]
            a[j], t = _rol(t, _ROTC[i]), a[j]

        # χ
        for y in range(0, 25, 5):
            row = a[y:y + 5]
            for x in range(5):
                a[y + x] = row[x] ^ ((~row[(x + 1) % 5] & _MASK) & row[(x + 2) % 5])

        # ι
        a[0] ^= _RC[rnd]


def ds_hash(data: bytes) -> bytes:
    """The 32-byte DeepSeekHashV1 digest of `data`."""
    state = [0] * 25
    padded = bytearray(data)
    padded.append(_PAD)
    while len(padded) % _RATE != 0:
        padded.append(0x00)
    padded[-1] |= 0x80

    for off in range(0, len(padded), _RATE):
        block = padded[off:off + _RATE]
        for i in range(_RATE // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(state)

    out = bytearray()
    while len(out) < _OUTLEN:
        for i in range(_RATE // 8):
            out += state[i].to_bytes(8, "little")
            if len(out) >= _OUTLEN:
                break
        if len(out) < _OUTLEN:
            _keccak_f(state)
    return bytes(out[:_OUTLEN])


# ── numpy-batched permutation ────────────────────────────────────────────────
# Identical maths to _keccak_f, applied across a (batch, 25) array so one pass
# advances thousands of candidate nonces. Kept beside the scalar version rather
# than replacing it so the two can be diffed and cross-checked.

def _keccak_f_batch(a):
    u64 = _np.uint64
    for rnd in range(1, 24):
        c = a[:, 0:5] ^ a[:, 5:10] ^ a[:, 10:15] ^ a[:, 15:20] ^ a[:, 20:25]
        d = _np.empty_like(c)
        for x in range(5):
            r = c[:, (x + 1) % 5]
            d[:, x] = c[:, (x + 4) % 5] ^ ((r << u64(1)) | (r >> u64(63)))
        for y in range(0, 25, 5):
            a[:, y:y + 5] ^= d

        t = a[:, 1].copy()
        for i in range(24):
            j = _PIL[i]
            n = _ROTC[i]
            tmp = a[:, j].copy()
            a[:, j] = (t << u64(n)) | (t >> u64(64 - n))
            t = tmp

        for y in range(0, 25, 5):
            row = a[:, y:y + 5].copy()
            for x in range(5):
                a[:, y + x] = row[:, x] ^ (~row[:, (x + 1) % 5] & row[:, (x + 2) % 5])

        a[:, 0] ^= _RC_NP[rnd]
    return a


_RC_NP = _np.array(_RC, dtype=_np.uint64) if _np is not None else None


def _hash_batch(msgs: List[bytes]):
    """Digest a batch of EQUAL-LENGTH messages. Returns a (B, 32) uint8 array."""
    b = len(msgs)
    n = len(msgs[0])
    padded_len = ((n + 1 + _RATE - 1) // _RATE) * _RATE
    buf = _np.zeros((b, padded_len), dtype=_np.uint8)
    for i, m in enumerate(msgs):
        buf[i, :n] = _np.frombuffer(m, dtype=_np.uint8)
    buf[:, n] = _PAD
    buf[:, padded_len - 1] |= 0x80

    state = _np.zeros((b, 25), dtype=_np.uint64)
    for off in range(0, padded_len, _RATE):
        state[:, : _RATE // 8] ^= buf[:, off:off + _RATE].view(_np.uint64)
        _keccak_f_batch(state)
    return state[:, : _OUTLEN // 8].view(_np.uint8)[:, :_OUTLEN]


def _solve_batched(prefix: bytes, target_hex: str, difficulty: int, chunk: int = 4096) -> int:
    target = bytes.fromhex(target_hex)
    tgt = _np.frombuffer(target, dtype=_np.uint8)
    nonce = 0
    while nonce < difficulty:
        # Group by decimal width: every message in a batch must be the same
        # length, or they would not share a padding layout.
        width = len(str(nonce))
        upper = min(difficulty, 10 ** width)
        while nonce < upper:
            size = min(chunk, upper - nonce)
            msgs = [prefix + str(nonce + i).encode() for i in range(size)]
            digests = _hash_batch(msgs)
            hits = _np.nonzero((digests == tgt).all(axis=1))[0]
            if hits.size:
                return nonce + int(hits[0])
            nonce += size
    return -1


def solve(
    algorithm: str,
    challenge: str,
    salt: str,
    difficulty: int,
    expire_at: int,
    *,
    force_scalar: bool = False,
) -> int:
    """
    Find the nonce whose digest equals `challenge`, or -1.

    Mirrors OmniRoute's `solveDeepSeekPowAsync`: the hashed input is
    `f"{salt}_{expire_at}_{nonce}"` and the comparison is against the full hex
    digest.
    """
    if algorithm != "DeepSeekHashV1":
        raise ValueError(f"Unsupported PoW algorithm: {algorithm!r}")

    prefix = f"{salt}_{expire_at}_".encode()
    difficulty = int(difficulty)

    if _np is not None and not force_scalar:
        return _solve_batched(prefix, challenge.lower(), difficulty)

    target = challenge.lower()
    for nonce in range(difficulty):
        if ds_hash(prefix + str(nonce).encode()).hex() == target:
            return nonce
    return -1


# Kept so a caller can prove at runtime that this module is NOT stock SHA3-256 —
# the distinction is subtle enough to be worth an assertion rather than a comment.
def differs_from_sha3(sample: bytes = b"deepseek") -> bool:
    return ds_hash(sample) != hashlib.sha3_256(sample).digest()
