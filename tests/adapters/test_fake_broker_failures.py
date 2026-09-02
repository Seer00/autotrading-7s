from __future__ import annotations

from uuid import uuid4

import pytest

from autotrading7s.adapters.fake.broker import (
    BrokerDisconnected,
    BrokerRejected,
    BrokerTimeout,
    FailMode,
    FakeBroker,
    FillMode,
)
from autotrading7s.domain.types import FillState, LimitOrderRequest, MarketSellRequest, Side

pytestmark = pytest.mark.asyncio


def a_buy(price: int = 9_500, qty: int = 105) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.BUY, qty=qty, price=price,
                             client_ref=uuid4())


def an_emergency_sell(qty: int = 100) -> MarketSellRequest:
    return MarketSellRequest(code="005930", qty=qty, client_ref=uuid4(),
                             reason="긴급")


async def test_timeout_raises():
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(a_buy())


async def test_timeout_does_not_inherit_timeout_error():
    """asyncio.wait_for 의 TimeoutError 와 구분되어야 한다 — 다른 사건이다."""
    assert not issubclass(BrokerTimeout, TimeoutError)


async def test_a_timed_out_order_was_still_accepted():
    """설계서 9절 ⑤ 의 핵심. 응답이 없어도 서버에 도달했을 수 있다 —
    그래서 재발주하지 않고 조회로 확인한다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT,
                        fill_mode=FillMode.NEVER)
    req = a_buy()
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(req)
    orders = await broker.list_orders_today("005930")
    assert [o.client_ref for o in orders] == [req.client_ref]


async def test_client_ref_lets_us_tell_accepted_from_not_accepted():
    """두 주문 중 하나만 타임아웃했을 때 어느 것이 접수됐는지 구별할 수 있어야 한다."""
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        fail_mode=FailMode.TIMEOUT, fail_after=1)
    first = a_buy()
    second = a_buy(price=9_000)
    await broker.place_limit_order(first)      # 성공
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(second)  # 타임아웃 — 그래도 접수됨
    refs = {o.client_ref for o in await broker.list_orders_today("005930")}
    assert refs == {first.client_ref, second.client_ref}


async def test_fail_after_lets_the_first_n_calls_succeed():
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT, fail_after=2)
    await broker.place_limit_order(a_buy())
    await broker.place_limit_order(a_buy())
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(a_buy())


async def test_reject_raises_and_does_not_accept_the_order():
    """명시적 거부는 타임아웃과 다르다 — 주문이 접수되지 않았음이 확실하다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.REJECT)
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(a_buy())
    assert await broker.list_orders_today("005930") == []


async def test_reject_carries_a_code_and_message():
    """설계서 12.1절의 order_log.api_code·api_message 에 기록될 값이다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.REJECT)
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_limit_order(a_buy())
    assert exc.value.code
    assert exc.value.message


async def test_reject_leaves_the_balance_untouched():
    broker = FakeBroker([9_500], fail_mode=FailMode.REJECT, cash=10_000_000)
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(a_buy())
    balance = await broker.get_balance()
    assert balance.cash == 10_000_000
    assert balance.qty_of("005930") == 0


async def test_disconnect_ends_the_stream_with_an_exception():
    """설계서 8.4절 — WebSocket 끊김. 모의투자로는 만들 수 없다."""
    broker = FakeBroker([9_500, 9_000, 8_500], fail_mode=FailMode.DISCONNECT,
                        fail_after=2)
    seen = []
    with pytest.raises(BrokerDisconnected):
        async for tick in broker.subscribe_quotes(["005930"]):
            seen.append(tick.price)
    assert seen == [9_500, 9_000]


async def test_resubscribing_after_a_disconnect_resumes_the_script():
    """재연결 후 구독 복원. 설계서 8.3절이 이것을 빠뜨리면 조용한 실패가 된다고 적었다."""
    broker = FakeBroker([9_500, 9_000, 8_500], fail_mode=FailMode.DISCONNECT,
                        fail_after=2)
    with pytest.raises(BrokerDisconnected):
        async for _ in broker.subscribe_quotes(["005930"]):
            pass
    broker.clear_failure()
    rest = [t.price async for t in broker.subscribe_quotes(["005930"])]
    assert rest == [8_500]


async def test_clear_failure_restores_normal_ordering():
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT,
                        fill_mode=FillMode.INSTANT)
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(a_buy())
    broker.clear_failure()
    ack = await broker.place_limit_order(a_buy())
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.FILLED


async def test_get_balance_can_also_fail():
    """대사(설계서 10.2절)가 잔고 조회 실패를 다뤄야 하므로 만들 수 있어야 한다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerTimeout):
        await broker.get_balance()


async def test_failure_modes_are_deterministic():
    async def run() -> int:
        broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT, fail_after=1)
        await broker.place_limit_order(a_buy())
        try:
            await broker.place_limit_order(a_buy())
        except BrokerTimeout:
            return len(await broker.list_orders_today("005930"))
        return -1

    assert await run() == await run() == 2


async def test_emergency_market_sell_fills_immediately_even_in_never_mode():
    """설계서 6절 — 긴급 기능의 즉시성. `_accept` 의 `price is None` 단축은
    시장가 매도가 fill_mode 를 우회하게 만든다. Task 11 의 테스트는 이것을
    INSTANT 모드로만 돌려서 두 조건을 구분하지 못했다 — NEVER 모드에서도
    긴급 시장가 매도가 즉시 전량 체결됨을 못박는다."""
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER, cash=10_000_000)
    ack = await broker.place_market_sell(an_emergency_sell(qty=100))
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.FILLED
    assert status.filled_qty == 100
