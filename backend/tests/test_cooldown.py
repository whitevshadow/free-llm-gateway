"""
Cooldown-escalation and pinning-filter tests (SRS §7.3, §8.3).

These are the pure decision functions — no provider, no network. The point:
a provider's Retry-After must always win, consecutive 429s must climb the
ladder instead of hammering a daily quota every 60 seconds, and a pin must
narrow the candidate set without ever emptying it.
"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from app.core.llm_router import _apply_pin
from app.services.prober import (
    COOLDOWN_LADDER, RETRY_AFTER_CAP, cooldown_seconds, retry_after_seconds,
)


class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


class _FakeError(Exception):
    def __init__(self, headers=None):
        super().__init__("429")
        if headers is not None:
            self.response = _FakeResponse(headers)


# ── the ladder ───────────────────────────────────────────────────────────────

def test_ladder_escalates_and_caps():
    assert cooldown_seconds(1) == COOLDOWN_LADDER[0]      # first 429 → 60s
    assert cooldown_seconds(2) == COOLDOWN_LADDER[1]      # second → 2m
    assert cooldown_seconds(3) == COOLDOWN_LADDER[2]      # third → 5m
    assert cooldown_seconds(50) == COOLDOWN_LADDER[-1]    # stays on the last rung


def test_zero_strikes_still_gets_the_first_rung():
    # Defensive: a caller that forgot to increment must not produce a 0s bench.
    assert cooldown_seconds(0) == COOLDOWN_LADDER[0]


def test_retry_after_beats_the_ladder():
    assert cooldown_seconds(5, retry_after=7) == 7


# ── Retry-After parsing ──────────────────────────────────────────────────────

def test_retry_after_delta_seconds():
    assert retry_after_seconds(_FakeError({"retry-after": "30"})) == 30


def test_retry_after_http_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=120)
    secs = retry_after_seconds(_FakeError({"Retry-After": format_datetime(when)}))
    assert secs is not None
    assert 100 <= secs <= 130   # clock slack


def test_retry_after_is_capped():
    assert retry_after_seconds(_FakeError({"retry-after": "999999"})) == RETRY_AFTER_CAP


def test_retry_after_garbage_and_absence_return_none():
    assert retry_after_seconds(_FakeError({"retry-after": "soonish"})) is None
    assert retry_after_seconds(_FakeError({})) is None
    assert retry_after_seconds(_FakeError()) is None                 # no response at all
    assert retry_after_seconds(_FakeError({"retry-after": "-5"})) is None  # past = absent


# ── pinning filter ───────────────────────────────────────────────────────────

def _row(model, provider_id):
    return {"model": model, "provider_id": provider_id}


def test_pin_narrows_only_models_the_pinned_provider_serves():
    rows = [
        _row("llama", 1), _row("llama", 2),   # both serve llama → keep provider 1 only
        _row("qwen", 2),                       # provider 1 doesn't serve qwen → keep
    ]
    result = _apply_pin(rows, pinned_provider_id=1)
    assert result == [_row("llama", 1), _row("qwen", 2)]


def test_pin_never_empties_a_model_or_the_list():
    rows = [_row("qwen", 2), _row("qwen", 3)]
    # Pinned provider serves nothing here — everything falls back untouched.
    assert _apply_pin(rows, pinned_provider_id=1) == rows


def test_no_pin_is_a_passthrough():
    rows = [_row("llama", 1), _row("llama", 2)]
    assert _apply_pin(rows, None) == rows
