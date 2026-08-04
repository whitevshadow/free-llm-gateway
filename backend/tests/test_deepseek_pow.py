"""
DeepSeekHashV1 regression guard.

The vectors below were produced by OmniRoute's own JS reference implementation
(`OmniRoute/open-sse/lib/deepseek-pow-solver.cjs`, the `U` sponge) so this
module is pinned to the upstream behaviour rather than to our reading of it.

The single most likely future "fix" to `deepseek_pow.py` is changing
`range(1, 24)` to `range(24)`, because 23 rounds looks like an off-by-one bug.
It is not: the upstream loop really is `for (i = 1; i < 24; i++)`, so RC[0] is
never applied. Making it 24 rounds yields correct SHA3-256 and wrong DeepSeek
answers — every PoW would silently fail and DeepSeek Web would stop working with
no error pointing here. test_not_sha3 exists to fail loudly if that happens.
"""

import hashlib

import pytest

from app.services import deepseek_pow as pow_mod

PREFIX = "abc123salt_1785840000_"

# nonce -> full hex digest, from the JS reference.
JS_VECTORS = {
    0: "20bfff01bad73f13233b52aca4e4f088c86ef959740219f1c87e02ff24c3b395",
    1: "ea3f2544ef53f7f833080319a20549f463b4be69072fa831fbc7fe00cfaf4bc4",
    42: "8c5fe51a752a234994bb9acab9b3ecbeb2058a4ce0c92fd7dd102b48a7184c99",
    9999: "932fcee783fca1666c6336e080f9676d9f3dccb8f140f7cfbc013249c8158666",
    123456: "bb99797b87c4fbc23fceaddd9ff03ded182d8b4f3f341aff4dcecf6f668c0f7d",
}


@pytest.mark.parametrize("nonce,expected", sorted(JS_VECTORS.items()))
def test_matches_js_reference(nonce, expected):
    assert pow_mod.ds_hash(f"{PREFIX}{nonce}".encode()).hex() == expected


def test_not_sha3():
    """If this ever passes, the round loop was 'corrected' and PoW is broken."""
    assert pow_mod.ds_hash(b"deepseek") != hashlib.sha3_256(b"deepseek").digest()
    assert pow_mod.differs_from_sha3()


@pytest.mark.parametrize("target", [0, 1, 42, 9999])
def test_solve_recovers_nonce(target):
    salt, expire_at = "abc123salt", 1785840000
    challenge = JS_VECTORS[target]
    got = pow_mod.solve("DeepSeekHashV1", challenge, salt, target + 1, expire_at)
    assert got == target


def test_scalar_and_batched_agree():
    """The numpy path must not drift from the scalar one."""
    if pow_mod._np is None:
        pytest.skip("numpy not installed")
    salt, expire_at, target = "abc123salt", 1785840000, 9999
    challenge = JS_VECTORS[target]
    batched = pow_mod.solve("DeepSeekHashV1", challenge, salt, target + 1, expire_at)
    scalar = pow_mod.solve(
        "DeepSeekHashV1", challenge, salt, target + 1, expire_at, force_scalar=True
    )
    assert batched == scalar == target


def test_rejects_unknown_algorithm():
    with pytest.raises(ValueError):
        pow_mod.solve("DeepSeekHashV2", "00" * 32, "salt", 10, 0)


def test_no_solution_returns_minus_one():
    assert pow_mod.solve("DeepSeekHashV1", "ff" * 32, "salt", 50, 0) == -1
