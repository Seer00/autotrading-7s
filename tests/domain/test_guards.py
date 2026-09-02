from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.guards import GuardContext, check_buy, check_sell
from autotrading7s.domain.rules import BuyStage, SellStage


def buy(price: int = 9_500, qty: int = 105) -> BuyStage:
    return BuyStage(stage_no=2, limit_price=price, qty=qty, reason="test")


def sell(price: int = 10_500, qty: int = 100) -> SellStage:
    return SellStage(stage_no=1, limit_price=price, qty=qty, reason="test")


def ctx(**over) -> GuardContext:
    kwargs = dict(
        stock_invested=0,
        stock_limit=7_000_000,
        total_invested=0,
        total_limit=21_000_000,
        orders_last_minute=0,
        max_orders_per_minute=10,
    )
    kwargs.update(over)
    return GuardContext(**kwargs)  # type: ignore[arg-type]


def test_allows_buy_within_limits():
    assert check_buy(buy(), ctx()).allowed is True


def test_stock_limit_exact_boundary_is_allowed():
    """한도와 정확히 같아지는 주문은 허용한다."""
    est = 9_500 * 105  # 997,500
    verdict = check_buy(buy(), ctx(stock_invested=7_000_000 - est))
    assert verdict.allowed is True


def test_stock_limit_exceeded_by_one_won_is_rejected():
    est = 9_500 * 105
    verdict = check_buy(buy(), ctx(stock_invested=7_000_000 - est + 1))
    assert verdict.allowed is False
    assert "종목 총한도" in verdict.reason


def test_total_limit_exceeded_is_rejected():
    est = 9_500 * 105
    verdict = check_buy(
        buy(), ctx(stock_limit=100_000_000, total_invested=21_000_000 - est + 1)
    )
    assert verdict.allowed is False
    assert "전체 총한도" in verdict.reason


def test_order_frequency_limit():
    assert check_buy(buy(), ctx(orders_last_minute=9)).allowed is True
    verdict = check_buy(buy(), ctx(orders_last_minute=10))
    assert verdict.allowed is False
    assert "주문 빈도" in verdict.reason


def test_sell_is_not_limited_by_investment_caps():
    """매도는 포지션을 줄이므로 총한도와 무관하다."""
    verdict = check_sell(sell(), ctx(stock_invested=999_999_999,
                                     total_invested=999_999_999))
    assert verdict.allowed is True


def test_sell_is_limited_by_order_frequency():
    verdict = check_sell(sell(), ctx(orders_last_minute=10))
    assert verdict.allowed is False
    assert "주문 빈도" in verdict.reason


def test_verdict_reason_is_always_present():
    """감사 추적성 — 허용된 경우에도 근거를 남긴다 (설계서 6절)."""
    assert check_buy(buy(), ctx()).reason != ""
    assert check_sell(sell(), ctx()).reason != ""


def test_reason_records_limit_usage():
    reason = check_buy(buy(), ctx(stock_invested=1_200_000)).reason
    assert "1200000" in reason.replace(",", "")
    assert "7000000" in reason.replace(",", "")


@pytest.mark.parametrize("bad", [-1, -1_000])
def test_rejects_negative_context(bad: int):
    with pytest.raises(ValueError):
        ctx(stock_invested=bad)


# Type validation tests (Fix Round 1)

@pytest.mark.parametrize("field_name", [
    "stock_invested", "stock_limit", "total_invested", "total_limit",
    "orders_last_minute", "max_orders_per_minute"
])
def test_rejects_float_inf_for_all_fields(field_name: str):
    """Each field rejects float('inf') with TypeError."""
    with pytest.raises(TypeError, match=f"{field_name} must be int"):
        ctx(**{field_name: float('inf')})


@pytest.mark.parametrize("field_name", [
    "stock_invested", "stock_limit", "total_invested", "total_limit",
    "orders_last_minute", "max_orders_per_minute"
])
def test_rejects_float_nan_for_all_fields(field_name: str):
    """Each field rejects float('nan') with TypeError."""
    with pytest.raises(TypeError, match=f"{field_name} must be int"):
        ctx(**{field_name: float('nan')})


def test_rejects_ordinary_float():
    """Ordinary float values (not inf/nan) also raise TypeError."""
    with pytest.raises(TypeError, match="stock_limit must be int"):
        ctx(stock_limit=7_000_000.0)


def test_rejects_decimal():
    """Decimal values raise TypeError."""
    with pytest.raises(TypeError, match="stock_limit must be int"):
        ctx(stock_limit=Decimal(7_000_000))


def test_rejects_bool():
    """bool raises TypeError (even though it's an int subclass)."""
    with pytest.raises(TypeError, match="stock_limit must be int"):
        ctx(stock_limit=True)


def test_allows_zero_investment_caps():
    """stock_limit=0 is a legitimate per-stock kill switch."""
    c = ctx(stock_limit=0)
    assert c.stock_limit == 0


def test_allows_zero_orders_per_minute():
    """max_orders_per_minute=0 is a legitimate frequency kill switch."""
    c = ctx(max_orders_per_minute=0)
    assert c.max_orders_per_minute == 0


def test_allows_total_limit_below_stock_limit():
    """total_limit < stock_limit is legitimate; the tighter limit binds."""
    c = ctx(stock_limit=10_000_000, total_limit=5_000_000)
    assert c.total_limit < c.stock_limit


### 결정 타입(BuyStage·SellStage)의 불변식 — guards 의 직접 입력이다.


@pytest.mark.parametrize("decision_type", [BuyStage, SellStage])
@pytest.mark.parametrize("field_name", ["stage_no", "limit_price", "qty"])
@pytest.mark.parametrize("bad_value", [0, -1, -100])
def test_decision_rejects_nonpositive_fields(
    decision_type: type, field_name: str, bad_value: int
):
    kwargs = {"stage_no": 2, "limit_price": 9_500, "qty": 105, "reason": "test"}
    kwargs[field_name] = bad_value
    with pytest.raises(ValueError, match=f"{field_name} must be positive"):
        decision_type(**kwargs)


@pytest.mark.parametrize("decision_type", [BuyStage, SellStage])
@pytest.mark.parametrize("field_name", ["stage_no", "limit_price", "qty"])
@pytest.mark.parametrize("bad_value", [9_500.0, True, Decimal(9_500)])
def test_decision_rejects_non_int_fields(
    decision_type: type, field_name: str, bad_value: object
):
    kwargs = {"stage_no": 2, "limit_price": 9_500, "qty": 105, "reason": "test"}
    kwargs[field_name] = bad_value
    with pytest.raises(TypeError, match=f"{field_name} must be int"):
        decision_type(**kwargs)


def test_rejects_bypass_via_negative_limit_price():
    """음수 지정가는 총한도를 무력화한다 — 결정이 만들어지는 시점에 막는다.

    ``limit_price=-100, qty=10`` 이면 예상 체결금액이 -1,000 이 되어
    ``누적 + 예상 > 한도`` 가 언제나 거짓이 된다. 손절매가 없는 이 전략에서
    총한도는 유일한 구조적 보호장치이므로, 이 값은 guards 에 닿기 전에
    존재할 수 없어야 한다.
    """
    with pytest.raises(ValueError, match="limit_price must be positive"):
        BuyStage(stage_no=2, limit_price=-100, qty=10, reason="cap bypass")


def test_rejects_zero_limit_price_market_order_encoding():
    """국내 증권사 API 에서 가격 0 은 시장가의 전선 표현이다.

    설계서 8.2절의 "자동 트리거 경로는 시장가를 표현할 수 없다" 는 제약이
    사슬 끝의 ``LimitOrderRequest`` 뿐 아니라 판정 경계에서도 성립해야 한다.
    """
    with pytest.raises(ValueError, match="limit_price must be positive"):
        BuyStage(stage_no=2, limit_price=0, qty=105, reason="market order")
    with pytest.raises(ValueError, match="limit_price must be positive"):
        SellStage(stage_no=2, limit_price=0, qty=105, reason="market order")


def test_rejects_bypass_via_float_inf_stock_limit():
    """The exact bypass scenario that would have allowed every buy."""
    with pytest.raises(TypeError, match="stock_limit must be int"):
        GuardContext(
            stock_invested=6_900_000,
            stock_limit=float('inf'),
            total_invested=0,
            total_limit=21_000_000,
            orders_last_minute=0,
            max_orders_per_minute=10,
        )
