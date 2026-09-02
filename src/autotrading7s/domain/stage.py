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

    def __post_init__(self) -> None:
        """단계의 정체성 필드와 (보유 상태라면) 체결 필드의 불변식.

        이 검증은 두 계층으로 이루어진다:
        1. to_holding() 은 전이 문맥에서 유효성을 검사한다 (전이 실패 메시지)
        2. __post_init__() 은 타입의 불변식을 강제한다 (타입 안정성)
        두 계층의 중복은 의도적이며, dataclasses.replace() 호출이 __init__을 거치므로
        모든 전이도 이 검증을 통과한다.

        Plan 2에서 SQLite 행 재구성 시 부분 정보(예: fill_qty NULL)나 손상된
        값(단계 0, 음수 발동가·수량)은 정확히 이 불변식을 위반한다. 손상된
        발동가는 트리거 비교를, 손상된 수량은 금액 산술을 조용히 뒤집으므로
        경계에서의 보호가 필수다.
        """
        self._check_int_field("stage_no", self.stage_no, minimum=1,
                              phrase="positive")
        self._check_int_field("trigger_price", self.trigger_price, minimum=1,
                              phrase="positive")
        self._check_int_field("planned_qty", self.planned_qty, minimum=0,
                              phrase="non-negative")
        self._check_int_field("rebuy_count", self.rebuy_count, minimum=0,
                              phrase="non-negative")
        if self.status in (StageStatus.HOLDING, StageStatus.SELL_PENDING):
            self._check_fill_field("fill_price", self.fill_price)
            self._check_fill_field("fill_qty", self.fill_qty)

    @staticmethod
    def _check_int_field(
        name: str, value: object, *, minimum: int, phrase: str
    ) -> None:
        """정체성 필드 공통 검증: 타입 → 하한, 이 순서로.

        타입 검사가 먼저인 이유는 `_check_fill_field` 와 같다 — float·bool·
        Decimal 은 모두 크기 비교를 통과하므로, 타입을 확인하지 않으면 실수
        발동가나 수량이 조용히 트리거 판정과 금액 산술로 흘러간다.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be int, not {type(value).__name__}")
        if value < minimum:
            raise ValueError(f"{name} must be {phrase}: {value}")

    def _check_fill_field(self, name: str, value: int | None) -> None:
        """`fill_price`/`fill_qty` 공통 검증: 존재 → 타입 → 양수, 이 순서로.

        타입 검사가 양수 비교보다 먼저 와야 한다 — float·bool·Decimal 은
        모두 ``<= 0`` 비교를 통과하므로, 타입을 확인하지 않으면 실수
        수량(예: 50.5)이 조용히 ``fill_qty`` 로 들어가 이후
        ``invested_amount`` 등 금액 계산까지 float 로 오염시킨다
        (설계서 3.1절 — float 금지).
        """
        if value is None:
            raise ValueError(
                f"status {self.status.value}: {name} must be positive, got {value}"
            )
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be int, not {type(value).__name__}")
        if value <= 0:
            raise ValueError(
                f"status {self.status.value}: {name} must be positive, got {value}"
            )


def _guard(state: StageState, to: StageStatus) -> None:
    if to not in _ALLOWED[state.status]:
        raise IllegalStageTransition(
            f"stage {state.stage_no}: {state.status.value} → {to.value} 는 허용되지 않음"
        )


def _require_source(state: StageState, expected: StageStatus, fn_name: str) -> None:
    """도우미가 노리는 출발 상태와 실제 출발 상태가 맞는지 확인한다.

    ``_guard`` 는 "이 목표 상태가 표에 있는가"를 묻는다. 이 함수는 그와
    다른 질문 — "이 도우미가 이 출발 상태에 맞는 도우미인가" — 를 묻는다.
    ``_ALLOWED`` 는 여러 출발 상태가 같은 목표에 도달하는 것을 허용한다
    (예: HOLDING ← BUY_PENDING, SELL_PENDING). 도우미 하나는 그중 정확히
    하나의 전이만 의미하므로, 표만으로는 잘못된 도우미가 잘못된 출발
    상태에서 호출되는 것을 막지 못한다 — 예를 들어 ``to_holding`` 을
    SELL_PENDING 에 호출하면 표는 통과시키지만 그 기록을 조용히 덮어쓴다.
    이 검사를 ``_guard`` 보다 먼저 실행해야, 잘못 걸린 도우미가 그 사실을
    보고하고 필드를 건드리기 전에 멈춘다.
    """
    if state.status is not expected:
        raise IllegalStageTransition(
            f"stage {state.stage_no}: {fn_name}() 는 {expected.value} 에서만 "
            f"호출할 수 있음, 실제 상태는 {state.status.value}"
        )


def to_buy_pending(state: StageState) -> StageState:
    _require_source(state, StageStatus.WAITING, "to_buy_pending")
    _guard(state, StageStatus.BUY_PENDING)
    return replace(state, status=StageStatus.BUY_PENDING)


def to_holding(
    state: StageState, *, fill_price: int, fill_qty: int, at: datetime
) -> StageState:
    """매수 체결 반영.

    부분체결이면 ``fill_qty`` 가 ``planned_qty`` 보다 작다. 설계서 4.1절에 따라
    체결 수량만으로 확정하며 잔량을 쫓지 않는다.
    """
    _require_source(state, StageStatus.BUY_PENDING, "to_holding")
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
    _require_source(state, StageStatus.HOLDING, "to_sell_pending")
    _guard(state, StageStatus.SELL_PENDING)
    return replace(state, status=StageStatus.SELL_PENDING)


def after_sell(state: StageState, *, at: datetime, allow_rebuy: bool) -> StageState:
    """매도 전량 체결 반영.

    ``allow_rebuy`` 면 WAITING 으로 복귀하여 같은 발동가에서 재매수 대상이 되고,
    아니면 SOLD 로 종료된다. 발동가는 사다리에 고정되어 있어 변하지 않는다.
    """
    _require_source(state, StageStatus.SELL_PENDING, "after_sell")
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
    _require_source(state, StageStatus.BUY_PENDING, "cancel_buy")
    _guard(state, StageStatus.WAITING)
    return replace(state, status=StageStatus.WAITING)


def cancel_sell(state: StageState, *, remaining_qty: int) -> StageState:
    """매도 주문이 취소되어 보유로 되돌아간다.

    호출자가 남은 수량을 알려준다. 한국 주식 주문은 당일에만 유효하므로,
    부분체결된 매도 주문의 미체결 잔량이 마감과 함께 취소되면 처음
    보유했던 수량보다 적은 채로 HOLDING 에 복귀하는 것이 일상적인 경로다
    (설계서 9절). 전혀 체결되지 않은 취소(``remaining_qty ==
    state.fill_qty``)는 그 특수 케이스일 뿐, 별도 분기가 아니다.
    """
    _require_source(state, StageStatus.SELL_PENDING, "cancel_sell")
    _guard(state, StageStatus.HOLDING)
    # remaining_qty 를 여기서도 검사한다. StageState.__post_init__ 이 최종
    # 방어선이지만, 그 예외는 fill_qty 라는 이름으로 나기 때문에 호출자가
    # remaining_qty 를 잘못 넘겼다는 것을 알 수 없다. to_holding 과
    # __post_init__ 사이의 관계와 같은 의도된 이중 방어이며, 나중에 이
    # 검사를 "중복이니 지운다"고 지우면 안 된다.
    if isinstance(remaining_qty, bool) or not isinstance(remaining_qty, int):
        raise TypeError(
            f"remaining_qty must be int, not {type(remaining_qty).__name__}"
        )
    if not 0 < remaining_qty <= state.fill_qty:
        raise ValueError(
            f"remaining_qty must be in (0, fill_qty]: "
            f"remaining_qty={remaining_qty}, fill_qty={state.fill_qty}"
        )
    return replace(state, status=StageStatus.HOLDING, fill_qty=remaining_qty)


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
