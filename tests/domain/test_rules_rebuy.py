from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import Cycle, confirm_anchor, start
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.rules import BuyStage, TriggerParams, decide
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def ladder() -> Ladder:
    return Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running_cycle(lad: Ladder) -> Cycle:
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    return confirm_anchor(start(idle, at=T0), anchor_price=lad.anchor_price,
                          ladder=lad, at=T0)


def sold_then_waiting(lad: Ladder, no: int, sold_at: datetime) -> StageState:
    """매도 후 대기로 복귀한 단계 (재매수 대상)."""
    return StageState(stage_no=no, status=StageStatus.WAITING,
                      trigger_price=lad.trigger_price(no),
                      planned_qty=lad.planned_qty(no),
                      last_sold_at=sold_at, rebuy_count=1)


def run(price: int, states, lad: Ladder, params: TriggerParams, now: datetime):
    return decide(tick=Tick(code="005930", price=price, at=now, source=TickSource.WS),
                  cycle=running_cycle(lad), states=states, params=params,
                  now=now, market_open=True, stock_code="005930")


@pytest.mark.parametrize(
    ("elapsed_sec", "expect_buy"),
    [(0, False), (30, False), (59, False), (60, True), (61, True), (600, True)],
)
def test_rule3_cooldown_boundary(elapsed_sec: int, expect_buy: bool):
    """규칙 3: 매도 체결 후 60초가 지나야 재매수한다."""
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    now = T0 + timedelta(seconds=elapsed_sec)
    decisions = run(9_500, states, lad, params, now)
    assert bool(decisions) is expect_buy
    if expect_buy:
        assert isinstance(decisions[0], BuyStage)
        assert decisions[0].stage_no == 2


@pytest.mark.parametrize("bad_value", [60.0, True, Decimal(60)])
def test_rejects_non_int_cooldown(bad_value: object):
    """쿨다운은 초 단위 int 다. float 초는 경과시간 비교를 흐리고, bool 은
    `True == 1` 이라 1초 쿨다운으로 조용히 해석된다."""
    with pytest.raises(TypeError, match="rebuy_cooldown_sec must be int"):
        TriggerParams(target_pct=FIVE, rebuy_cooldown_sec=bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_value", [1, 0, "false", None])
def test_rejects_non_bool_allow_rebuy(bad_value: object):
    """재매수 허용은 bool 이다 — 진리값으로 해석하면 설정이 뒤집힐 수 있다.

    `_eval_buy` 는 `if not params.allow_rebuy` 로 이 값을 읽는다. 문자열
    "false" 는 참이므로 사용자가 끈 재매수가 켜진 것으로 읽힌다. Plan 2 의
    SQLite 는 boolean 을 0/1 로 돌려주므로, 그 변환을 저장소 경계에서
    명시적으로 하도록 여기서 int 도 거절한다.
    """
    with pytest.raises(TypeError, match="allow_rebuy must be bool"):
        TriggerParams(target_pct=FIVE, allow_rebuy=bad_value)  # type: ignore[arg-type]


def test_custom_cooldown_is_honored():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=300)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    assert run(9_500, states, lad, params, T0 + timedelta(seconds=299)) == []
    assert run(9_500, states, lad, params, T0 + timedelta(seconds=300)) != []


def test_zero_cooldown_allows_immediate_rebuy():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=0)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    assert run(9_500, states, lad, params, T0) != []


def test_allow_rebuy_false_blocks_rebuy():
    """설정이 재매수를 막으면 대기 상태여도 매수하지 않는다.

    정상 흐름에서는 재매수 불허 단계가 SOLD 로 끝나므로 WAITING 으로 오지
    않지만, 사용자가 사이클 중간에 설정을 바꿀 수 있어 방어가 필요하다.
    """
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=False, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    assert run(9_500, states, lad, params, T0 + timedelta(hours=1)) == []


def test_first_buy_is_not_affected_by_cooldown():
    """최초 매수(last_sold_at 없음)는 쿨다운과 무관하다."""
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=3600)
    states = [
        StageState(stage_no=2, status=StageStatus.WAITING,
                   trigger_price=lad.trigger_price(2), planned_qty=lad.planned_qty(2))
    ]
    assert run(9_500, states, lad, params, T0) != []


def test_cooldown_skips_to_next_eligible_stage():
    """쿨다운 중인 단계는 건너뛰고 다음 조건 충족 단계를 본다."""
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [
        sold_then_waiting(lad, 2, sold_at=T0),   # 쿨다운 중
        StageState(stage_no=3, status=StageStatus.WAITING,
                   trigger_price=lad.trigger_price(3), planned_qty=lad.planned_qty(3)),
    ]
    decisions = run(9_000, states, lad, params, T0 + timedelta(seconds=10))
    assert [d.stage_no for d in decisions] == [3]


def test_rebuy_reason_marks_rebuy():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    reason = run(9_500, states, lad, params, T0 + timedelta(seconds=90))[0].reason
    assert "rebuy=1" in reason
    assert "cooldown_ok" in reason


# naive/aware datetime 혼용은 ValueError 로 원인을 밝힌다 — 그냥 빼면 TypeError
# 가 나지만 그 예외는 어느 단계·어느 필드가 원인인지 알려주지 않는다.
def test_naive_last_sold_at_raises_value_error():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    naive_sold_at = datetime(2026, 9, 1, 9, 30)  # no tzinfo
    states = [sold_then_waiting(lad, 2, sold_at=naive_sold_at)]
    with pytest.raises(ValueError):
        run(9_500, states, lad, params, T0 + timedelta(seconds=90))


def test_naive_now_raises_value_error():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    naive_now = datetime(2026, 9, 1, 9, 32)  # no tzinfo
    with pytest.raises(ValueError):
        run(9_500, states, lad, params, naive_now)


# 시계 역행(now 가 last_sold_at 보다 이전)은 예외가 아니라 재매수 차단으로
# 처리된다. 경과 시간을 알 수 없을 때 거래하지 않는 것이 안전한 방향이므로
# 의도된 동작이며, "고치지 않고" 못박는다.
def test_clock_regression_blocks_rebuy_without_raising():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    past_now = T0 - timedelta(seconds=5)
    assert run(9_500, states, lad, params, past_now) == []
