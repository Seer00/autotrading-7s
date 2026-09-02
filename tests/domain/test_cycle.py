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


def complete_stages() -> list[StageState]:
    """SOLD 또는 WAITING 상태로 사이클 종료 조건을 만족하는 단계들."""
    return [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.WAITING)]


def _stage(no: int, status: StageStatus, qty: int | None = None) -> StageState:
    return StageState(stage_no=no, status=status, trigger_price=10_000 - no * 500,
                      planned_qty=100, fill_price=9_000 if qty else None, fill_qty=qty)


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
    """FINDING F3: 모든 non-RUNNING 상태는 유효한 anchor/ladder가 있어도 triggers를 거부한다."""
    # FINDING F3: 모든 상태에 anchor와 ladder를 제공하여 stronger assertion
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
    """FINDING C: close()는 이제 완료된 단계 목록을 요구한다."""
    cyc = close(running(), reason=CloseReason.NORMAL, at=T0, states=complete_stages())
    assert cyc.status is CycleStatus.CLOSED
    assert cyc.close_reason is CloseReason.NORMAL
    assert cyc.closed_at == T0


def test_close_from_liquidating_records_emergency():
    """FINDING C: close()는 이제 완료된 단계 목록을 요구한다."""
    cyc = close(begin_liquidation(running()), reason=CloseReason.EMERGENCY, at=T0,
                states=complete_stages())
    assert cyc.close_reason is CloseReason.EMERGENCY


def test_paused_can_be_closed():
    """외부에서 수동 전량 매도된 종목은 PAUSED 에서 종료할 수 있어야 한다.
    FINDING C: close()는 이제 완료된 단계 목록을 요구한다.
    """
    assert close(pause(running()), reason=CloseReason.NORMAL, at=T0,
                 states=complete_stages()).status is CycleStatus.CLOSED


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
    """FINDING A: RUNNING과 LIQUIDATING 상태는 anchor_price와 ladder가 필수."""
    if status in (CycleStatus.RUNNING, CycleStatus.LIQUIDATING):
        cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status,
                    anchor_price=10_000, ladder=ladder())
    else:
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


# FINDING A: Cycle 불변량 검사 테스트
def test_cycle_running_requires_anchor_price():
    """FINDING A: RUNNING 상태는 anchor_price가 필수."""
    with pytest.raises(ValueError, match="requires anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              ladder=ladder())


def test_cycle_running_requires_ladder():
    """FINDING A: RUNNING 상태는 ladder가 필수."""
    with pytest.raises(ValueError, match="requires ladder"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=10_000)


def test_cycle_running_anchor_ladder_must_match():
    """FINDING A: RUNNING의 anchor_price와 ladder.anchor_price가 일치해야 한다."""
    with pytest.raises(ValueError, match="anchor_price .* != ladder.anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=9_000, ladder=ladder(anchor=10_000))


def test_cycle_paused_requires_anchor_price():
    """FINDING A: PAUSED 상태는 anchor_price가 필수."""
    with pytest.raises(ValueError, match="requires anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.PAUSED,
              ladder=ladder())


def test_cycle_paused_requires_ladder():
    """FINDING A: PAUSED 상태는 ladder가 필수."""
    with pytest.raises(ValueError, match="requires ladder"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.PAUSED,
              anchor_price=10_000)


def test_cycle_paused_anchor_ladder_mismatch():
    """FINDING A: PAUSED에서도 anchor/ladder 미스매치는 거부된다."""
    with pytest.raises(ValueError, match="anchor_price .* != ladder.anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.PAUSED,
              anchor_price=9_340, ladder=ladder(anchor=10_000))


def test_cycle_liquidating_from_starting_allows_no_fields():
    """FINDING D: LIQUIDATING은 STARTING에서 (사용자 긴급 취소) anchor/ladder 없이도 가능."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.LIQUIDATING)
    assert cyc.anchor_price is None
    assert cyc.ladder is None


def test_cycle_liquidating_with_anchor_requires_matching_ladder():
    """FINDING A: LIQUIDATING이 anchor_price를 가지면 ladder도 필수이고 일치해야 한다."""
    with pytest.raises(ValueError, match="requires ladder"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.LIQUIDATING,
              anchor_price=10_000)


def test_cycle_idle_allows_no_fields():
    """FINDING A: IDLE 상태는 anchor와 ladder가 없어도 된다."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    assert cyc.anchor_price is None
    assert cyc.ladder is None


def test_cycle_starting_allows_no_fields():
    """FINDING A: STARTING 상태는 anchor와 ladder가 없어도 된다."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.STARTING)
    assert cyc.anchor_price is None
    assert cyc.ladder is None


# FINDING B: is_cycle_complete 공백 검사 테스트
def test_is_cycle_complete_empty_raises():
    """FINDING B: 빈 단계 리스트는 데이터 무결성 실패를 나타내므로 ValueError를 던진다."""
    with pytest.raises(ValueError, match="stage states sequence is empty"):
        is_cycle_complete([])


# FINDING C: close() 검증 테스트
def test_close_rejects_incomplete_cycle():
    """FINDING C: close()는 보유 주식이 있으면 거부한다."""
    incomplete_states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.HOLDING, qty=100)]
    with pytest.raises(ValueError, match="100 shares still held"):
        close(running(), reason=CloseReason.NORMAL, at=T0, states=incomplete_states)


def test_close_emergency_also_checks_holdings():
    """FINDING C: 긴급청산 종료도 보유 주식을 확인한다."""
    incomplete_states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.HOLDING, qty=50)]
    with pytest.raises(ValueError, match="50 shares still held"):
        close(begin_liquidation(running()), reason=CloseReason.EMERGENCY, at=T0,
              states=incomplete_states)


def test_close_with_pending_orders_fails():
    """FINDING F4: PENDING 주문이 있으면 사이클을 종료할 수 없다."""
    incomplete_states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.BUY_PENDING)]
    with pytest.raises(ValueError, match="pending orders"):
        close(running(), reason=CloseReason.NORMAL, at=T0, states=incomplete_states)


# FINDING D: begin_liquidation from STARTING 테스트
def test_begin_liquidation_from_starting():
    """FINDING D: 긴급청산은 STARTING 상태에서도 가능해야 한다 (사용자 중단)."""
    cyc = start(idle(), at=T0)
    assert cyc.status is CycleStatus.STARTING
    liq_cyc = begin_liquidation(cyc)
    assert liq_cyc.status is CycleStatus.LIQUIDATING


def test_begin_liquidation_still_refuses_idle():
    """FINDING D: 긴급청산은 IDLE에서는 여전히 거부된다 (청산할 포지션 없음)."""
    with pytest.raises(IllegalCycleTransition):
        begin_liquidation(idle())


def test_begin_liquidation_still_refuses_closed():
    """FINDING D: 긴급청산은 CLOSED에서는 여전히 거부된다."""
    with pytest.raises(IllegalCycleTransition):
        begin_liquidation(
            Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED)
        )


def test_begin_liquidation_still_refuses_from_liquidating():
    """FINDING D: 긴급청산은 LIQUIDATING에서 거부된다 (이중 요청 방지)."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.LIQUIDATING,
                anchor_price=10_000, ladder=ladder())
    with pytest.raises(IllegalCycleTransition):
        begin_liquidation(cyc)


def test_liquidating_to_paused_raises():
    """LIQUIDATING → PAUSED는 설계서 4.2절의 일방향 래칫을 보호하기 위해 거부된다.
    설계: PAUSED → RUNNING이 허용되므로, LIQUIDATING → PAUSED → RUNNING 경로는
    사용자가 이미 청산을 시작한 종목에서 자동 트리거를 다시 시작하게 할 수 있다
    (무한 물타기 위험, 설계서 7.2절). LIQUIDATING은 오직 CLOSED로만 전이한다.
    """
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.LIQUIDATING,
                anchor_price=10_000, ladder=ladder())
    with pytest.raises(IllegalCycleTransition):
        pause(cyc)
