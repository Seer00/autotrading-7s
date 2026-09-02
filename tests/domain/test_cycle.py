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
    """RUNNING 만 트리거 판정을 받는다 — 사다리가 있어도 상태가 결정한다.

    앵커와 사다리를 모두 갖춘 사이클로 검사한다. 필드가 없어서 거부되는
    것이 아니라 상태 때문에 거부된다는 것을 보이려는 것이다.
    """
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
    """정상 종료는 사유와 시각을 남긴다 — 종료 조건을 만족한 단계 목록이 필요하다."""
    cyc = close(running(), reason=CloseReason.NORMAL, at=T0, states=complete_stages())
    assert cyc.status is CycleStatus.CLOSED
    assert cyc.close_reason is CloseReason.NORMAL
    assert cyc.closed_at == T0


def test_close_from_liquidating_records_emergency():
    """긴급청산을 거쳐 종료하면 사유가 EMERGENCY 로 남는다 — 설계서 12.2절."""
    cyc = close(begin_liquidation(running()), reason=CloseReason.EMERGENCY, at=T0,
                states=complete_stages())
    assert cyc.close_reason is CloseReason.EMERGENCY


def test_paused_can_be_closed():
    """외부에서 수동 전량 매도된 종목은 PAUSED 에서 종료할 수 있어야 한다
    (설계서 10.2절). 보유가 0 이므로 종료 조건을 만족한다."""
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
    """전이표에 없는 전이는 거부된다 — 설계서 4.2절.

    RUNNING·LIQUIDATING 케이스는 앵커와 사다리를 갖춰 구성한다. 그 상태의
    필드 불변식이 요구하기 때문이며, 전이 거부와는 별개다.
    """
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


# 상태별 필드 불변식 — 사다리 없이 판정할 수 있는 상태는 없다.

def test_cycle_running_requires_anchor_price():
    """RUNNING 은 앵커가 있어야 한다 — 사다리 전체가 앵커에서 파생된다."""
    with pytest.raises(ValueError, match="requires anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              ladder=ladder())


def test_cycle_running_requires_ladder():
    """RUNNING 은 사다리가 있어야 한다 — 없으면 발동가를 계산할 수 없다."""
    with pytest.raises(ValueError, match="requires ladder"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=10_000)


def test_cycle_running_anchor_ladder_must_match():
    """RUNNING 의 앵커와 사다리의 앵커가 다르면 어느 쪽이 진실인지 알 수 없다."""
    with pytest.raises(ValueError, match="anchor_price .* != ladder.anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=9_000, ladder=ladder(anchor=10_000))


def test_cycle_paused_requires_anchor_price():
    """PAUSED 도 앵커가 있어야 한다 — resume 으로 판정을 재개할 수 있다."""
    with pytest.raises(ValueError, match="requires anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.PAUSED,
              ladder=ladder())


def test_cycle_paused_requires_ladder():
    """PAUSED 도 사다리가 있어야 한다 — 보유는 유지되고 판정만 멈춘 상태다."""
    with pytest.raises(ValueError, match="requires ladder"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.PAUSED,
              anchor_price=10_000)


def test_cycle_paused_anchor_ladder_mismatch():
    """PAUSED 에서도 앵커/사다리 불일치는 거부된다."""
    with pytest.raises(ValueError, match="anchor_price .* != ladder.anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.PAUSED,
              anchor_price=9_340, ladder=ladder(anchor=10_000))


def test_cycle_liquidating_from_starting_allows_no_fields():
    """LIQUIDATING 은 앵커 없이도 성립한다 — 청산은 앵커가 확정되기 전인
    STARTING 에서도 시작할 수 있다(설계서 11.1절)."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.LIQUIDATING)
    assert cyc.anchor_price is None
    assert cyc.ladder is None


def test_cycle_liquidating_with_anchor_requires_matching_ladder():
    """앵커가 있으면 사다리도 있어야 한다 — 둘은 같은 순간에 확정된다."""
    with pytest.raises(ValueError, match="requires ladder"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.LIQUIDATING,
              anchor_price=10_000)


def test_cycle_idle_allows_no_fields():
    """IDLE 은 아직 아무것도 시작하지 않은 상태다 — 앵커도 사다리도 없다."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    assert cyc.anchor_price is None
    assert cyc.ladder is None


def test_cycle_starting_allows_no_fields():
    """STARTING 은 1단계 체결을 기다리는 구간이므로 앵커가 아직 없다."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.STARTING)
    assert cyc.anchor_price is None
    assert cyc.ladder is None


def test_is_cycle_complete_empty_raises():
    """빈 단계 리스트는 데이터 무결성 실패다 — "종료됨"으로 답하지 않는다.

    단계가 없는 사이클은 존재할 수 없다. True 를 돌려주면 보유를 추적하는
    주체 없이 사이클이 닫힌다.
    """
    with pytest.raises(ValueError, match="stage states sequence is empty"):
        is_cycle_complete([])


# close() 는 종료 조건을 실제로 확인한다.

def test_close_rejects_incomplete_cycle():
    """보유 주식이 남아 있으면 종료할 수 없다 — 남은 수량을 메시지에 남긴다."""
    incomplete_states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.HOLDING, qty=100)]
    with pytest.raises(ValueError, match="100 shares still held"):
        close(running(), reason=CloseReason.NORMAL, at=T0, states=incomplete_states)


def test_close_emergency_also_checks_holdings():
    """긴급청산 종료도 예외가 아니다 — 청산이 다 끝났는지 확인한다."""
    incomplete_states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.HOLDING, qty=50)]
    with pytest.raises(ValueError, match="50 shares still held"):
        close(begin_liquidation(running()), reason=CloseReason.EMERGENCY, at=T0,
              states=incomplete_states)


def test_close_with_pending_orders_fails():
    """PENDING 주문이 남아 있으면 종료할 수 없다 — 곧 보유가 생길 수 있다."""
    incomplete_states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.BUY_PENDING)]
    with pytest.raises(ValueError, match="pending orders"):
        close(running(), reason=CloseReason.NORMAL, at=T0, states=incomplete_states)


# 긴급청산을 시작할 수 있는 상태.

def test_begin_liquidation_from_starting():
    """1단계 주문이 나간 뒤 사용자가 중단할 수 있어야 한다 — 설계서 11.1절."""
    cyc = start(idle(), at=T0)
    assert cyc.status is CycleStatus.STARTING
    liq_cyc = begin_liquidation(cyc)
    assert liq_cyc.status is CycleStatus.LIQUIDATING


def test_begin_liquidation_still_refuses_idle():
    """IDLE 에서는 거부된다 — 청산할 포지션도 진행 중인 주문도 없다."""
    with pytest.raises(IllegalCycleTransition):
        begin_liquidation(idle())


def test_begin_liquidation_still_refuses_closed():
    """CLOSED 는 종단 상태다 — 이미 끝난 사이클을 다시 청산할 수 없다."""
    with pytest.raises(IllegalCycleTransition):
        begin_liquidation(
            Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED)
        )


def test_begin_liquidation_still_refuses_from_liquidating():
    """이미 청산 중이면 거부된다 — 이중 청산 주문을 막는다."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.LIQUIDATING,
                anchor_price=10_000, ladder=ladder())
    with pytest.raises(IllegalCycleTransition):
        begin_liquidation(cyc)


@pytest.mark.parametrize("status", list(CycleStatus))
def test_anchor_ladder_mismatch_is_rejected_in_every_status(status: CycleStatus):
    """앵커와 사다리가 어긋난 사이클은 어떤 상태에서도 존재할 수 없다.

    사다리는 앵커에서 전부 파생되므로, 두 값이 다르면 어느 쪽이 진실인지 알
    방법이 없다. CLOSED·STARTING·IDLE 도 예외가 아니다 — Plan 2 가 그 행을
    복원해 화면에 손익을 표시하고, 재시작 시 RUNNING 으로 되돌릴 수 있다.
    """
    with pytest.raises(ValueError, match="anchor_price .* != ladder.anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=status,
              anchor_price=9_000, ladder=ladder(anchor=10_000))


@pytest.mark.parametrize("status", list(CycleStatus))
def test_anchor_without_ladder_is_rejected_in_every_status(status: CycleStatus):
    """앵커가 있으면 사다리도 있어야 한다 — 앵커는 사다리 확정과 함께 생긴다."""
    with pytest.raises(ValueError, match="requires ladder"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=status, anchor_price=10_000)


@pytest.mark.parametrize(
    "status",
    [CycleStatus.IDLE, CycleStatus.STARTING, CycleStatus.LIQUIDATING,
     CycleStatus.CLOSED],
)
def test_ladder_without_anchor_is_rejected(status: CycleStatus):
    """사다리가 있으면 앵커도 있어야 한다 — 앵커 없이 사다리만 있는 행은 손상이다.

    "앵커가 있으면 사다리 필수" 의 거울상이다. 둘은 `confirm_anchor` 에서 같은
    순간에 생기므로 한쪽만 있는 행은 어느 상태에서도 성립하지 않는다.
    RUNNING·PAUSED 는 앵커를 먼저 요구하므로 이 검사에 닿지 않는다.
    """
    with pytest.raises(ValueError, match="requires anchor_price"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=status, ladder=ladder())


@pytest.mark.parametrize(
    "status",
    [CycleStatus.IDLE, CycleStatus.STARTING, CycleStatus.LIQUIDATING,
     CycleStatus.CLOSED],
)
def test_neither_anchor_nor_ladder_still_constructs(status: CycleStatus):
    """둘 다 없는 것은 정상이다 — 앵커가 확정되기 전의 사이클과 종료된 사이클."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status)
    assert cyc.anchor_price is None
    assert cyc.ladder is None


@pytest.mark.parametrize("status", list(CycleStatus))
def test_matching_anchor_and_ladder_constructs_in_every_status(status: CycleStatus):
    """일치하면 어떤 상태에서도 구성된다 — 검사는 불일치만 막는다."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status,
                anchor_price=10_000, ladder=ladder(anchor=10_000))
    assert cyc.anchor_price == cyc.ladder.anchor_price  # type: ignore[union-attr]


@pytest.mark.parametrize("field", ["cycle_id", "config_id", "seq"])
@pytest.mark.parametrize("bad_value", [1.5, "x", None, True, Decimal(1)])
def test_rejects_non_int_identity_fields(field: str, bad_value: object):
    """사이클의 정체성 필드는 int 다 — Plan 2 가 이 값으로 행을 찾는다."""
    kwargs = {"cycle_id": 1, "config_id": 1, "seq": 1,
              "status": CycleStatus.IDLE}
    kwargs[field] = bad_value
    with pytest.raises(TypeError, match=f"{field} must be int"):
        Cycle(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["cycle_id", "config_id", "seq"])
@pytest.mark.parametrize("bad_value", [0, -1])
def test_rejects_nonpositive_identity_fields(field: str, bad_value: int):
    kwargs = {"cycle_id": 1, "config_id": 1, "seq": 1,
              "status": CycleStatus.IDLE}
    kwargs[field] = bad_value
    with pytest.raises(ValueError, match=f"{field} must be positive"):
        Cycle(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CycleStatus.IDLE, False),
        (CycleStatus.STARTING, True),
        (CycleStatus.RUNNING, True),
        (CycleStatus.PAUSED, False),
        (CycleStatus.LIQUIDATING, False),
        (CycleStatus.CLOSED, False),
    ],
)
def test_is_active_only_for_starting_and_running(status: CycleStatus, expected: bool):
    """진행 중 사이클은 STARTING·RUNNING 뿐이다 — 나머지는 활성이 아니다."""
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status,
                anchor_price=10_000, ladder=ladder())
    assert cyc.is_active is expected


def test_confirm_anchor_rejects_anchor_not_matching_ladder():
    """앵커와 사다리를 함께 박제하는 지점에서 두 값의 일치를 확인한다."""
    with pytest.raises(ValueError, match="anchor mismatch: 9340 != ladder 10000"):
        confirm_anchor(start(idle(), at=T0), anchor_price=9_340,
                       ladder=ladder(anchor=10_000), at=T0)


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
