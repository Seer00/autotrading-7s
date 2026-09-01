"""사이클 상태기계 — 설계서 4.2절.

STARTING 은 앵커가 아직 없는 구간이다. 사다리를 계산할 수 없으므로 트리거
판정을 전혀 하지 않는다. 1단계가 체결되어 앵커가 확정되는 순간 RUNNING 으로
전이하고 사다리가 사이클에 박제된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus


class IllegalCycleTransition(RuntimeError):
    """전이표가 허용하지 않는 사이클 상태 전이."""


_ALLOWED: dict[CycleStatus, frozenset[CycleStatus]] = {
    CycleStatus.IDLE: frozenset({CycleStatus.STARTING}),
    CycleStatus.STARTING: frozenset({CycleStatus.RUNNING, CycleStatus.IDLE}),
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
        raise ValueError(
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


def close(cycle: Cycle, *, reason: CloseReason, at: datetime) -> Cycle:
    _guard(cycle, CycleStatus.CLOSED)
    return replace(cycle, status=CycleStatus.CLOSED, close_reason=reason, closed_at=at)


def is_cycle_complete(states: Sequence[StageState]) -> bool:
    """사이클 종료 조건 — 보유수량 0이고 진행 중인 주문도 없다.

    설계서 4.2절은 '보유수량 0 도달'을 종료 조건으로 규정한다. PENDING 주문이
    남아 있으면 곧 보유가 생길 수 있으므로 종료로 보지 않는다.
    """
    pending = (StageStatus.BUY_PENDING, StageStatus.SELL_PENDING)
    if any(s.status in pending for s in states):
        return False
    return all(s.held_qty == 0 for s in states)
