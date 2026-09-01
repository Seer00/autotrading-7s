from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import (
    Cycle,
    IllegalCycleTransition,
    abort_start,
    begin_liquidation,
    close,
    confirm_anchor,
    is_cycle_complete,
    pause,
    resume,
    start,
)
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def idle() -> Cycle:
    return Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)


def ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running() -> Cycle:
    return confirm_anchor(start(idle(), at=T0), anchor_price=10_000,
                          ladder=ladder(), at=T0)


def test_starting_does_not_accept_triggers():
    """앵커가 없으면 사다리를 계산할 수 없다 — 설계서 4.2절."""
    cyc = start(idle(), at=T0)
    assert cyc.status is CycleStatus.STARTING
    assert cyc.anchor_price is None
    assert cyc.accepts_triggers is False
    assert cyc.is_active is True


def test_confirm_anchor_fixes_ladder_and_enables_triggers():
    cyc = running()
    assert cyc.status is CycleStatus.RUNNING
    assert cyc.anchor_price == 10_000
    assert cyc.ladder is not None
    assert cyc.accepts_triggers is True


def test_abort_start_returns_to_idle():
    """1단계 주문이 미체결·취소되면 사이클이 성립하지 않는다."""
    cyc = abort_start(start(idle(), at=T0))
    assert cyc.status is CycleStatus.IDLE
    assert cyc.anchor_price is None


@pytest.mark.parametrize(
    "status",
    [CycleStatus.IDLE, CycleStatus.STARTING, CycleStatus.PAUSED,
     CycleStatus.LIQUIDATING, CycleStatus.CLOSED],
)
def test_only_running_accepts_triggers(status: CycleStatus):
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status,
                anchor_price=10_000, ladder=ladder())
    assert cyc.accepts_triggers is False


def test_pause_and_resume():
    cyc = pause(running())
    assert cyc.status is CycleStatus.PAUSED
    assert cyc.accepts_triggers is False
    assert resume(cyc).status is CycleStatus.RUNNING


def test_liquidation_from_running_and_paused():
    assert begin_liquidation(running()).status is CycleStatus.LIQUIDATING
    assert begin_liquidation(pause(running())).status is CycleStatus.LIQUIDATING


def test_close_records_reason_and_time():
    cyc = close(running(), reason=CloseReason.NORMAL, at=T0)
    assert cyc.status is CycleStatus.CLOSED
    assert cyc.close_reason is CloseReason.NORMAL
    assert cyc.closed_at == T0


def test_close_from_liquidating_records_emergency():
    cyc = close(begin_liquidation(running()), reason=CloseReason.EMERGENCY, at=T0)
    assert cyc.close_reason is CloseReason.EMERGENCY


def test_paused_can_be_closed():
    """외부에서 수동 전량 매도된 종목은 PAUSED 에서 종료할 수 있어야 한다."""
    assert close(pause(running()), reason=CloseReason.NORMAL, at=T0).status \
        is CycleStatus.CLOSED


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (CycleStatus.IDLE, "pause"),
        (CycleStatus.IDLE, "resume"),
        (CycleStatus.IDLE, "begin_liquidation"),
        (CycleStatus.RUNNING, "start"),
        (CycleStatus.RUNNING, "resume"),
        (CycleStatus.LIQUIDATING, "pause"),
        (CycleStatus.LIQUIDATING, "resume"),
        (CycleStatus.CLOSED, "start"),
        (CycleStatus.CLOSED, "pause"),
        (CycleStatus.CLOSED, "begin_liquidation"),
    ],
)
def test_illegal_cycle_transitions(status: CycleStatus, action: str):
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status)
    fn = {
        "start": lambda c: start(c, at=T0),
        "pause": pause,
        "resume": resume,
        "begin_liquidation": begin_liquidation,
    }[action]
    with pytest.raises(IllegalCycleTransition):
        fn(cyc)


def test_confirm_anchor_only_from_starting():
    with pytest.raises(IllegalCycleTransition):
        confirm_anchor(idle(), anchor_price=10_000, ladder=ladder(), at=T0)


def _stage(no: int, status: StageStatus, qty: int | None = None) -> StageState:
    return StageState(stage_no=no, status=status, trigger_price=10_000 - no * 500,
                      planned_qty=100, fill_price=9_000 if qty else None, fill_qty=qty)


def test_is_cycle_complete_when_no_holdings():
    """설계서 4.2절: 보유수량 0 도달이 사이클 종료 조건."""
    states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.WAITING)]
    assert is_cycle_complete(states) is True


def test_is_cycle_not_complete_while_holding():
    states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.HOLDING, qty=105)]
    assert is_cycle_complete(states) is False


def test_is_cycle_not_complete_while_pending():
    """PENDING 주문이 남아 있으면 아직 종료가 아니다."""
    states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.BUY_PENDING)]
    assert is_cycle_complete(states) is False
    states = [_stage(1, StageStatus.SELL_PENDING, qty=100)]
    assert is_cycle_complete(states) is False
