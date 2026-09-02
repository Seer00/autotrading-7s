from __future__ import annotations

import ast
import inspect
from dataclasses import replace as dc_replace
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import CycleClosed, EmergencyResult, Event
from autotrading7s.domain.rules import BuyStage
from autotrading7s.domain.types import (
    Balance,
    CloseReason,
    CycleStatus,
    FillState,
    Holding,
    MarketSellRequest,
    StageStatus,
    Tick,
    TickSource,
)
from autotrading7s.engine.emergency import EmergencyHandler, broker_qty
from autotrading7s.engine.executor import Executor

AT = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)


def _make(repo, broker, *, market_open=True):
    clock = FakeClock(current=AT, market_open=market_open)
    events: list[Event] = []
    handler = EmergencyHandler(repo=repo, broker=broker, clock=clock,
                               emit=events.append)
    return handler, clock, events


def _cycle(repo):
    """005930 사이클 — 1단계가 10,000원 100주 보유 중이다."""
    return repo.load_active_cycles()[0]


# ── Balance 의 모호성 (Plan 1 핸드오버 3) ───────────────────────────────
def test_broker_qty_distinguishes_absent_from_zero():
    """`Balance.qty_of` 는 없는 종목에 0 을 반환한다.

    긴급청산은 두 상황을 구분해야 한다 — '응답에 없음'은 '보유 0'의 증거가
    아니다. 응답이 잘렸거나 조회가 실패했을 수 있고, 그 상태에서 사이클을
    닫으면 실계좌에 주식이 남은 채 프로그램이 손을 뗀다.
    """
    balance = Balance(cash=0, holdings=(Holding(code="000660", qty=0,
                                                avg_price=1),))
    assert balance.qty_of("005930") == 0        # 도메인의 산술용 답
    assert broker_qty(balance, "005930") is None
    assert broker_qty(balance, "000660") == 0


# ── 11.3절 장외 요청 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rejects_outside_market_hours(repo_two_stocks):
    """D16 — 시장가 주문은 장중에만 가능하다. 요청은 이력에 남는다."""
    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True)
    handler, _, events = _make(repo_two_stocks, broker, market_open=False)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "REJECTED_CLOSED_MARKET"
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.RUNNING)
    row = repo_two_stocks._conn.execute(
        "SELECT result FROM emergency_liquidation_log"
    ).fetchone()
    assert dict(row)["result"] == "REJECTED_CLOSED_MARKET"
    assert [type(e) for e in events] == [EmergencyResult]


# ── 11.1절 ② 미체결 취소 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cancels_open_buy_orders_before_selling(repo_two_stocks):
    """②를 빠뜨리면 긴급청산이 무력화된다.

    전량 매도 직후 살아 있던 매수 지정가가 체결되면 방금 다 팔았는데 다시
    보유가 생긴다. 급락 중이라면 그 확률이 오히려 높다.
    """
    cyc = _cycle(repo_two_stocks)
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000,
                        holdings={"005930": (100, 1_000_000)})
    ex = Executor(repo=repo_two_stocks, broker=broker,
                  clock=FakeClock(current=AT), emit=lambda e: None)
    waiting = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.stage_no == 2)
    await ex.send(cycle=cyc, config=config, stage=waiting,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))
    assert len(repo_two_stocks.load_pending_orders()) == 1

    broker._fill_mode = FillMode.INSTANT
    handler, _, events = _make(repo_two_stocks, broker)
    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.canceled_orders == 1
    assert repo_two_stocks.load_pending_orders() == []
    assert out.result == "SUCCESS"


# ── 11.1절 ③ 실계좌 수량 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sells_the_brokers_quantity_not_the_internal_one(repo_two_stocks):
    """내부 기록 100주, 실계좌 40주 → 40주를 팔아야 한다.

    내부 기록으로 팔면 브로커가 보유수량 부족으로 거부하고 청산이 실패한다.
    validate_account 가 켜져 있으므로 잘못된 수량은 조용히 통과하지 못한다.
    """
    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (40, 400_000)})
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="오작동 의심")

    assert out.result == "SUCCESS"
    assert out.qty_before == 40
    assert out.qty_after == 0
    row = repo_two_stocks._conn.execute(
        "SELECT req_qty, order_type, path FROM order_log WHERE path = 'EMERGENCY'"
    ).fetchone()
    assert dict(row) == {"req_qty": 40, "order_type": "MARKET",
                         "path": "EMERGENCY"}


@pytest.mark.asyncio
async def test_absent_from_balance_is_a_failure(repo_two_stocks):
    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True)   # 보유 없음
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "FAILED"
    assert "잔고" in out.detail
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)


@pytest.mark.asyncio
async def test_broker_zero_with_internal_holdings_is_a_failure(repo_two_stocks):
    """실계좌는 비었는데 내부 기록이 남은 경우 — 설계서 11.4절의 전제.

    팔 것이 없으므로 청산은 실패이고, 사이클을 LIQUIDATING 에 남겨 사용자가
    강제 종료를 선택할 수 있게 한다.
    """
    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (0, 0)})
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "FAILED"
    assert out.qty_before == 0
    assert "강제 종료" in out.detail
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)


# ── 11.1절 ⑤⑥⑦ ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_success_closes_the_cycle_and_idles_the_config(repo_two_stocks):
    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="오작동 의심")

    assert out.result == "SUCCESS"
    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.status is CycleStatus.CLOSED
    assert reloaded.close_reason is CloseReason.EMERGENCY
    assert all(s.status is StageStatus.SOLD
               for s in repo_two_stocks.load_stages(cyc.cycle_id))
    assert repo_two_stocks.load_config(cyc.config_id).status == "IDLE"
    assert "005930" not in {h.stock_code for h in repo_two_stocks.holdings()}
    assert [type(e) for e in events] == [EmergencyResult, CycleClosed]


@pytest.mark.asyncio
async def test_success_records_realized_pnl(repo_two_stocks):
    """2A 핸드오버 2 — 종료 시 집계값을 기록하는 것은 엔진의 몫이다."""
    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _, _ = _make(repo_two_stocks, broker)
    await handler.liquidate_single(cyc.config_id, reason="테스트")

    row = repo_two_stocks._conn.execute(
        "SELECT realized_pnl FROM cycle WHERE id = ?", (cyc.cycle_id,)
    ).fetchone()
    # 픽스처는 매수를 order_log 없이 시드했으므로 매도만 집계된다:
    # 100주 × 10,500원. 값 자체가 아니라 '기록되었다' 가 요점이다.
    assert dict(row)["realized_pnl"] == 1_050_000


@pytest.mark.asyncio
async def test_partial_market_fill_reports_partial_and_stays_liquidating(
    repo_two_stocks,
):
    """시장가가 부분체결로 남으면 자동 재시도하지 않는다.

    급락 중 자동 재시도 루프는 무한히 팔려 들 수 있다. 재시도인지 강제
    종료인지는 사용자의 선택이다.
    """
    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    original_get = broker.get_order

    async def partial(broker_order_id):
        status = await original_get(broker_order_id)
        return dc_replace(status, state=FillState.PARTIAL, filled_qty=40)

    broker.get_order = partial              # type: ignore[method-assign]
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "PARTIAL"
    assert out.qty_after == 60
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)
    assert repo_two_stocks.load_config(cyc.config_id).status != "IDLE"


@pytest.mark.asyncio
async def test_market_sell_rejection_is_a_failure(repo_two_stocks):
    """거래정지 등으로 시장가 매도가 거부되면 LIQUIDATING 에 남는다.

    그 상태가 설계서 11.4절 강제 종료의 전제다.
    """
    from autotrading7s.ports.broker import BrokerRejected

    cyc = _cycle(repo_two_stocks)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})

    async def halted(req: MarketSellRequest):
        raise BrokerRejected("40510", "거래정지")

    broker.place_market_sell = halted       # type: ignore[method-assign]
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "FAILED"
    assert "거래정지" in out.detail
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)


# ── 11.1절 전체 청산 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_liquidate_all_processes_stocks_sequentially(repo_two_stocks):
    """병렬 발주는 TR 호출 제한에 걸려 일부가 조용히 실패할 수 있다.

    순차 처리하면 각 종목의 결과가 개별 로그로 남고 중간에 실패해도 어디까지
    됐는지 명확하다.
    """
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000),
                                  "000660": (100, 600_000)})
    handler, _, events = _make(repo_two_stocks, broker)

    outcomes = await handler.liquidate_all(reason="전체 청산")

    assert [o.stock_code for o in outcomes] == ["005930", "000660"]
    assert all(o.result == "SUCCESS" for o in outcomes)
    rows = repo_two_stocks._conn.execute(
        "SELECT scope, stock_code FROM emergency_liquidation_log ORDER BY id"
    ).fetchall()
    assert [dict(r) for r in rows] == [
        {"scope": "ALL", "stock_code": "005930"},
        {"scope": "ALL", "stock_code": "000660"},
    ]


@pytest.mark.asyncio
async def test_liquidate_all_continues_after_one_failure(repo_two_stocks):
    """한 종목이 실패해도 나머지는 계속 청산한다."""
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"000660": (100, 600_000)})   # 005930 없음
    handler, _, events = _make(repo_two_stocks, broker)

    outcomes = await handler.liquidate_all(reason="전체 청산")

    assert [o.result for o in outcomes] == ["FAILED", "SUCCESS"]


# ── Plan 1 핸드오버 1 ───────────────────────────────────────────────────
def test_emergency_never_consults_guards():
    """긴급청산은 가드를 거치지 않는다.

    `max_orders_per_minute=0` 이 매도를 막게 되고, 그것은 손절 없는 전략의
    유일한 탈출구에 레이트 리미터를 거는 것이다. import 부재로 고정하는 이유:
    호출 부재는 미래의 수정으로 조용히 깨지지만, import 를 되살리려면 누군가
    이 테스트를 지워야 한다.
    """
    from autotrading7s.engine import emergency as mod

    tree = ast.parse(inspect.getsource(mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert "autotrading7s.engine.guards" not in imported
    assert "autotrading7s.domain.guards" not in imported
