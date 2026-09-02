from __future__ import annotations

from dataclasses import replace
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


# 다른 종목의 틱은 판정에 들어갈 수 없다.

def test_wrong_stock_code_raises_value_error():
    """다른 종목의 틱으로 판정하면 남의 가격으로 이 종목을 주문한다."""
    lad = ladder()
    with pytest.raises(ValueError) as exc_info:
        run(9_500, fresh_states(lad), stock_code="035720")
    assert str(exc_info.value) == (
        "Tick code mismatch: tick has '005930', but stock_code is '035720'"
    )


def test_wrong_stock_code_raises_even_when_market_closed():
    """종목 불일치는 프로그래밍 오류이므로 장 운영시간 게이트보다 먼저 난다."""
    lad = ladder()
    with pytest.raises(ValueError, match="Tick code mismatch"):
        run(9_500, fresh_states(lad), market_open=False, stock_code="035720")


def test_matching_stock_code_still_works():
    """종목이 일치하면 정상 판정한다."""
    lad = ladder()
    decisions = run(9_500, fresh_states(lad), stock_code="005930")
    assert len(decisions) == 1


# 틱 가격의 불변식.

def test_tick_price_zero_raises_value_error():
    """가격 0 은 증권사 API 에서 시장가의 전선 표현이다 — 시세로 받지 않는다."""
    with pytest.raises(ValueError) as exc_info:
        Tick(code="005930", price=0, at=T0, source=TickSource.WS)
    assert str(exc_info.value) == "price must be positive: 0"


def test_tick_price_negative_raises_value_error():
    """음수 시세는 존재하지 않는다."""
    with pytest.raises(ValueError) as exc_info:
        Tick(code="005930", price=-5000, at=T0, source=TickSource.WS)
    assert str(exc_info.value) == "price must be positive: -5000"


def test_tick_price_float_raises_type_error():
    """float 시세는 발동가 비교와 금액 산술을 오염시킨다 — 설계서 3.1절."""
    with pytest.raises(TypeError) as exc_info:
        Tick(code="005930", price=9340.5, at=T0, source=TickSource.WS)
    assert str(exc_info.value) == "price must be int, not float"


def test_tick_price_bool_raises_type_error():
    """bool 은 int 의 하위 클래스지만 거절한다."""
    with pytest.raises(TypeError) as exc_info:
        Tick(code="005930", price=True, at=T0, source=TickSource.WS)
    assert str(exc_info.value) == "price must be int, not bool"


# 상태 목록의 중복 단계 감지.

def test_duplicate_stage_no_raises_value_error():
    """같은 단계가 두 번 담긴 목록은 손상이다 — 어느 쪽이 진실인지 알 수 없다."""
    lad = ladder()
    states = fresh_states(lad)
    states.append(
        StageState(stage_no=2, status=StageStatus.WAITING,
                   trigger_price=9_500, planned_qty=105)
    )
    with pytest.raises(ValueError) as exc_info:
        run(9_500, states)
    assert str(exc_info.value) == "Duplicate stage_no in states: 2"


def test_duplicate_stage_no_raises_even_when_market_closed():
    """중복 검사도 장 운영시간 게이트보다 먼저 난다."""
    lad = ladder()
    states = fresh_states(lad)
    states.append(
        StageState(stage_no=2, status=StageStatus.WAITING,
                   trigger_price=9_500, planned_qty=105)
    )
    with pytest.raises(ValueError, match="Duplicate stage_no"):
        run(9_500, states, market_open=False)


# TriggerParams 의 불변식.

def test_trigger_params_zero_target_pct_raises():
    """목표율 0 이면 목표가가 체결가와 같아져 수수료만 태우고 회전한다."""
    with pytest.raises(ValueError) as exc_info:
        TriggerParams(target_pct=Decimal("0"))
    assert str(exc_info.value) == "target_pct must be positive: 0"


def test_trigger_params_negative_target_pct_raises():
    """음수 목표율은 손실 가격에 매도를 걸게 된다."""
    with pytest.raises(ValueError) as exc_info:
        TriggerParams(target_pct=Decimal("-0.05"))
    assert str(exc_info.value) == "target_pct must be positive: -0.05"


def test_trigger_params_negative_cooldown_raises():
    """음수 쿨다운은 쿨다운이 없는 것과 같다 — 설정 실수를 조용히 넘기지 않는다."""
    with pytest.raises(ValueError) as exc_info:
        TriggerParams(target_pct=FIVE, rebuy_cooldown_sec=-1)
    assert str(exc_info.value) == "rebuy_cooldown_sec must be non-negative: -1"


def test_trigger_params_valid_defaults_construct():
    """기본값(재매수 허용, 쿨다운 60초)은 그대로 구성된다."""
    params = TriggerParams(target_pct=FIVE)
    assert params.target_pct == FIVE
    assert params.allow_rebuy is True
    assert params.rebuy_cooldown_sec == 60


@pytest.mark.parametrize("bad_value", [0.05, 0.25, 0.5, 0.125, 0.0625, 1, True])
def test_trigger_params_rejects_non_decimal_target_pct(bad_value: object):
    """목표율은 Decimal 이다 — float 은 여기서 막아야 한다.

    `decide()` 의 목표율 대조로는 이 값을 잡을 수 없다. Decimal 은 float 과
    **정확한 값**으로 비교되므로, 2진수로 정확히 표현되는 비율은 서로 같다고
    나온다: `Decimal("0.25") == 0.25` 는 참이다. 그러면 대조를 통과한 float 이
    `target_price` 까지 흘러가 매 틱 TypeError 를 내고, 손절매가 없는 이
    전략에서 그 종목은 어떤 단계도 팔지 못한다. 25%·12.5%·50%·6.25% 는
    모두 있을 수 있는 목표율이다.
    """
    with pytest.raises(TypeError, match="target_pct must be Decimal"):
        TriggerParams(target_pct=bad_value)  # type: ignore[arg-type]


def test_trigger_params_accepts_decimal_target_pct():
    """Decimal 목표율은 그대로 구성된다 — 막는 것은 타입뿐이다."""
    params = TriggerParams(target_pct=Decimal("0.05"))
    assert params.target_pct == Decimal("0.05")


def test_float_target_pct_fails_at_construction_not_at_every_tick():
    """사다리와 매개변수가 "같은" 값을 들고도 매 틱 예외가 나던 경로의 회귀 테스트.

    Ladder 의 Decimal("0.25") 와 TriggerParams 의 float 0.25 는 대조를
    통과했다(두 값이 같다). 이제 TriggerParams 구성 시점에 막히므로
    decide() 는 호출조차 되지 않는다.
    """
    lad = Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=Decimal("0.25"),
                 max_stages=7, amount_per_stage=1_000_000)
    assert lad.target_pct == Decimal("0.25")
    with pytest.raises(TypeError, match="target_pct must be Decimal"):
        TriggerParams(target_pct=0.25)  # type: ignore[arg-type]
    # 대조 검사만으로는 막히지 않았음을 함께 못박는다.
    assert Decimal("0.25") == 0.25, "Decimal 은 float 과 정확한 값으로 비교된다"


# 사다리와 매개변수의 목표율은 같은 값이어야 한다.

def test_target_pct_mismatch_raises():
    """같은 값이 두 곳에 저장되므로 판정 전에 대조한다 — 사다리는 사이클에
    박제되고 매개변수는 설정에서 다시 읽히므로 어긋날 수 있다."""
    lad_5pct = ladder(anchor=10_000)
    cycle = running_cycle(lad_5pct)

    params_3pct = TriggerParams(target_pct=Decimal("0.03"))

    with pytest.raises(ValueError) as exc_info:
        decide(tick=tick(9_500), cycle=cycle, states=fresh_states(lad_5pct),
               params=params_3pct, now=T0, market_open=True, stock_code="005930")

    assert str(exc_info.value) == (
        "target_pct mismatch: ladder has 0.05, params has 0.03"
    )


def test_target_pct_match_proceeds():
    """일치하면 정상 판정한다."""
    lad = ladder()  # 5%
    cycle = running_cycle(lad)
    params = TriggerParams(target_pct=FIVE)  # 5%

    decisions = decide(tick=tick(9_500), cycle=cycle, states=fresh_states(lad),
                       params=params, now=T0, market_open=True, stock_code="005930")
    assert len(decisions) == 1


def test_stored_trigger_price_must_match_the_ladder():
    """저장된 발동가가 사다리의 계산과 다르면 손상된 데이터다 — 판정하지 않는다.

    설계서 4.2절은 이 숫자를 두 곳(`cycle.ladder_json`, `stage_state.
    trigger_price`)에 쓴다. Plan 2 는 두 값을 묶어 두는 제약이 없는 컬럼에서
    복원하므로, 손상된 발동가는 앵커보다 높은 가격에 매수하는 결정을 만들어
    전략을 거꾸로 돌릴 수 있다.
    """
    lad = ladder()
    states = fresh_states(lad)
    states[1] = replace(states[1], trigger_price=999_999)

    with pytest.raises(ValueError) as exc_info:
        run(10_200, states)

    msg = str(exc_info.value)
    assert "stage 2" in msg, "어느 단계인지 밝혀야 한다"
    assert "999999" in msg, "저장된 값을 밝혀야 한다"
    assert "9500" in msg, "사다리가 계산한 값을 밝혀야 한다"


def test_stored_trigger_price_mismatch_raises_instead_of_skipping():
    """조용히 건너뛰면 손상이 숨는다 — 판정 대상이 없을 때도 예외가 나야 한다."""
    lad = ladder()
    states = fresh_states(lad)
    states[1] = replace(states[1], trigger_price=1)  # 어떤 틱에서도 발동하지 않는 값

    with pytest.raises(ValueError, match="trigger_price"):
        run(10_000, states)


def test_partial_states_list_stays_legal():
    """일부 단계만 담긴 목록은 유효하다 — 없는 단계는 검사 대상이 아니다."""
    lad = ladder()
    states = [
        StageState(stage_no=3, status=StageStatus.WAITING,
                   trigger_price=lad.trigger_price(3),
                   planned_qty=lad.planned_qty(3))
    ]
    decisions = run(9_000, states)
    assert [d.stage_no for d in decisions] == [3]


def test_starting_cycle_with_mismatched_target_pct_returns_empty():
    """STARTING 은 사다리가 없어 목표율 대조 이전에 빈 목록으로 끝난다."""
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    starting = start(idle, at=T0)
    lad = ladder()

    params_3pct = TriggerParams(target_pct=Decimal("0.03"))

    result = decide(tick=tick(9_500), cycle=starting, states=fresh_states(lad),
                    params=params_3pct, now=T0, market_open=True, stock_code="005930")
    # Should return [] because cycle.ladder is None (gate 4), not raise (gate 6)
    assert result == []
