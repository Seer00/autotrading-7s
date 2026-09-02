from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.ports.clock import ClockPort

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def test_fake_clock_satisfies_port():
    clock: ClockPort = FakeClock(current=T0)
    assert clock.now() == T0
    assert clock.is_market_open() is True


def test_advance_moves_time_forward():
    clock = FakeClock(current=T0)
    clock.advance(90)
    assert clock.now() == T0 + timedelta(seconds=90)


def test_market_open_can_be_toggled():
    clock = FakeClock(current=T0)
    clock.set_market_open(False)
    assert clock.is_market_open() is False
    clock.set_market_open(True)
    assert clock.is_market_open() is True


def test_advance_accepts_fractional_seconds():
    clock = FakeClock(current=T0)
    clock.advance(0.5)
    assert clock.now() == T0 + timedelta(milliseconds=500)
