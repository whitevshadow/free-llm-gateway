"""
Discovery key-rotation tests (SRS §9.3).

Discovery borrows a user key to call /v1/models. The borrow must rotate DAILY
across the provider's active keys — same key all day (a manual refresh must not
burn a second key), next key tomorrow — so no single key holder pays for every
catalog refresh.
"""

from datetime import date, timedelta

from app.services.catalog import _rotation_index


def test_stable_within_a_day():
    today = date(2026, 7, 14)
    assert _rotation_index(3, today) == _rotation_index(3, today)


def test_switches_to_the_next_key_the_next_day():
    day1 = date(2026, 7, 14)
    day2 = day1 + timedelta(days=1)
    assert _rotation_index(3, day2) == (_rotation_index(3, day1) + 1) % 3


def test_wraps_around_the_whole_pool():
    start = date(2026, 7, 14)
    picks = [_rotation_index(3, start + timedelta(days=d)) for d in range(6)]
    # Every key gets used, in order, twice over six days.
    assert picks == picks[:3] * 2
    assert sorted(set(picks)) == [0, 1, 2]


def test_single_key_is_always_index_zero():
    assert _rotation_index(1, date(2026, 7, 14)) == 0
    assert _rotation_index(1, date(2026, 7, 15)) == 0
