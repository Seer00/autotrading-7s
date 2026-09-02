from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.domain.types import (
    FillState,
    LimitOrderRequest,
    MarketSellRequest,
    Side,
)
from autotrading7s.ports.broker import BrokerPort


def a_buy(price: int = 9_500, qty: int = 105) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.BUY, qty=qty, price=price,
                             client_ref=uuid4())


def a_sell(price: int = 10_500, qty: int = 100) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.SELL, qty=qty, price=price,
                             client_ref=uuid4())


def test_satisfies_the_broker_port():
    assert isinstance(FakeBroker([9_500]), BrokerPort)


@pytest.mark.asyncio
async def test_subscribe_replays_the_script_in_order():
    broker = FakeBroker([9_500, 9_000, 8_500])
    prices = [tick.price async for tick in broker.subscribe_quotes(["005930"])]
    assert prices == [9_500, 9_000, 8_500]


@pytest.mark.asyncio
async def test_ticks_carry_the_code_and_an_aware_timestamp():
    broker = FakeBroker([9_500])
    ticks = [t async for t in broker.subscribe_quotes(["005930"])]
    assert ticks[0].code == "005930"
    assert ticks[0].at.tzinfo is not None


@pytest.mark.asyncio
async def test_get_price_returns_the_last_replayed_tick():
    broker = FakeBroker([9_500, 9_000])
    async for _ in broker.subscribe_quotes(["005930"]):
        pass
    assert await broker.get_price("005930") == 9_000


@pytest.mark.asyncio
async def test_get_price_before_any_tick_uses_the_first_script_entry():
    broker = FakeBroker([9_500, 9_000])
    assert await broker.get_price("005930") == 9_500


@pytest.mark.asyncio
async def test_instant_fill_completes_immediately():
    broker = FakeBroker([9_500], fill_mode=FillMode.INSTANT)
    req = a_buy()
    ack = await broker.place_limit_order(req)
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.FILLED
    assert status.filled_qty == req.qty
    assert status.filled_price == req.price
    assert status.client_ref == req.client_ref


@pytest.mark.asyncio
async def test_instant_fill_updates_the_balance():
    broker = FakeBroker([9_500], fill_mode=FillMode.INSTANT, cash=10_000_000)
    await broker.place_limit_order(a_buy(price=9_500, qty=105))
    balance = await broker.get_balance()
    assert balance.qty_of("005930") == 105
    assert balance.cash == 10_000_000 - 9_500 * 105


@pytest.mark.asyncio
async def test_selling_reduces_the_position_and_adds_cash():
    broker = FakeBroker([10_500], fill_mode=FillMode.INSTANT, cash=10_000_000)
    await broker.place_limit_order(a_buy(price=9_500, qty=105))
    await broker.place_limit_order(a_sell(price=10_500, qty=105))
    balance = await broker.get_balance()
    assert balance.qty_of("005930") == 0
    assert balance.cash == 10_000_000 - 9_500 * 105 + 10_500 * 105


@pytest.mark.asyncio
async def test_never_mode_leaves_the_order_open():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    ack = await broker.place_limit_order(a_buy())
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.OPEN
    assert status.filled_qty == 0


@pytest.mark.asyncio
async def test_partial_mode_fills_the_configured_ratio():
    broker = FakeBroker([9_500], fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"))
    ack = await broker.place_limit_order(a_buy(qty=105))
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.PARTIAL
    assert status.filled_qty == 42          # floor(105 × 0.4)
    assert status.filled_qty < 105


@pytest.mark.asyncio
async def test_partial_mode_never_fills_zero():
    """수량이 작아 floor 가 0 이 되면 최소 1주는 체결한다 — 0주 부분체결은
    도메인의 StageState 불변식이 거부하는 상태를 만든다."""
    broker = FakeBroker([9_500], fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"))
    ack = await broker.place_limit_order(a_buy(qty=1))
    status = await broker.get_order(ack.broker_order_id)
    assert status.filled_qty == 1
    assert status.state is FillState.FILLED


@pytest.mark.asyncio
async def test_delayed_mode_fills_after_the_configured_tick_count():
    """delay_ticks 는 실제 시간이 아니라 소비된 틱 수로 센다 — 결정론을 위해서다."""
    broker = FakeBroker([9_500, 9_400, 9_300, 9_200],
                        fill_mode=FillMode.DELAYED, delay_ticks=2)
    ack = await broker.place_limit_order(a_buy())
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.OPEN
    consumed = 0
    async for _ in broker.subscribe_quotes(["005930"]):
        consumed += 1
        if consumed == 2:
            break
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.FILLED


@pytest.mark.asyncio
async def test_cancel_moves_an_open_order_to_canceled():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    ack = await broker.place_limit_order(a_buy())
    cancel = await broker.cancel_order(ack.broker_order_id)
    assert cancel.broker_order_id == ack.broker_order_id
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.CANCELED


@pytest.mark.asyncio
async def test_cancel_of_a_filled_order_is_refused():
    broker = FakeBroker([9_500], fill_mode=FillMode.INSTANT)
    ack = await broker.place_limit_order(a_buy())
    with pytest.raises(ValueError, match="already"):
        await broker.cancel_order(ack.broker_order_id)


@pytest.mark.asyncio
async def test_market_sell_fills_at_the_current_price():
    broker = FakeBroker([9_340], fill_mode=FillMode.INSTANT, cash=0)
    await broker.place_limit_order(a_buy(price=10_000, qty=100))
    ack = await broker.place_market_sell(MarketSellRequest(
        code="005930", qty=100, client_ref=uuid4(), reason="긴급"))
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.FILLED
    assert status.filled_price == 9_340


@pytest.mark.asyncio
async def test_list_orders_today_includes_every_order():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    first = await broker.place_limit_order(a_buy())
    second = await broker.place_limit_order(a_buy(price=9_000))
    orders = await broker.list_orders_today("005930")
    assert {o.broker_order_id for o in orders} == {
        first.broker_order_id, second.broker_order_id}


@pytest.mark.asyncio
async def test_list_orders_today_filters_by_code():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    await broker.place_limit_order(a_buy())
    assert await broker.list_orders_today("035720") == []
    assert len(await broker.list_orders_today(None)) == 1


@pytest.mark.asyncio
async def test_client_ref_survives_so_unknown_reconciliation_works():
    """설계서 9절 ⑤ — 응답 타임아웃 후 client_ref 로 접수 여부를 확인한다."""
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    req = a_buy()
    await broker.place_limit_order(req)
    orders = await broker.list_orders_today("005930")
    assert [o.client_ref for o in orders] == [req.client_ref]


@pytest.mark.asyncio
async def test_the_same_script_and_mode_gives_the_same_result_twice():
    """결정론 — 난수도 시간 의존도 없다."""
    async def run() -> tuple[int, ...]:
        broker = FakeBroker([9_500, 9_000], fill_mode=FillMode.PARTIAL)
        ack = await broker.place_limit_order(a_buy(qty=105))
        status = await broker.get_order(ack.broker_order_id)
        return (status.filled_qty, status.filled_price or 0)

    assert await run() == await run()
