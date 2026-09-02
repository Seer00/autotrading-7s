"""G1 게이트 — 도메인 코어만으로 한 사이클을 끝까지 돌린다.

브로커도 DB도 없이 decide() 와 상태 전이 함수만으로 진행한다. 개별 단위
테스트가 통과해도 조합에서 어긋나는 문제를 잡기 위한 시나리오 테스트다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.domain.cycle import (
    Cycle,
    close,
    confirm_anchor,
    is_cycle_complete,
    start,
)
from autotrading7s.domain.guards import GuardContext, check_buy, check_sell
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.pnl import held_qty, invested_amount
from autotrading7s.domain.rules import BuyStage, SellStage, TriggerParams, decide
from autotrading7s.domain.stage import (
    StageState,
    after_sell,
    to_buy_pending,
    to_holding,
    to_sell_pending,
)
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    StageStatus,
    Tick,
    TickSource,
)

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def _ladder() -> Ladder:
    return Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def _initial_states(lad: Ladder) -> list[StageState]:
    return [
        StageState(stage_no=n, status=StageStatus.WAITING,
                   trigger_price=lad.trigger_price(n), planned_qty=lad.planned_qty(n))
        for n in range(1, lad.max_stages + 1)
    ]


def test_full_cycle_down_then_up_closes_at_zero_holdings():
    lad = _ladder()
    clock = FakeClock(current=T0)
    params = TriggerParams(target_pct=FIVE, allow_rebuy=False, rebuy_cooldown_sec=60)
    states = _initial_states(lad)

    # 1단계는 사이클 시작 시 체결되어 앵커를 확정한다.
    states[0] = to_holding(to_buy_pending(states[0]), fill_price=10_000,
                           fill_qty=lad.planned_qty(1), at=clock.now())
    cycle = confirm_anchor(
        start(Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE),
              at=clock.now()),
        anchor_price=10_000, ladder=lad, at=clock.now(),
    )
    assert cycle.status is CycleStatus.RUNNING

    orders = 0

    def step(price: int) -> list[BuyStage | SellStage]:
        nonlocal orders, states
        decisions = decide(
            tick=Tick(code="005930", price=price, at=clock.now(),
                      source=TickSource.WS),
            cycle=cycle, states=states, params=params,
            now=clock.now(), market_open=clock.is_market_open(),
            stock_code="005930",
        )
        for d in decisions:
            ctx = GuardContext(
                stock_invested=invested_amount(states), stock_limit=7_000_000,
                total_invested=invested_amount(states), total_limit=21_000_000,
                orders_last_minute=0,
            )
            idx = d.stage_no - 1
            if isinstance(d, BuyStage):
                assert check_buy(d, ctx).allowed
                states[idx] = to_holding(
                    to_buy_pending(states[idx]), fill_price=d.limit_price,
                    fill_qty=d.qty, at=clock.now(),
                )
            else:
                assert check_sell(d, ctx).allowed
                states[idx] = after_sell(
                    to_sell_pending(states[idx]), at=clock.now(),
                    allow_rebuy=params.allow_rebuy,
                )
            orders += 1
            clock.advance(1)
        return decisions

    # 하락 — 2~4단계가 순차로 채워진다.
    for price in (9_500, 9_000, 8_500):
        assert [d.stage_no for d in step(price)] != []

    assert [s.status for s in states[:4]] == [StageStatus.HOLDING] * 4
    assert held_qty(states) == sum(lad.planned_qty(n) for n in range(1, 5))

    # 반등 — 낮은 단계가 먼저 정리된다.
    sold_order: list[int] = []
    # 목표가: 4단계 8,930 / 3단계 9,450 / 2단계 9,980 / 1단계 10,500
    # (8,500 × 1.05 = 8,925 는 10원 배수가 아니므로 올림하여 8,930)
    for price in (8_930, 9_450, 9_980, 10_500):
        for d in step(price):
            assert isinstance(d, SellStage)
            sold_order.append(d.stage_no)

    assert sold_order == [4, 3, 2, 1], "체결가가 낮은 단계가 먼저 목표에 닿는다"
    assert held_qty(states) == 0
    assert is_cycle_complete(states) is True

    closed = close(cycle, reason=CloseReason.NORMAL, at=clock.now(), states=states)
    assert closed.status is CycleStatus.CLOSED
    assert closed.close_reason is CloseReason.NORMAL
    assert orders == 7, "매수 3건 + 매도 4건"


def test_no_activity_outside_market_hours():
    lad = _ladder()
    clock = FakeClock(current=T0)
    clock.set_market_open(False)
    states = _initial_states(lad)
    states[0] = to_holding(to_buy_pending(states[0]), fill_price=10_000,
                           fill_qty=100, at=T0)
    cycle = confirm_anchor(
        start(Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE), at=T0),
        anchor_price=10_000, ladder=lad, at=T0,
    )
    for price in (9_500, 8_000, 12_000):
        assert decide(
            tick=Tick(code="005930", price=price, at=clock.now(),
                      source=TickSource.WS),
            cycle=cycle, states=states, params=TriggerParams(target_pct=FIVE),
            now=clock.now(), market_open=clock.is_market_open(),
            stock_code="005930",
        ) == []


def test_total_limit_stops_further_buys():
    """한도에 걸리면 판정은 나오지만 guard 가 막는다."""
    lad = _ladder()
    states = _initial_states(lad)
    states[0] = to_holding(to_buy_pending(states[0]), fill_price=10_000,
                           fill_qty=100, at=T0)
    cycle = confirm_anchor(
        start(Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE), at=T0),
        anchor_price=10_000, ladder=lad, at=T0,
    )
    decisions = decide(
        tick=Tick(code="005930", price=9_500, at=T0, source=TickSource.WS),
        cycle=cycle, states=states, params=TriggerParams(target_pct=FIVE),
        now=T0, market_open=True, stock_code="005930",
    )
    assert len(decisions) == 1
    ctx = GuardContext(stock_invested=6_900_000, stock_limit=7_000_000,
                       total_invested=6_900_000, total_limit=21_000_000,
                       orders_last_minute=0)
    verdict = check_buy(decisions[0], ctx)  # type: ignore[arg-type]
    assert verdict.allowed is False
    assert "종목 총한도" in verdict.reason


def test_domain_imports_nothing_external():
    """설계서 7.2절 의존 규칙 — domain 은 표준 라이브러리만 쓴다."""
    import ast
    import pathlib
    import sys

    stdlib = set(sys.stdlib_module_names)
    domain_dir = pathlib.Path(__file__).parent.parent / "src" / "autotrading7s" / "domain"
    offenders: list[str] = []

    for path in domain_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if root and root not in stdlib and root != "autotrading7s":
                    offenders.append(f"{path.name}: {name}")

    assert offenders == [], f"domain 이 외부 모듈을 import 한다: {offenders}"
