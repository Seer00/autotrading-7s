from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import Cycle, confirm_anchor, pause, start
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.rules import BuyStage, TriggerParams, decide
from autotrading7s.domain.stage import StageState, to_buy_pending, to_holding
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
FIVE = Decimal("0.05")
PARAMS = TriggerParams(target_pct=FIVE)


def ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running_cycle(lad: Ladder | None = None) -> Cycle:
    lad = lad or ladder()
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    return confirm_anchor(start(idle, at=T0), anchor_price=lad.anchor_price,
                          ladder=lad, at=T0)


def fresh_states(lad: Ladder) -> list[StageState]:
    """1단계는 이미 체결(앵커 확정), 나머지는 대기."""
    states = [
        StageState(stage_no=1, status=StageStatus.HOLDING,
                   trigger_price=lad.trigger_price(1),
                   planned_qty=lad.planned_qty(1),
                   fill_price=lad.anchor_price, fill_qty=lad.planned_qty(1),
                   bought_at=T0)
    ]
    for n in range(2, lad.max_stages + 1):
        states.append(
            StageState(stage_no=n, status=StageStatus.WAITING,
                       trigger_price=lad.trigger_price(n),
                       planned_qty=lad.planned_qty(n))
        )
    return states


def tick(price: int, source: TickSource = TickSource.WS) -> Tick:
    return Tick(code="005930", price=price, at=T0, source=source)


def run(price: int, states, cycle=None, market_open=True, now=T0, params=PARAMS):
    return decide(tick=tick(price), cycle=cycle or running_cycle(),
                  states=states, params=params, now=now, market_open=market_open)


def test_buys_next_stage_when_trigger_reached():
    lad = ladder()
    states = fresh_states(lad)
    decisions = run(9_500, states)
    assert len(decisions) == 1
    d = decisions[0]
    assert isinstance(d, BuyStage)
    assert d.stage_no == 2
    assert d.limit_price == 9_500, "지정가는 관측된 현재가로 발주한다"
    assert d.qty == lad.planned_qty(2)


def test_no_buy_above_trigger():
    assert run(9_501, fresh_states(ladder())) == []


def test_gap_down_buys_only_one_stage_per_tick():
    """규칙 2: 발동가 3개를 한꺼번에 통과해도 한 틱에 1단계만."""
    lad = ladder()
    decisions = run(8_400, fresh_states(lad))
    assert len(decisions) == 1
    assert decisions[0].stage_no == 2, "번호가 낮은 단계부터 채운다"


def test_gap_down_fills_sequentially_over_ticks():
    """8,400 에 머무는 동안 연속 틱으로 2 → 3 → 4 단계가 채워진다."""
    lad = ladder()
    states = fresh_states(lad)
    filled: list[int] = []

    for _ in range(3):
        decisions = run(8_400, states)
        assert len(decisions) == 1
        d = decisions[0]
        filled.append(d.stage_no)
        idx = d.stage_no - 1
        states[idx] = to_holding(
            to_buy_pending(states[idx]), fill_price=8_400, fill_qty=d.qty, at=T0
        )

    assert filled == [2, 3, 4]
    # 체결가는 발동가가 아니라 실제 체결가로 기록된다
    assert [states[i].fill_price for i in (1, 2, 3)] == [8_400, 8_400, 8_400]


@pytest.mark.parametrize(
    "status",
    [StageStatus.BUY_PENDING, StageStatus.SELL_PENDING, StageStatus.HOLDING,
     StageStatus.SOLD],
)
def test_rule5_excludes_non_waiting_stages(status: StageStatus):
    """규칙 5: PENDING 상태 단계는 판정 대상에서 제외한다."""
    lad = ladder()
    states = fresh_states(lad)
    states[1] = StageState(stage_no=2, status=status, trigger_price=9_500,
                           planned_qty=105, fill_price=9_500, fill_qty=105)
    decisions = run(9_500, states)
    # 2단계가 제외되면 3단계 발동가(9,000)에는 아직 못 미쳤으므로 결정 없음
    assert [d.stage_no for d in decisions if isinstance(d, BuyStage)] == []


def test_rule4_no_decision_outside_market_hours():
    """규칙 4: 장 운영시간 밖에서는 어떤 결정도 내리지 않는다."""
    assert run(8_400, fresh_states(ladder()), market_open=False) == []


def test_no_decision_while_starting():
    """앵커가 없으면 사다리를 계산할 수 없다."""
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    starting = start(idle, at=T0)
    lad = ladder()
    assert decide(tick=tick(8_400), cycle=starting, states=fresh_states(lad),
                  params=PARAMS, now=T0, market_open=True) == []


def test_no_decision_while_paused():
    assert run(8_400, fresh_states(ladder()), cycle=pause(running_cycle())) == []


def test_reason_records_trigger_basis():
    """설계서 12.2절: 판정 근거를 사람이 읽을 수 있게 남긴다."""
    reason = run(9_500, fresh_states(ladder()))[0].reason
    assert "stage=2 BUY" in reason
    assert "tick=9500(WS)" in reason
    assert "trigger=9500" in reason
    assert "anchor=10000" in reason
    assert "drop=5%" in reason
    assert "rule2_sequential" in reason


def test_reason_records_rest_poll_source():
    lad = ladder()
    d = decide(tick=tick(9_500, TickSource.REST_POLL), cycle=running_cycle(lad),
               states=fresh_states(lad), params=PARAMS, now=T0, market_open=True)[0]
    assert "tick=9500(REST_POLL)" in d.reason
