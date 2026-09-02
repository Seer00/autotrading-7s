from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FailMode, FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import Event, OrderRejected, OrderUnknown
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.domain.types import StageStatus, Tick, TickSource
from autotrading7s.engine.executor import Executor

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _tick(price: int, source: TickSource = TickSource.WS) -> Tick:
    return Tick(code="005930", price=price, at=AT, source=source)


def _executor(repo, broker):
    events: list[Event] = []
    ex = Executor(repo=repo, broker=broker, clock=FakeClock(current=AT),
                  emit=events.append)
    return ex, events


def _waiting_stage(repo, cycle_id, stage_no=2):
    return next(s for s in repo.load_stages(cycle_id) if s.stage_no == stage_no)


@pytest.mark.asyncio
async def test_records_the_order_before_placing_it(repo_two_stocks):
    """설계서 9절 ③④ — 발주보다 먼저 기록하고 커밋한다.

    'SENDING 행이 존재한 시점' 을 직접 관측하려면 브로커를 발주 시점에
    멈춰야 한다. place_limit_order 를 감싼 스파이가 그때 DB 를 읽는다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], validate_account=True, cash=100_000_000)
    seen: dict[str, object] = {}
    original = broker.place_limit_order

    async def spy(req):
        rows = repo_two_stocks.load_pending_orders()
        seen["pending"] = [(r.status, r.req_qty) for r in rows]
        seen["stage_status"] = _waiting_stage(
            repo_two_stocks, cyc.cycle_id).status
        return await original(req)

    broker.place_limit_order = spy            # type: ignore[method-assign]
    ex, _ = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    await ex.send(cycle=cyc, config=config, stage=stage,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="테스트"),
                  tick=_tick(9_500))

    assert seen["pending"] == [("SENDING", 52)]
    assert seen["stage_status"] is StageStatus.BUY_PENDING


@pytest.mark.asyncio
async def test_accepted_order_records_broker_id_and_leaves_stage_pending(
    repo_two_stocks,
):
    """⑤ 성공 — 체결 반영은 별도 단계이므로 단계는 BUY_PENDING 에 머문다."""
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "ACCEPTED"
    assert outcome.broker_order_id == "FAKE-1"
    assert outcome.stage.status is StageStatus.BUY_PENDING
    rows = repo_two_stocks.load_pending_orders()
    assert [(r.status, r.broker_order_id) for r in rows] == [("ACCEPTED", "FAKE-1")]
    assert events == []


@pytest.mark.asyncio
async def test_explicit_rejection_restores_the_stage_to_waiting(repo_two_stocks):
    """⑤ 명시적 거부 — 단계를 WAITING 으로 복구하고 이벤트를 낸다."""
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], validate_account=True, cash=100_000_000,
                        fail_mode=FailMode.REJECT)
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "REJECTED"
    assert outcome.stage.status is StageStatus.WAITING
    assert _waiting_stage(repo_two_stocks, cyc.cycle_id).status is StageStatus.WAITING
    assert repo_two_stocks.load_pending_orders() == []
    assert [type(e) for e in events] == [OrderRejected]
    assert events[0].api_code == "40510"


@pytest.mark.asyncio
async def test_timeout_confirms_acceptance_by_query_and_does_not_resend(
    repo_two_stocks,
):
    """⑤ UNKNOWN — 접수됨. **이 시스템에서 가장 중요한 분기다.**

    FakeBroker 의 TIMEOUT 은 주문을 등록한 뒤 던진다. 재발주하면 같은 단계를
    두 번 사게 되므로, 유일하게 안전한 행동은 조회로 사실을 확인하는 것이다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000,
                        fail_mode=FailMode.TIMEOUT)
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "UNKNOWN_ACCEPTED"
    assert outcome.stage.status is StageStatus.BUY_PENDING
    # 주문은 정확히 하나여야 한다 — 재발주가 없었다는 직접 증거
    assert len(await broker.list_orders_today("005930")) == 1
    assert [r.status for r in repo_two_stocks.load_pending_orders()] == ["ACCEPTED"]
    assert [type(e) for e in events] == [OrderUnknown]


@pytest.mark.asyncio
async def test_timeout_with_no_trace_restores_the_stage(repo_two_stocks):
    """⑤ UNKNOWN — 미접수. 조회에 흔적이 없으면 WAITING 으로 복구한다."""
    from autotrading7s.ports.broker import BrokerTimeout

    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)

    async def lost(req):
        raise BrokerTimeout("no response (never reached the broker)")

    broker.place_limit_order = lost           # type: ignore[method-assign]
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "UNKNOWN_NOT_SENT"
    assert outcome.stage.status is StageStatus.WAITING
    # 미접수는 CANCELED 로 종결한다 — REJECTED 는 브로커의 명시적 판단용이다
    assert repo_two_stocks.load_pending_orders() == []


@pytest.mark.asyncio
async def test_unresolvable_timeout_leaves_the_stage_pending(repo_two_stocks):
    """확인 조회 자체가 실패하면 **되돌리지 않는다** (원장 Ruling 8).

    WAITING 으로 복구하면 다음 틱에 재발주되고, 그것이 정확히 D12 가 막는
    중복 주문이다. PENDING 으로 남기면 규칙 5 가 그 단계를 판정에서 제외하고
    재시작 복구가 같은 조회로 정정한다.
    """
    from autotrading7s.ports.broker import BrokerTimeout

    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000,
                        fail_mode=FailMode.TIMEOUT)

    async def unreachable(code):
        raise BrokerTimeout("query also timed out")

    broker.list_orders_today = unreachable    # type: ignore[method-assign]
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "UNKNOWN_UNRESOLVED"
    assert outcome.stage.status is StageStatus.BUY_PENDING
    assert [r.status for r in repo_two_stocks.load_pending_orders()] == ["UNKNOWN"]


@pytest.mark.asyncio
async def test_sell_send_restores_holding_with_the_same_qty_on_rejection(
    repo_two_stocks,
):
    """매도 발주 실패는 보유 수량을 건드리지 않아야 한다.

    cancel_sell 은 remaining_qty 를 요구한다. 발주 자체가 실패했으면 체결이
    0 이므로 원래 fill_qty 를 그대로 넘겨야 하며, 잘못 넘기면 보유가 조용히
    줄어든다 — 그 줄어든 수량이 이후 모든 목표가 계산의 근거가 된다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    holding = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.status is StageStatus.HOLDING)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (holding.fill_qty, 1_000_000)},
                        fail_mode=FailMode.REJECT)
    ex, events = _executor(repo_two_stocks, broker)

    outcome = await ex.send(
        cycle=cyc, config=config, stage=holding,
        decision=SellStage(stage_no=holding.stage_no, limit_price=10_500,
                           qty=holding.fill_qty, reason="r"),
        tick=_tick(10_500),
    )

    assert outcome.status == "REJECTED"
    assert outcome.stage.status is StageStatus.HOLDING
    assert outcome.stage.fill_qty == holding.fill_qty
    assert outcome.stage.fill_price == holding.fill_price
    assert [type(e) for e in events] == [OrderRejected]


@pytest.mark.asyncio
async def test_order_log_links_to_the_stage_row(repo_two_stocks):
    """재시작 복구가 주문을 단계로 되돌릴 수 있어야 한다 (설계서 10.1절 2)."""
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, _ = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    await ex.send(cycle=cyc, config=config, stage=stage,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=_tick(9_500))

    row = repo_two_stocks.load_pending_orders()[0]
    assert row.stage_state_id == repo_two_stocks.stage_row_id(cyc.cycle_id, 2)


@pytest.mark.asyncio
async def test_trigger_path_records_the_tick_that_caused_it(repo_two_stocks):
    """설계서 12.1절 order_log 의 tick_price·tick_source·trigger_reason.

    사후에 "왜 이 주문이 나갔는가" 를 답할 수 있어야 한다. 트리거 이유는
    도메인이 만든 문자열을 그대로 저장한다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, _ = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    await ex.send(cycle=cyc, config=config, stage=stage,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="2단계 발동가 9,500 도달"),
                  tick=_tick(9_480, TickSource.REST_POLL))

    row = repo_two_stocks._conn.execute(
        "SELECT trigger_reason, tick_price, tick_source, path, order_type "
        "FROM order_log"
    ).fetchone()
    assert dict(row) == {
        "trigger_reason": "2단계 발동가 9,500 도달",
        "tick_price": 9_480,
        "tick_source": "REST_POLL",
        "path": "TRIGGER",
        "order_type": "LIMIT",
    }


@pytest.mark.asyncio
async def test_executor_never_places_a_market_order(repo_two_stocks):
    """자동 트리거 경로는 시장가를 표현할 수 없다 (설계서 8.2절).

    executor 모듈이 MarketSellRequest 를 참조하지 않는 것으로 확인한다 —
    참조가 없으면 그 경로가 존재할 수 없다.
    """
    import inspect

    from autotrading7s.engine import executor as mod

    source = inspect.getsource(mod)
    assert "MarketSellRequest" not in source
    assert "place_market_sell" not in source
