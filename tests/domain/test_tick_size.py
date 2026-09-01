from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.tick_size import normalize_tick, tick_unit
from autotrading7s.domain.types import Side


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (1, 1), (1_999, 1),
        (2_000, 5), (4_999, 5),
        (5_000, 10), (9_340, 10), (19_999, 10),
        (20_000, 50), (49_999, 50),
        (50_000, 100), (161_200, 100), (199_999, 100),
        (200_000, 500), (499_999, 500),
        (500_000, 1_000), (1_000_000, 1_000),
    ],
)
def test_tick_unit_boundaries(price: int, expected: int):
    assert tick_unit(price) == expected


def test_tick_unit_rejects_nonpositive():
    with pytest.raises(ValueError):
        tick_unit(0)
    with pytest.raises(ValueError):
        tick_unit(-100)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (9_340, 9_340),          # 이미 유효 호가
        (8_873, 8_870),          # 설계서 3.1절 2단계
        (8_406, 8_400),          # 3단계
        (7_939, 7_930),          # 4단계
        (7_472, 7_470),          # 5단계
        (7_005, 7_000),          # 6단계
        (6_538, 6_530),          # 7단계
    ],
)
def test_normalize_buy_floors(raw: int, expected: int):
    """매수 발동가는 내림 — 설계서 3.2절."""
    assert normalize_tick(Decimal(raw), Side.BUY) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("9807"), 9_810),     # 9,340 × 1.05
        (Decimal("9954"), 9_960),     # 9,480 × 1.05
        (Decimal("9397.5"), 9_400),   # 8,950 × 1.05
        (Decimal("10500"), 10_500),   # 이미 유효 호가면 그대로
    ],
)
def test_normalize_sell_ceils(raw: Decimal, expected: int):
    """목표 매도가는 올림 — 목표수익률 미달 방지. 설계서 3.2절."""
    assert normalize_tick(raw, Side.SELL) == expected


def test_normalize_sell_crossing_unit_boundary_stays_valid():
    """올림이 구간 경계를 넘어도 결과는 유효 호가여야 한다."""
    assert normalize_tick(Decimal("19998"), Side.SELL) == 20_000
    assert normalize_tick(Decimal("4999"), Side.SELL) == 5_000


def test_normalize_rejects_float():
    """설계서 3.1절: float 은 금액 계산에서 금지한다."""
    with pytest.raises(TypeError):
        normalize_tick(9340.5, Side.BUY)  # type: ignore[arg-type]


def test_normalize_rejects_nonpositive():
    with pytest.raises(ValueError):
        normalize_tick(Decimal(0), Side.BUY)
    with pytest.raises(ValueError):
        normalize_tick(Decimal(-1), Side.SELL)
