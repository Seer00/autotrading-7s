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


def run(price: int, states, cycle=None, market_open=True, now=T0, params=PARAMS,
        stock_code: str = "005930"):
    return decide(tick=tick(price), cycle=cycle or running_cycle(),
                  states=states, params=params, now=now, market_open=market_open,
                  stock_code=stock_code)


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
                  params=PARAMS, now=T0, market_open=True, stock_code="005930") == []


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
               states=fresh_states(lad), params=PARAMS, now=T0, market_open=True,
               stock_code="005930")[0]
    assert "tick=9500(REST_POLL)" in d.reason


# Finding 1: Wrong instrument detection
def test_wrong_stock_code_raises_value_error():
    """FINDING 1: decide() must detect a tick for the wrong instrument."""
    lad = ladder()
    with pytest.raises(ValueError) as exc_info:
        run(9_500, fresh_states(lad), stock_code="035720")
    assert "005930" in str(exc_info.value)
    assert "035720" in str(exc_info.value)


def test_wrong_stock_code_raises_even_when_market_closed():
    """FINDING 1: Stock code check happens before market_open gate."""
    lad = ladder()
    with pytest.raises(ValueError):
        run(9_500, fresh_states(lad), market_open=False, stock_code="035720")


def test_matching_stock_code_still_works():
    """FINDING 1: Matching code proceeds normally."""
    lad = ladder()
    decisions = run(9_500, fresh_states(lad), stock_code="005930")
    assert len(decisions) == 1


# Finding 2: Tick price validation
def test_tick_price_zero_raises_value_error():
    """FINDING 2: Tick price must be positive."""
    with pytest.raises(ValueError) as exc_info:
        Tick(code="005930", price=0, at=T0, source=TickSource.WS)
    assert "positive" in str(exc_info.value).lower() or "0" in str(exc_info.value)


def test_tick_price_negative_raises_value_error():
    """FINDING 2: Tick price must be positive."""
    with pytest.raises(ValueError) as exc_info:
        Tick(code="005930", price=-5000, at=T0, source=TickSource.WS)
    assert "positive" in str(exc_info.value).lower() or "-5000" in str(exc_info.value)


def test_tick_price_float_raises_type_error():
    """FINDING 2: Tick price must be int, not float."""
    with pytest.raises(TypeError) as exc_info:
        Tick(code="005930", price=9340.5, at=T0, source=TickSource.WS)
    assert "int" in str(exc_info.value).lower()


def test_tick_price_bool_raises_type_error():
    """FINDING 2: bool is rejected even though it's technically an int subclass."""
    with pytest.raises(TypeError) as exc_info:
        Tick(code="005930", price=True, at=T0, source=TickSource.WS)
    assert "int" in str(exc_info.value).lower()


# Finding 3: Duplicate stage_no detection
def test_duplicate_stage_no_raises_value_error():
    """FINDING 3: Passing states with two entries for same stage_no raises ValueError."""
    lad = ladder()
    states = fresh_states(lad)
    # Add a duplicate entry for stage 2
    states.append(
        StageState(stage_no=2, status=StageStatus.WAITING,
                   trigger_price=9_500, planned_qty=105)
    )
    with pytest.raises(ValueError) as exc_info:
        run(9_500, states)
    assert "2" in str(exc_info.value) or "duplicate" in str(exc_info.value).lower()


def test_duplicate_stage_no_raises_even_when_market_closed():
    """FINDING 3: Duplicate check happens before market_open gate."""
    lad = ladder()
    states = fresh_states(lad)
    states.append(
        StageState(stage_no=2, status=StageStatus.WAITING,
                   trigger_price=9_500, planned_qty=105)
    )
    with pytest.raises(ValueError):
        run(9_500, states, market_open=False)


# Finding 5: TriggerParams validation
def test_trigger_params_zero_target_pct_raises():
    """FINDING 5: target_pct must be positive."""
    with pytest.raises(ValueError) as exc_info:
        TriggerParams(target_pct=Decimal("0"))
    assert "positive" in str(exc_info.value).lower() or "0" in str(exc_info.value)


def test_trigger_params_negative_target_pct_raises():
    """FINDING 5: target_pct must be positive."""
    with pytest.raises(ValueError) as exc_info:
        TriggerParams(target_pct=Decimal("-0.05"))
    assert "positive" in str(exc_info.value).lower()


def test_trigger_params_negative_cooldown_raises():
    """FINDING 5: rebuy_cooldown_sec must be non-negative."""
    with pytest.raises(ValueError) as exc_info:
        TriggerParams(target_pct=FIVE, rebuy_cooldown_sec=-1)
    assert "non-negative" in str(exc_info.value).lower() or "-1" in str(exc_info.value)


def test_trigger_params_valid_defaults_construct():
    """FINDING 5: Valid defaults still work."""
    params = TriggerParams(target_pct=FIVE)
    assert params.target_pct == FIVE
    assert params.allow_rebuy is True
    assert params.rebuy_cooldown_sec == 60


# Finding 6: target_pct mismatch between ladder and params
def test_target_pct_mismatch_raises():
    """FINDING 6: Ladder and params target_pct must match."""
    lad_5pct = ladder(anchor=10_000)  # Has 5% target_pct
    cycle = running_cycle(lad_5pct)

    params_3pct = TriggerParams(target_pct=Decimal("0.03"))

    with pytest.raises(ValueError) as exc_info:
        decide(tick=tick(9_500), cycle=cycle, states=fresh_states(lad_5pct),
               params=params_3pct, now=T0, market_open=True, stock_code="005930")

    assert "5%" in str(exc_info.value) or "3%" in str(exc_info.value) or "target_pct" in str(exc_info.value).lower()


def test_target_pct_match_proceeds():
    """FINDING 6: Matching target_pct proceeds normally."""
    lad = ladder()  # 5%
    cycle = running_cycle(lad)
    params = TriggerParams(target_pct=FIVE)  # 5%

    decisions = decide(tick=tick(9_500), cycle=cycle, states=fresh_states(lad),
                       params=params, now=T0, market_open=True, stock_code="005930")
    assert len(decisions) == 1


def test_starting_cycle_with_mismatched_target_pct_returns_empty():
    """FINDING 6: STARTING cycle returns [] before mismatch check (gate 4 fires first)."""
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    starting = start(idle, at=T0)
    lad = ladder()

    params_3pct = TriggerParams(target_pct=Decimal("0.03"))

    result = decide(tick=tick(9_500), cycle=starting, states=fresh_states(lad),
                    params=params_3pct, now=T0, market_open=True, stock_code="005930")
    # Should return [] because cycle.ladder is None (gate 4), not raise (gate 6)
    assert result == []
