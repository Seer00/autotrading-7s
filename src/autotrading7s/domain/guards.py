"""안전장치 — 설계서 6절.

설계서 7절 2항의 "무한 물타기 리스크"에 대한 코드 레벨 대응이다. 세븐스플릿은
손절매를 하지 않으므로, 프로그램이 제한할 수 있는 것은 투입 총액뿐이다.

한도는 **실체결금액 누적** 기준이다. 계획금액으로 세면 floor(금액/가격) 오차
때문에 한도가 실제보다 헐거워진다(설계서 3.1절).
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.rules import BuyStage, SellStage


@dataclass(frozen=True, slots=True)
class GuardContext:
    stock_invested: int          # 이 종목의 누적 실체결금액
    stock_limit: int             # 종목별 한도
    total_invested: int          # 전 종목 누적 실체결금액
    total_limit: int             # 전체 한도
    orders_last_minute: int
    max_orders_per_minute: int = 10

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field.name} must be int, not {type(value).__name__}")
            if value < 0:
                raise DomainInvariantError(f"{field.name} must be non-negative: {value}")


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    allowed: bool
    reason: str


def check_buy(decision: BuyStage, ctx: GuardContext) -> GuardVerdict:
    """매수 허용 여부.

    예상 체결금액은 지정가 × 수량으로 계산한다. 실제 체결가는 지정가 이하이므로
    이 추정은 보수적이다 — 한도를 넘길 위험이 없는 쪽으로 어긋난다.
    """
    if ctx.orders_last_minute >= ctx.max_orders_per_minute:
        return GuardVerdict(
            False,
            f"주문 빈도 제한 초과: {ctx.orders_last_minute}/"
            f"{ctx.max_orders_per_minute}건/분",
        )

    estimate = decision.limit_price * decision.qty

    if ctx.stock_invested + estimate > ctx.stock_limit:
        return GuardVerdict(
            False,
            f"종목 총한도 초과: 누적 {ctx.stock_invested:,} + 예상 {estimate:,} "
            f"> 한도 {ctx.stock_limit:,}",
        )

    if ctx.total_invested + estimate > ctx.total_limit:
        return GuardVerdict(
            False,
            f"전체 총한도 초과: 누적 {ctx.total_invested:,} + 예상 {estimate:,} "
            f"> 한도 {ctx.total_limit:,}",
        )

    return GuardVerdict(
        True,
        f"guard_ok stage={decision.stage_no} est={estimate:,} "
        f"stock={ctx.stock_invested:,}/{ctx.stock_limit:,} "
        f"total={ctx.total_invested:,}/{ctx.total_limit:,}",
    )


def check_sell(decision: SellStage, ctx: GuardContext) -> GuardVerdict:
    """매도 허용 여부. 포지션을 줄이는 방향이므로 투입 한도와 무관하다."""
    if ctx.orders_last_minute >= ctx.max_orders_per_minute:
        return GuardVerdict(
            False,
            f"주문 빈도 제한 초과: {ctx.orders_last_minute}/"
            f"{ctx.max_orders_per_minute}건/분",
        )
    return GuardVerdict(
        True, f"guard_ok stage={decision.stage_no} SELL qty={decision.qty}"
    )
