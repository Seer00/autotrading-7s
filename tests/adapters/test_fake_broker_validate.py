from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from autotrading7s.adapters.fake.broker import (
    BrokerRejected,
    BrokerTimeout,
    FailMode,
    FakeBroker,
    FillMode,
)
from autotrading7s.domain.types import LimitOrderRequest, MarketSellRequest, Side


def _buy(qty: int, price: int) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.BUY, qty=qty, price=price,
                             client_ref=uuid.uuid4())


def _sell(qty: int, price: int) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.SELL, qty=qty, price=price,
                             client_ref=uuid.uuid4())


@pytest.mark.asyncio
async def test_validation_is_off_by_default_and_cash_still_goes_negative():
    """2A 의 동작을 그대로 고정한다 — 기본값을 바꾸지 않았음을 증명한다."""
    broker = FakeBroker([10_000], cash=1_000)
    await broker.place_limit_order(_buy(qty=100, price=10_000))
    balance = await broker.get_balance()
    assert balance.cash < 0


@pytest.mark.asyncio
async def test_rejects_buy_beyond_cash_when_validating():
    broker = FakeBroker([10_000], cash=999_999, validate_account=True)
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert exc.value.code == "40940"
    assert "예수금" in exc.value.message


@pytest.mark.asyncio
async def test_rejected_order_is_not_registered():
    """미접수가 확실해야 한다 — 설계서 9절 ⑤의 '명시적 거부' 분기.

    등록해두면 재시작 복구가 당일 주문 조회에서 그것을 찾아내고, 실제로는
    없는 주문을 근거로 단계 상태를 정정한다.
    """
    broker = FakeBroker([10_000], cash=1, validate_account=True)
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert await broker.list_orders_today("005930") == []


@pytest.mark.asyncio
async def test_allows_buy_exactly_at_cash():
    broker = FakeBroker([10_000], cash=1_000_000, validate_account=True)
    ack = await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert ack.broker_order_id
    assert (await broker.get_balance()).cash == 0


@pytest.mark.asyncio
async def test_rejects_sell_beyond_position_when_validating():
    broker = FakeBroker([10_000], validate_account=True)
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_limit_order(_sell(qty=1, price=10_000))
    assert exc.value.code == "40950"
    assert "보유수량" in exc.value.message


@pytest.mark.asyncio
async def test_sell_of_exactly_the_position_succeeds():
    broker = FakeBroker([10_000], validate_account=True)
    await broker.place_limit_order(_buy(qty=100, price=10_000))
    ack = await broker.place_limit_order(_sell(qty=100, price=10_000))
    assert ack.broker_order_id
    assert (await broker.get_balance()).qty_of("005930") == 0


@pytest.mark.asyncio
async def test_market_sell_is_validated_too():
    """긴급청산 경로도 없는 포지션을 팔 수 없다.

    이것이 검증되지 않으면 설계서 11.1절 ③(실계좌 수량으로 팔기)이 지켜지는지
    를 이 더블로 확인할 수 없다.
    """
    broker = FakeBroker([10_000], validate_account=True)
    req = MarketSellRequest(code="005930", qty=40, client_ref=uuid.uuid4(),
                            reason="긴급청산")
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_market_sell(req)
    assert exc.value.code == "40950"


@pytest.mark.asyncio
async def test_preexisting_holdings_can_be_sold():
    """엔진이 모르는 포지션 — 대사 불일치와 긴급청산 시나리오의 출발점."""
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (40, 400_000)})
    assert (await broker.get_balance()).qty_of("005930") == 40
    req = MarketSellRequest(code="005930", qty=40, client_ref=uuid.uuid4(),
                            reason="긴급청산")
    ack = await broker.place_market_sell(req)
    assert ack.broker_order_id
    assert (await broker.get_balance()).qty_of("005930") == 0


@pytest.mark.asyncio
async def test_transport_failure_wins_over_validation():
    """FailMode 는 거래소보다 앞단이다 — 타임아웃은 등록한 뒤 던진다.

    순서가 뒤집히면 fail_after 의 의미("실패할 수 있었던 호출 N번")가 깨진다.
    """
    broker = FakeBroker([10_000], cash=1, validate_account=True,
                        fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert len(await broker.list_orders_today("005930")) == 1


@pytest.mark.asyncio
async def test_validation_does_not_change_partial_fill_behaviour():
    """PARTIAL 모드에서 검증 기준은 요청 수량이다, 체결 수량이 아니다.

    체결 수량으로 검증하면 부분체결로 조금씩 팔아 없는 포지션을 비울 수 있다.
    """
    broker = FakeBroker([10_000], validate_account=True,
                        fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"),
                        holdings={"005930": (10, 100_000)})
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(_sell(qty=100, price=10_000))
