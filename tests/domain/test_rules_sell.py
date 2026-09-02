from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import Cycle, confirm_anchor, start
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.rules import BuyStage, SellStage, TriggerParams, decide
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
FIVE = Decimal("0.05")
PARAMS = TriggerParams(target_pct=FIVE)


def ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running_cycle(lad: Ladder) -> Cycle:
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    return confirm_anchor(start(idle, at=T0), anchor_price=lad.anchor_price,
                          ladder=lad, at=T0)


def stage(lad: Ladder, no: int, status: StageStatus,
          fill_price: int | None = None, fill_qty: int | None = None,
          last_sold_at: datetime | None = None) -> StageState:
    return StageState(stage_no=no, status=status, trigger_price=lad.trigger_price(no),
                      planned_qty=lad.planned_qty(no), fill_price=fill_price,
                      fill_qty=fill_qty, last_sold_at=last_sold_at)


def run(price: int, states, lad: Ladder, params=PARAMS, now=T0):
    return decide(tick=Tick(code="005930", price=price, at=T0, source=TickSource.WS),
                  cycle=running_cycle(lad), states=states, params=params,
                  now=now, market_open=True, stock_code="005930")


def test_sells_when_target_reached():
    lad = ladder()
    states = [stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100)]
    decisions = run(10_500, states, lad)
    assert len(decisions) == 1
    d = decisions[0]
    assert isinstance(d, SellStage)
    assert d.stage_no == 1
    assert d.limit_price == 10_500, "목표가로 지정가 발주"
    assert d.qty == 100


def test_no_sell_below_target():
    lad = ladder()
    states = [stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100)]
    assert run(10_499, states, lad) == []


def test_sell_limit_uses_ceiled_target_price():
    """목표가는 체결가 × (1+목표율) 을 호가 단위로 올린 값이다."""
    lad = ladder()
    states = [stage(lad, 2, StageStatus.HOLDING, fill_price=9_480, fill_qty=105)]
    d = run(9_960, states, lad)[0]
    assert d.limit_price == 9_960   # 9,954 → 올림


def test_rule1_sell_takes_precedence_over_buy():
    """설계서 규칙 1 예시 시나리오를 그대로 재현한다.

    2단계: 매도완료 → 대기 (발동가 9,500)
    3단계: 보유, 체결가 9,000, 목표가 9,450
    현재가 9,500 → 두 조건이 동시에 충족되지만 매도만 집행한다.
    """
    lad = ladder()
    states = [
        stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100),
        stage(lad, 2, StageStatus.WAITING),
        stage(lad, 3, StageStatus.HOLDING, fill_price=9_000, fill_qty=111),
    ]
    decisions = run(9_500, states, lad)
    assert all(isinstance(d, SellStage) for d in decisions)
    assert [d.stage_no for d in decisions] == [3]
    assert not any(isinstance(d, BuyStage) for d in decisions)


def test_lower_stages_sell_first_when_multiple_targets_hit():
    """반등 구간에서 아래쪽 단계가 차례로 정리된다 — 의도된 동작."""
    lad = ladder()
    states = [
        stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100),
        stage(lad, 2, StageStatus.HOLDING, fill_price=9_500, fill_qty=105),
        stage(lad, 3, StageStatus.HOLDING, fill_price=9_000, fill_qty=111),
    ]
    # 목표가: 10,500 / 9,980 / 9,450 — 9,980 에서는 2·3단계만 충족
    decisions = run(9_980, states, lad)
    assert [d.stage_no for d in decisions] == [2, 3]


def test_all_stages_sell_at_high_price():
    lad = ladder()
    states = [
        stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100),
        stage(lad, 2, StageStatus.HOLDING, fill_price=9_500, fill_qty=105),
    ]
    assert [d.stage_no for d in run(11_000, states, lad)] == [1, 2]


@pytest.mark.parametrize(
    "status", [StageStatus.SELL_PENDING, StageStatus.BUY_PENDING, StageStatus.SOLD]
)
def test_rule5_excludes_non_holding_from_sell(status: StageStatus):
    """규칙 5: SELL_PENDING 은 이미 주문이 나갔으므로 중복 발주하지 않는다."""
    lad = ladder()
    states = [stage(lad, 1, status, fill_price=10_000, fill_qty=100)]
    assert run(11_000, states, lad) == []


def test_no_sell_on_decline_however_deep():
    """자동 손절매 배제 — 설계서 6절.

    `decide()` 에 하락 조건 매도 분기가 없다는 것은 이 브랜치의 세 구조적
    제약 중 하나다. 나머지 둘(신용 필드 부재, 시장가 표현 불가)은
    test_types.py 가 타입 구조로 확인하지만, 이 제약은 구조로 확인할 수
    없다 — 없는 분기를 볼 수는 없으므로 행동으로 못박는다.

    여러 단계를 보유한 채 모든 체결가를 크게 밑도는 틱이 와도 결정은 없다.
    평단 관리는 물타기(추가 매수)로 하며, 이 틱에서 매수가 나오지 않는 것은
    발동가보다 낮은 단계가 모두 이미 채워졌기 때문이다.
    """
    lad = ladder()
    states = [
        stage(lad, n, StageStatus.HOLDING,
              fill_price=lad.trigger_price(n), fill_qty=lad.planned_qty(n))
        for n in range(1, 8)
    ]
    # 7단계 체결가(7,000원)의 절반 — 어떤 단계도 목표가에 닿지 않았고,
    # 손절 기준선이라 부를 만한 값에는 전부 도달했다.
    assert run(3_500, states, lad) == []
    # 사이클 전체가 -65% 인 극단값에서도 마찬가지다.
    assert run(100, states, lad) == []


def test_sell_reason_records_basis():
    lad = ladder()
    states = [stage(lad, 3, StageStatus.HOLDING, fill_price=8_950, fill_qty=111)]
    reason = run(9_400, states, lad)[0].reason
    assert "stage=3 SELL" in reason
    assert "tick=9400(WS)" in reason
    assert "target=9400" in reason
    assert "fill=8950" in reason
    assert "target_pct=5%" in reason
    assert "rule1_sell_first" in reason
