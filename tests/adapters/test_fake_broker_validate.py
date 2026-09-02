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
async def test_timeout_still_registers_a_valid_order():
    """설계서 9절 ⑤ 의 "접수됨" 분기 — 유효한 주문은 등록한 뒤 던진다.

    엔진이 list_orders_today 로 접수를 확인하고 체결을 기다리는 경로가 이
    조합에서 나온다.
    """
    broker = FakeBroker([10_000], cash=100_000_000, validate_account=True,
                        fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert len(await broker.list_orders_today("005930")) == 1


@pytest.mark.asyncio
async def test_timeout_does_not_bypass_account_validation():
    """검증이 전송 실패보다 먼저 온다 — 배경 보안 리뷰가 지적한 우회.

    타임아웃은 "응답이 유실됐다" 는 뜻이고 "거래소가 받아줬다" 는 뜻이
    아니다. 검증을 나중에 두면 TIMEOUT 을 주입한 모든 시나리오에서
    validate_account 가 조용히 무력화되고, 예수금 1원으로 1,000,000원 주문이
    등록·체결된다. **G2 시나리오 7(응답 타임아웃)이 정확히 그 조합이므로**,
    그 게이트가 한도를 전혀 검사하지 않게 된다.

    거부된 주문은 등록되지 않아야 한다. 등록되면 엔진의 UNKNOWN 조회가
    "접수됨" 으로 오판하고 있지도 않은 주문의 체결을 기다린다.
    """
    broker = FakeBroker([10_000], cash=1, validate_account=True,
                        fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert exc.value.code == "40940"
    assert await broker.list_orders_today("005930") == []
    # get_balance 는 TIMEOUT 모드에서 실패하도록 2A 가 만들어 뒀으므로
    # (설계서 10.2절 대사가 잔고 조회 실패를 다뤄야 한다) 모드를 해제한 뒤 본다
    broker.clear_failure()
    assert (await broker.get_balance()).cash == 1


@pytest.mark.asyncio
async def test_validation_failure_does_not_consume_the_fail_budget():
    """검증에 걸린 주문은 전송 계층에 도달하지 못했다.

    카운터를 소모하면 `fail_after` 가 "실패할 수 있었던 호출 N번" 이라는
    의미를 잃고, 실패 지점이 무관한 거부 건수에 따라 움직인다 — 2A 가
    get_balance 에 대해 이미 고친 결함과 같은 모양이다.
    """
    broker = FakeBroker([10_000], cash=1_000_000, validate_account=True,
                        fail_mode=FailMode.TIMEOUT, fail_after=1)
    # 보유가 없으므로 검증에서 거부된다 — 예산을 소모하지 않아야 한다
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(_sell(qty=10, price=10_000))
    # 첫 유효 주문은 fail_after=1 이므로 통과한다
    ack = await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert ack.broker_order_id
    # 두 번째 유효 주문에서 타임아웃이 난다
    broker._cash = 100_000_000
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(_buy(qty=100, price=10_000))


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
