from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from autotrading7s.domain.types import (
    Balance,
    FillState,
    Holding,
    LimitOrderRequest,
    MarketSellRequest,
    Side,
    Tick,
    TickSource,
)


def _now() -> datetime:
    return datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def test_tick_is_frozen():
    tick = Tick(code="005930", price=9340, at=_now(), source=TickSource.WS)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tick.price = 9350  # type: ignore[misc]


def test_limit_order_request_has_no_credit_fields():
    """설계서 6절: 신용·미수 필드가 타입에 존재하지 않아야 한다."""
    names = {f.name for f in dataclasses.fields(LimitOrderRequest)}
    assert names == {"code", "side", "qty", "price", "client_ref"}
    forbidden = {"credit", "credit_type", "loan", "loan_type", "margin", "misu"}
    assert names & forbidden == set()


def test_limit_order_request_price_is_mandatory():
    """설계서 8.2절: 자동 트리거 경로는 시장가를 표현할 수 없다."""
    with pytest.raises(TypeError):
        LimitOrderRequest(  # type: ignore[call-arg]
            code="005930", side=Side.BUY, qty=100, client_ref=uuid4()
        )


def test_limit_order_request_rejects_nonpositive():
    with pytest.raises(ValueError):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=0, price=9340,
                          client_ref=uuid4())
    with pytest.raises(ValueError):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=100, price=0,
                          client_ref=uuid4())


def test_market_sell_request_requires_reason():
    """설계서 8.2절: 사유 필드가 필수여서 로깅을 빼먹을 수 없다."""
    with pytest.raises(TypeError):
        MarketSellRequest(code="005930", qty=316, client_ref=uuid4())  # type: ignore[call-arg]

    req = MarketSellRequest(code="005930", qty=316, client_ref=uuid4(),
                            reason="실적 쇼크")
    assert req.reason == "실적 쇼크"


def test_market_sell_request_allows_empty_reason():
    """사용자 입력은 선택이므로 빈 문자열은 허용한다 (설계서 14.3절)."""
    req = MarketSellRequest(code="005930", qty=1, client_ref=uuid4(), reason="")
    assert req.reason == ""


def test_limit_order_request_rejects_float_qty():
    """설계서 3.1절: float은 금액 계산에 쓸 수 없다."""
    with pytest.raises(TypeError, match="qty must be int"):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=100.5, price=9340,  # type: ignore[arg-type]
                          client_ref=uuid4())


def test_limit_order_request_rejects_float_price():
    """설계서 3.1절: float은 금액 계산에 쓸 수 없다."""
    with pytest.raises(TypeError, match="price must be int"):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=100, price=9340.5,  # type: ignore[arg-type]
                          client_ref=uuid4())


def test_limit_order_request_rejects_bool_qty():
    """bool은 int의 서브클래스지만 거절한다."""
    with pytest.raises(TypeError, match="qty must be int"):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=True,  # type: ignore[arg-type]
                          price=9340, client_ref=uuid4())


def test_limit_order_request_rejects_bool_price():
    """bool은 int의 서브클래스지만 거절한다."""
    with pytest.raises(TypeError, match="price must be int"):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=100, price=False,  # type: ignore[arg-type]
                          client_ref=uuid4())


def test_limit_order_request_rejects_decimal_price():
    """Decimal은 금액 계산에 쓸 수 있지만 이 타입에서는 거절한다."""
    with pytest.raises(TypeError, match="price must be int"):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=100, price=Decimal("9340.50"),  # type: ignore[arg-type]
                          client_ref=uuid4())


def test_market_sell_request_rejects_float_qty():
    """설계서 3.1절: float은 금액 계산에 쓸 수 없다."""
    with pytest.raises(TypeError, match="qty must be int"):
        MarketSellRequest(code="005930", qty=100.5, client_ref=uuid4(),  # type: ignore[arg-type]
                          reason="test")


def test_market_sell_request_rejects_bool_qty():
    """bool은 int의 서브클래스지만 거절한다."""
    with pytest.raises(TypeError, match="qty must be int"):
        MarketSellRequest(code="005930", qty=True, client_ref=uuid4(),  # type: ignore[arg-type]
                          reason="test")


def test_market_sell_request_has_no_credit_fields():
    """설계서 6절: 주문 DTO 는 복수다 — 긴급청산 경로도 신용·미수 필드가 없다."""
    names = {f.name for f in dataclasses.fields(MarketSellRequest)}
    assert names == {"code", "qty", "client_ref", "reason"}
    forbidden = {"credit", "credit_type", "loan", "loan_type", "margin", "misu"}
    assert names & forbidden == set()


def test_market_sell_request_rejects_nonpositive_qty():
    """수량 0 이하의 청산 주문은 존재할 수 없다."""
    with pytest.raises(ValueError, match="qty must be positive"):
        MarketSellRequest(code="005930", qty=0, client_ref=uuid4(), reason="test")
    with pytest.raises(ValueError, match="qty must be positive"):
        MarketSellRequest(code="005930", qty=-316, client_ref=uuid4(), reason="test")


@pytest.mark.parametrize("field", ["qty", "avg_price"])
@pytest.mark.parametrize("bad_value", [100.5, True, Decimal(100)])
def test_holding_rejects_non_int_fields(field: str, bad_value: object):
    """보유 수량은 MarketSellRequest.qty 로 흘러간다 — 설계서 11.1절은 그
    수량을 실제 계좌에서 가져오라고 요구하므로, 여기가 그 값의 경계다."""
    kwargs = {"code": "005930", "qty": 316, "avg_price": 9_458}
    kwargs[field] = bad_value
    with pytest.raises(TypeError, match=f"{field} must be int"):
        Holding(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["qty", "avg_price"])
def test_holding_rejects_negative_fields(field: str):
    kwargs = {"code": "005930", "qty": 316, "avg_price": 9_458}
    kwargs[field] = -1
    with pytest.raises(ValueError, match=f"{field} must be non-negative"):
        Holding(**kwargs)  # type: ignore[arg-type]


def test_holding_allows_zero_qty():
    """전량 매도된 종목이 잔고에 0주로 남아 오는 것은 정상이다."""
    holding = Holding(code="005930", qty=0, avg_price=0)
    assert holding.qty == 0


def test_balance_qty_of():
    bal = Balance(cash=1_000_000, holdings=(
        Holding(code="005930", qty=316, avg_price=9458),
        Holding(code="035720", qty=833, avg_price=8382),
    ))
    assert bal.qty_of("005930") == 316
    assert bal.qty_of("035720") == 833
    assert bal.qty_of("035420") == 0


def test_fill_state_members():
    assert {s.name for s in FillState} == {
        "OPEN", "PARTIAL", "FILLED", "CANCELED", "REJECTED"
    }
