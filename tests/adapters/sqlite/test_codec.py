from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.codec import (
    bool_to_int,
    dt_to_text,
    int_to_bool,
    ratio_to_text,
    text_to_dt,
    text_to_ratio,
)
from autotrading7s.domain.errors import DomainInvariantError

KST = timezone(timedelta(hours=9))


@pytest.mark.parametrize(
    "value",
    [Decimal("0.05"), Decimal("0.1666"), Decimal("0.25"), Decimal("0.5"),
     Decimal("0.0001")],
)
def test_ratio_round_trip_is_exact(value: Decimal):
    """Decimal("0.05") 가 Decimal("0.0500") 이 되면 target_pct 비교가 어긋난다."""
    assert text_to_ratio(ratio_to_text(value)) == value
    assert str(text_to_ratio(ratio_to_text(value))) == str(value)


def test_ratio_text_is_not_scientific_notation():
    """지수 표기가 되면 사람이 DB 를 읽을 때 혼란스럽고 비교도 흔들린다."""
    assert "E" not in ratio_to_text(Decimal("0.0001")).upper()


def test_ratio_rejects_float():
    with pytest.raises(TypeError):
        ratio_to_text(0.05)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 9, 30, 15, 123456, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 18, 30, tzinfo=KST),
    ],
)
def test_datetime_round_trip_preserves_the_instant(value: datetime):
    assert text_to_dt(dt_to_text(value)) == value


def test_datetime_round_trip_preserves_awareness():
    value = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    restored = text_to_dt(dt_to_text(value))
    assert restored.tzinfo is not None
    assert restored.tzinfo.utcoffset(restored) is not None


def test_writing_a_naive_datetime_is_refused():
    """도메인의 모든 datetime 은 tz-aware 여야 한다 — 쓰는 쪽에서도 막는다."""
    with pytest.raises(DomainInvariantError, match="timezone-aware"):
        dt_to_text(datetime(2026, 9, 1, 9, 30))


def test_reading_a_naive_text_is_refused():
    """H2 의 핵심. 오프셋 없는 TEXT 는 naive datetime 을 만들고, 그것이 엔진 틱
    루프 안에서 TypeError 를 낸다 — 읽는 쪽에서 애초에 만들지 않는다."""
    with pytest.raises(DomainInvariantError, match="timezone-aware"):
        text_to_dt("2026-09-01T09:30:00")


def test_reading_garbage_is_refused():
    with pytest.raises(DomainInvariantError):
        text_to_dt("not a timestamp")


def test_kst_and_utc_texts_compare_as_the_same_instant():
    """저장 시각대가 달라도 같은 순간이면 같아야 한다 — 쿨다운 산술의 전제."""
    utc = text_to_dt("2026-09-01T09:30:00+00:00")
    kst = text_to_dt("2026-09-01T18:30:00+09:00")
    assert utc == kst
    assert (utc - kst).total_seconds() == 0


@pytest.mark.parametrize(("value", "expected"), [(True, 1), (False, 0)])
def test_bool_round_trip(value: bool, expected: int):
    assert bool_to_int(value) == expected
    assert int_to_bool(bool_to_int(value)) is value


def test_bool_to_int_rejects_non_bool():
    """allow_rebuy 가 진리값 해석으로 켜지는 것을 Plan 1 이 막았다 — 여기서도 막는다."""
    with pytest.raises(TypeError):
        bool_to_int(1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [2, -1])
def test_int_to_bool_rejects_values_outside_zero_and_one(value: int):
    with pytest.raises(DomainInvariantError):
        int_to_bool(value)


# Fix Round 1: Finding 1 — text_to_ratio must guard against non-string input.
def test_text_to_ratio_rejects_non_string():
    """Symmetric to ratio_to_text rejecting non-Decimal. Prevents float noise entry."""
    with pytest.raises(TypeError):
        text_to_ratio(0.05)  # type: ignore[arg-type]


# Fix Round 1: Finding 3 — Non-finite values must be rejected in both directions.
def test_ratio_to_text_rejects_nan():
    """NaN ratios would cause silent failures in domain calculations."""
    with pytest.raises(DomainInvariantError, match="finite"):
        ratio_to_text(Decimal("NaN"))


def test_ratio_to_text_rejects_infinity():
    """Infinity ratios would cause silent failures in domain calculations."""
    with pytest.raises(DomainInvariantError, match="finite"):
        ratio_to_text(Decimal("Infinity"))


def test_text_to_ratio_rejects_nan():
    """Prevents NaN from entering domain via database."""
    with pytest.raises(DomainInvariantError, match="finite"):
        text_to_ratio("NaN")


def test_text_to_ratio_rejects_infinity():
    """Prevents Infinity from entering domain via database."""
    with pytest.raises(DomainInvariantError, match="finite"):
        text_to_ratio("Infinity")


# Fix Round 1: Finding 2 — text_to_dt must not conflate TypeError with ValueError.
def test_text_to_dt_rejects_none_with_type_error():
    """NULL from database should raise TypeError, not be wrapped as row corruption.

    This is the realistic case: a nullable TEXT column read from SQLite yields None.
    A mapping function that forgot a NULL check is a caller bug, not row corruption.
    """
    with pytest.raises(TypeError):
        text_to_dt(None)  # type: ignore[arg-type]


def test_text_to_dt_rejects_int_with_type_error():
    """Non-string type should raise TypeError, not DomainInvariantError."""
    with pytest.raises(TypeError):
        text_to_dt(123)  # type: ignore[arg-type]


# Fix Round 1: Bonus Minor — Pin literal TEXT forms, not just round-trip.
def test_ratio_to_text_literal_form():
    """Round-trip test passes under several wrong serializations.
    Pinning the string ensures the storage format is correct."""
    assert ratio_to_text(Decimal("0.05")) == "0.05"
    assert ratio_to_text(Decimal("0.1666")) == "0.1666"
    assert ratio_to_text(Decimal("0.0001")) == "0.0001"


def test_dt_to_text_literal_form():
    """Pinning the ISO 8601 format ensures storage consistency."""
    value = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    text = dt_to_text(value)
    assert text == "2026-09-01T09:30:00+00:00"

    value_with_microseconds = datetime(
        2026, 9, 1, 9, 30, 15, 123456, tzinfo=timezone.utc
    )
    text_with_microseconds = dt_to_text(value_with_microseconds)
    assert text_with_microseconds == "2026-09-01T09:30:15.123456+00:00"

    value_kst = datetime(2026, 9, 1, 18, 30, tzinfo=KST)
    text_kst = dt_to_text(value_kst)
    assert text_kst == "2026-09-01T18:30:00+09:00"
