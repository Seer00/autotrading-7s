"""단계 상태기계 — 설계서 4.1절.

BUY_PENDING / SELL_PENDING 이라는 중간 상태가 중복 주문을 막는 유일한
방어선이다. WebSocket 시세는 초당 수십 틱이 오므로, 주문을 보내고 응답을
기다리는 동안 상태가 WAITING 으로 남아 있으면 그 틱마다 새 주문이 나간다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from autotrading7s.domain.types import StageStatus


class IllegalStageTransition(RuntimeError):
    """전이표가 허용하지 않는 상태 전이."""


# 설계서 4.1절 전이도.
_ALLOWED: dict[StageStatus, frozenset[StageStatus]] = {
    StageStatus.WAITING: frozenset({StageStatus.BUY_PENDING}),
    StageStatus.BUY_PENDING: frozenset({StageStatus.HOLDING, StageStatus.WAITING}),
    StageStatus.HOLDING: frozenset({StageStatus.SELL_PENDING}),
    # SELL_PENDING → HOLDING 은 매도 주문이 체결 없이 취소된 경우다.
    # 설계서 4.1절 전이도에 명시되지 않았으나 미체결 취소 처리에 필요하다.
    StageStatus.SELL_PENDING: frozenset(
        {StageStatus.HOLDING, StageStatus.WAITING, StageStatus.SOLD}
    ),
    StageStatus.SOLD: frozenset(),
}


@dataclass(frozen=True, slots=True)
class StageState:
    stage_no: int
    status: StageStatus
    trigger_price: int
    planned_qty: int
    fill_price: int | None = None
    fill_qty: int | None = None
    bought_at: datetime | None = None
    last_sold_at: datetime | None = None
    rebuy_count: int = 0

    @property
    def held_qty(self) -> int:
        """실제 보유 수량. PENDING 매수 중에는 아직 0이다."""
        if self.status in (StageStatus.HOLDING, StageStatus.SELL_PENDING):
            return self.fill_qty or 0
        return 0


def _guard(state: StageState, to: StageStatus) -> None:
    if to not in _ALLOWED[state.status]:
        raise IllegalStageTransition(
            f"stage {state.stage_no}: {state.status.value} → {to.value} 는 허용되지 않음"
        )


def to_buy_pending(state: StageState) -> StageState:
    _guard(state, StageStatus.BUY_PENDING)
    return replace(state, status=StageStatus.BUY_PENDING)


def to_holding(
    state: StageState, *, fill_price: int, fill_qty: int, at: datetime
) -> StageState:
    """매수 체결 반영.

    부분체결이면 ``fill_qty`` 가 ``planned_qty`` 보다 작다. 설계서 4.1절에 따라
    체결 수량만으로 확정하며 잔량을 쫓지 않는다.
    """
    _guard(state, StageStatus.HOLDING)
    if fill_price <= 0 or fill_qty <= 0:
        raise ValueError(f"invalid fill: price={fill_price} qty={fill_qty}")
    return replace(
        state,
        status=StageStatus.HOLDING,
        fill_price=fill_price,
        fill_qty=fill_qty,
        bought_at=at,
    )


def to_sell_pending(state: StageState) -> StageState:
    _guard(state, StageStatus.SELL_PENDING)
    return replace(state, status=StageStatus.SELL_PENDING)


def after_sell(state: StageState, *, at: datetime, allow_rebuy: bool) -> StageState:
    """매도 전량 체결 반영.

    ``allow_rebuy`` 면 WAITING 으로 복귀하여 같은 발동가에서 재매수 대상이 되고,
    아니면 SOLD 로 종료된다. 발동가는 사다리에 고정되어 있어 변하지 않는다.
    """
    target = StageStatus.WAITING if allow_rebuy else StageStatus.SOLD
    _guard(state, target)
    return replace(
        state,
        status=target,
        fill_price=None,
        fill_qty=None,
        bought_at=None,
        last_sold_at=at,
        rebuy_count=state.rebuy_count + (1 if allow_rebuy else 0),
    )


def cancel_buy(state: StageState) -> StageState:
    """매수 주문 미체결 취소 → 대기 복귀. 다음 틱에 재시도된다."""
    _guard(state, StageStatus.WAITING)
    return replace(state, status=StageStatus.WAITING)


def cancel_sell(state: StageState) -> StageState:
    """매도 주문이 체결 없이 취소됨 → 보유 복귀."""
    _guard(state, StageStatus.HOLDING)
    return replace(state, status=StageStatus.HOLDING)


def force_sold(state: StageState, *, at: datetime) -> StageState:
    """긴급청산 전용 — 전이표를 우회한다.

    설계서 11.1절은 긴급청산을 Trigger Engine 을 거치지 않는 별도 경로로
    규정한다. 이 함수는 그 설계를 코드에 반영한 것이며, 일반 전이 경로에서는
    절대 호출하지 않는다. 이미 SOLD 인 단계에 대해 멱등하다.
    """
    return replace(
        state,
        status=StageStatus.SOLD,
        fill_price=None,
        fill_qty=None,
        bought_at=None,
        last_sold_at=at,
    )
