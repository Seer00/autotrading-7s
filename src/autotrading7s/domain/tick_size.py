"""호가 단위(tick size) 정규화 — 설계서 3.2절.

한국거래소에는 가격 구간별 호가 단위가 있어 유효 호가가 아닌 가격으로
주문하면 거부된다. 구간표는 2023년 KRX 호가 단위 개편 기준이며, 설계서
18.2절에 따라 구현 0단계에서 현행 값과 코스피·코스닥 차이를 재확인해야 한다.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from autotrading7s.domain.types import Side

# (상한(배타), 호가 단위) — 오름차순
_TICK_TABLE: tuple[tuple[int, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
_TICK_ABOVE_TABLE = 1_000


def tick_unit(price: int) -> int:
    """``price`` 가 속한 구간의 호가 단위."""
    if price <= 0:
        raise ValueError(f"price must be positive: {price}")
    for upper, unit in _TICK_TABLE:
        if price < upper:
            return unit
    return _TICK_ABOVE_TABLE


def normalize_tick(raw: Decimal | int, side: Side) -> int:
    """유효 호가로 정규화한다.

    BUY  → 내림. 발동가는 판정 기준선이므로 유효 호가 이하로 맞춘다.
    SELL → 올림. 내림하면 설정한 목표수익률에 미달한 채로 팔린다.

    구간 경계는 다음 구간 단위의 배수이므로(예: 20,000 은 50의 배수) 올림이
    경계를 넘어도 결과는 항상 유효 호가다.
    """
    if isinstance(raw, float):
        raise TypeError(
            "float 은 금액 계산에서 금지한다 — Decimal 또는 int 를 쓸 것 (설계서 3.1절)"
        )
    value = Decimal(raw)
    if value <= 0:
        raise ValueError(f"price must be positive: {value}")

    unit = tick_unit(int(value))
    rounding = ROUND_FLOOR if side is Side.BUY else ROUND_CEILING
    quotient = (value / unit).to_integral_value(rounding=rounding)
    return int(quotient) * unit
