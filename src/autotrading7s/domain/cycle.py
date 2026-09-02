"""사이클 상태기계 — 설계서 4.2절.

STARTING 은 앵커가 아직 없는 구간이다. 사다리를 계산할 수 없으므로 트리거
판정을 전혀 하지 않는다. 1단계가 체결되어 앵커가 확정되는 순간 RUNNING 으로
전이하고 사다리가 사이클에 박제된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus


class IllegalCycleTransition(RuntimeError):
    """전이표가 허용하지 않는 사이클 상태 전이."""


_ALLOWED: dict[CycleStatus, frozenset[CycleStatus]] = {
    CycleStatus.IDLE: frozenset({CycleStatus.STARTING}),
    CycleStatus.STARTING: frozenset({CycleStatus.RUNNING, CycleStatus.IDLE, CycleStatus.LIQUIDATING}),
    CycleStatus.RUNNING: frozenset(
        {CycleStatus.PAUSED, CycleStatus.LIQUIDATING, CycleStatus.CLOSED}
    ),
    # PAUSED → CLOSED 는 대사 불일치로 정지된 뒤 외부에서 수동 전량 매도된
    # 종목을 정리하는 경로다(설계서 10.2절).
    CycleStatus.PAUSED: frozenset(
        {CycleStatus.RUNNING, CycleStatus.LIQUIDATING, CycleStatus.CLOSED}
    ),
    CycleStatus.LIQUIDATING: frozenset({CycleStatus.CLOSED}),
    CycleStatus.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Cycle:
    cycle_id: int
    config_id: int
    seq: int
    status: CycleStatus
    anchor_price: int | None = None
    ladder: Ladder | None = None
    close_reason: CloseReason | None = None
    started_at: datetime | None = None
    closed_at: datetime | None = None
    # D20 강제 종료의 증언과 잔량 (설계서 11.4절). 스키마의 CHECK 와 같은
    # 불변식을 __post_init__ 이 도메인에서도 말한다 — 두 층이 같은 것을 말하면
    # 어긋날 수 없고, 한 층만 말하면 다른 경로로 들어온 값이 통과한다.
    forced_close_reason: str | None = None
    forced_close_qty: int | None = None

    def __post_init__(self) -> None:
        for name in ("cycle_id", "config_id", "seq"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be int, not {type(value).__name__}")
            if value <= 0:
                raise DomainInvariantError(f"{name} must be positive: {value}")

        # RUNNING·PAUSED 는 앵커와 사다리가 반드시 있어야 한다. 트리거 판정을
        # 하거나(RUNNING) 판정을 재개할 수 있는(PAUSED) 상태이므로 사다리 없이는
        # 성립하지 않는다. LIQUIDATING 은 요구하지 않는다 — 긴급청산은 앵커가
        # 생기기 전인 STARTING 에서도 시작할 수 있다(설계서 11.1절).
        requires_ladder = self.status in (CycleStatus.RUNNING, CycleStatus.PAUSED)
        if requires_ladder and self.anchor_price is None:
            raise DomainInvariantError(
                f"Cycle status {self.status.value} requires anchor_price, got None"
            )
        # 앵커가 있으면 사다리도 있어야 한다. 앵커는 사다리 확정과 같은 순간에
        # 생기므로(confirm_anchor), 앵커만 있는 행은 어느 상태에서든 손상이다.
        if self.ladder is None and (requires_ladder or self.anchor_price is not None):
            raise DomainInvariantError(
                f"Cycle status {self.status.value} requires ladder, got None"
            )
        # 거울상: 사다리만 있고 앵커가 없는 행도 손상이다. 위 검사와 같은
        # 이유이며(둘은 confirm_anchor 에서 같은 순간에 생긴다), RUNNING·
        # PAUSED 는 첫 검사가 이미 앵커를 요구하므로 여기 닿지 않는다.
        if self.anchor_price is None and self.ladder is not None:
            raise DomainInvariantError(
                f"Cycle status {self.status.value} with ladder "
                "requires anchor_price, got None"
            )
        # 두 값이 모두 있으면 반드시 같은 앵커를 가리켜야 한다 — 상태와
        # 무관하다. 사다리는 앵커에서 전부 파생되므로 두 값이 다르면 어느 쪽이
        # 진실인지 알 방법이 없다.
        if self.anchor_price is not None and self.ladder is not None:
            if self.anchor_price != self.ladder.anchor_price:
                raise DomainInvariantError(
                    f"anchor_price {self.anchor_price} != "
                    f"ladder.anchor_price {self.ladder.anchor_price}"
                )

        # D20 (설계서 11.4절) — 스키마의 CHECK 와 같은 불변식이다. 두 층이 같은
        # 것을 말하면 어긋날 수 없고, 한 층만 말하면 다른 경로로 들어온 값이
        # 통과한다. FORCED 인 종료는 증언과 잔량이 둘 다 있어야 하고, FORCED 가
        # 아닌 종료는 둘 다 없어야 한다.
        forced = self.close_reason is CloseReason.FORCED
        has_fields = (self.forced_close_reason is not None
                      and self.forced_close_qty is not None)
        if forced != has_fields:
            raise DomainInvariantError(
                f"close_reason FORCED and forced_close_* must agree: "
                f"close_reason={self.close_reason}, "
                f"forced_close_reason={self.forced_close_reason!r}, "
                f"forced_close_qty={self.forced_close_qty!r}"
            )
        if self.forced_close_qty is not None:
            if (isinstance(self.forced_close_qty, bool)
                    or not isinstance(self.forced_close_qty, int)):
                raise TypeError(
                    f"forced_close_qty must be int, not "
                    f"{type(self.forced_close_qty).__name__}"
                )
            if self.forced_close_qty <= 0:
                raise DomainInvariantError(
                    f"forced_close_qty must be positive: {self.forced_close_qty}"
                )

    @property
    def is_active(self) -> bool:
        return self.status in (CycleStatus.STARTING, CycleStatus.RUNNING)

    @property
    def accepts_triggers(self) -> bool:
        """트리거 판정을 수행해도 되는 상태인가.

        RUNNING 만 허용한다. STARTING 은 앵커가 없어 사다리를 계산할 수 없고,
        PAUSED·LIQUIDATING 은 자동 트리거가 정지된 상태다.
        """
        return self.status is CycleStatus.RUNNING


def _guard(cycle: Cycle, to: CycleStatus) -> None:
    if to not in _ALLOWED[cycle.status]:
        raise IllegalCycleTransition(
            f"cycle {cycle.cycle_id}: {cycle.status.value} → {to.value} 는 허용되지 않음"
        )


def start(cycle: Cycle, *, at: datetime) -> Cycle:
    """사용자가 [시작]을 눌렀다. 1단계 주문을 내기 전 상태."""
    _guard(cycle, CycleStatus.STARTING)
    return replace(cycle, status=CycleStatus.STARTING, started_at=at)


def confirm_anchor(
    cycle: Cycle, *, anchor_price: int, ladder: Ladder, at: datetime
) -> Cycle:
    """1단계가 체결되어 앵커가 확정됐다. 사다리를 사이클에 고정한다."""
    _guard(cycle, CycleStatus.RUNNING)
    if anchor_price != ladder.anchor_price:
        # Cycle.__post_init__ 은 동일한 조건(앵커·사다리 불일치)을
        # DomainInvariantError 로 낸다 — 여기서도 같은 예외 타입을 써서
        # 도메인 불변식 위반이 항상 같은 방식으로 드러나게 한다.
        raise DomainInvariantError(
            f"anchor mismatch: {anchor_price} != ladder {ladder.anchor_price}"
        )
    return replace(
        cycle,
        status=CycleStatus.RUNNING,
        anchor_price=anchor_price,
        ladder=ladder,
        started_at=cycle.started_at or at,
    )


def abort_start(cycle: Cycle) -> Cycle:
    """1단계 주문이 미체결·거부되어 사이클이 성립하지 않았다."""
    _guard(cycle, CycleStatus.IDLE)
    return replace(cycle, status=CycleStatus.IDLE, started_at=None)


def pause(cycle: Cycle) -> Cycle:
    """자동 트리거 정지, 보유는 유지 (설계서 D11)."""
    _guard(cycle, CycleStatus.PAUSED)
    return replace(cycle, status=CycleStatus.PAUSED)


def resume(cycle: Cycle) -> Cycle:
    _guard(cycle, CycleStatus.RUNNING)
    return replace(cycle, status=CycleStatus.RUNNING)


def begin_liquidation(cycle: Cycle) -> Cycle:
    """긴급청산 시작. 자동 트리거가 즉시 정지된다 (설계서 11.1절 ①)."""
    _guard(cycle, CycleStatus.LIQUIDATING)
    return replace(cycle, status=CycleStatus.LIQUIDATING)


def close(
    cycle: Cycle, *, reason: CloseReason, at: datetime, states: Sequence[StageState]
) -> Cycle:
    """사이클을 종료 상태로 전이. 실제로 종료 조건을 만족했는지 확인한다.

    단계 목록을 요구하는 이유는 상태 전이만으로는 종료를 판단할 수 없기
    때문이다 — 보유 주식이나 진행 중인 주문이 남은 채 CLOSED 로 넘어가면
    그 포지션을 추적하는 주체가 사라진다.
    """
    _guard(cycle, CycleStatus.CLOSED)
    if not is_cycle_complete(states):
        # PENDING 주문과 보유 주식은 원인이 다르므로 메시지를 구분한다.
        pending = (StageStatus.BUY_PENDING, StageStatus.SELL_PENDING)
        pending_stages = [s.stage_no for s in states if s.status in pending]
        if pending_stages:
            raise ValueError(
                f"cannot close cycle — pending orders on stages: {pending_stages}"
            )
        held = sum(s.held_qty for s in states)
        raise ValueError(
            f"cannot close cycle with {held} shares still held — not all stages complete"
        )
    return replace(cycle, status=CycleStatus.CLOSED, close_reason=reason, closed_at=at)


def force_close(cycle: Cycle, *, reason: str, qty: int, at: datetime) -> Cycle:
    """D20 강제 종료 — 설계서 11.4절.

    `close()` 의 우회가 아니라 별도 경로다. `close()` 는 보유 0 을 확인하고,
    이 함수는 **보유가 남았다는 사실 자체를 기록한다.** 그래서 단계 목록을
    요구하지 않으며 `is_cycle_complete` 를 부르지 않는다.

    `LIQUIDATING` 에서만 호출할 수 있다. 사용자가 먼저 긴급청산을 시도해야
    하며, 그 시도 이력(횟수·시각·실패 사유)이 강제 종료 다이얼로그의 근거가
    된다. `RUNNING` 에서 바로 강제 종료하는 경로를 두면 그 근거 없이 내부
    기록과 실계좌를 어긋나게 만들 수 있다.

    설계서 10.2절이 금지하는 것과 구분된다 — 10.2절이 금지하는 것은
    **프로그램이** 불일치를 조용히 만드는 것이고, 이것은 사용자가 "잔량이
    얼마인지 알고 있으며 내가 처리한다" 고 명시적으로 증언하는 것이다.

    `_guard` 를 쓰지 않는 이유: 전이표는 `LIQUIDATING → CLOSED` 외에
    `RUNNING → CLOSED` 등도 허용하는데, 이 함수는 그보다 **좁은** 조건을
    강제하므로 직접 검사하는 것이 의도를 드러낸다.
    """
    if cycle.status is not CycleStatus.LIQUIDATING:
        raise IllegalCycleTransition(
            f"force_close requires LIQUIDATING, not {cycle.status.value} "
            f"(설계서 11.4절 — 긴급청산을 먼저 시도해야 한다)"
        )
    if isinstance(qty, bool) or not isinstance(qty, int):
        raise TypeError(f"qty must be int, not {type(qty).__name__}")
    if qty <= 0:
        raise DomainInvariantError(
            f"qty must be positive: {qty} — 잔량 0 의 강제 종료는 정상 close() "
            f"로 처리한다 (설계서 11.4절 절차 ③)"
        )
    if not reason or not reason.strip():
        raise DomainInvariantError("reason must be a non-empty statement")
    return replace(
        cycle, status=CycleStatus.CLOSED, close_reason=CloseReason.FORCED,
        closed_at=at, forced_close_reason=reason, forced_close_qty=qty,
    )


def is_cycle_complete(states: Sequence[StageState]) -> bool:
    """사이클 종료 조건 — 보유수량 0이고 진행 중인 주문도 없다.

    설계서 4.2절은 '보유수량 0 도달'을 종료 조건으로 규정한다. PENDING 주문이
    남아 있으면 곧 보유가 생길 수 있으므로 종료로 보지 않는다.

    빈 단계 리스트는 데이터 무결성 실패다 — 단계가 없는 사이클은 존재할 수
    없으므로 "종료됨"으로 답하지 않고 DomainInvariantError(ValueError 의
    하위)를 던진다.
    """
    if not states:
        raise DomainInvariantError("stage states sequence is empty — data integrity failure")
    pending = (StageStatus.BUY_PENDING, StageStatus.SELL_PENDING)
    if any(s.status in pending for s in states):
        return False
    return all(s.held_qty == 0 for s in states)
