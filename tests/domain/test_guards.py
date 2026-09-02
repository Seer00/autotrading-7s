from __future__ import annotations

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
