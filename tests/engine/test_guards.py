from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.engine.guards import (
    GuardGate,
    OrderRateWindow,
    compute_exposure,
)

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


# ── 분당 주문 카운터 ────────────────────────────────────────────────────
def test_rate_window_counts_orders_inside_the_window():
    w = OrderRateWindow()
    for i in range(3):
        w.record(NOW + timedelta(seconds=i))
    assert w.count(NOW + timedelta(seconds=3)) == 3


def test_rate_window_drops_orders_exactly_at_the_boundary():
    """60초 전 주문은 '지난 1분'에 포함되지 않는다.

    경계를 명시하는 이유: 포함하면 max_orders_per_minute 가 실질적으로 1건
    좁아지고, 그 1건이 매도라면 손절 없는 전략에서 탈출이 한 틱 늦어진다.
    """
    w = OrderRateWindow()
    w.record(NOW)
    assert w.count(NOW + timedelta(seconds=59, milliseconds=999)) == 1
    assert w.count(NOW + timedelta(seconds=60)) == 0


def test_rate_window_rejects_naive_datetime():
    w = OrderRateWindow()
    with pytest.raises(ValueError, match="tz-aware"):
        w.record(datetime(2026, 9, 2, 10, 0))


# ── 노출금액 집계 ───────────────────────────────────────────────────────
def test_compute_exposure_sums_holding_cost_across_active_cycles(repo_two_stocks):
    """활성 사이클 전부의 보유 원가를 종목별로, 그리고 전체로 집계한다."""
    exposure = compute_exposure(repo_two_stocks)
    assert exposure.per_stock == {"005930": 1_000_000, "000660": 600_000}
    assert exposure.total == 1_600_000


def test_compute_exposure_excludes_sold_stages(repo_with_sold_stage):
    """매도 완료된 단계는 자본이 회수됐으므로 노출이 아니다.

    한도는 '동시 노출' 을 제한하는 장치다 — 누적 지출을 제한하는 것이라면
    재매수가 허용된 설정에서 한도가 영구적으로 소진된다.
    """
    exposure = compute_exposure(repo_with_sold_stage)
    assert exposure.per_stock == {"005930": 950_000}


def test_compute_exposure_ignores_closed_cycles(repo_with_closed_cycle):
    exposure = compute_exposure(repo_with_closed_cycle)
    assert exposure.total == 0
    assert exposure.per_stock == {}


# ── 가드 판정 ───────────────────────────────────────────────────────────
def test_check_buy_allows_exactly_at_the_limit(repo_two_stocks):
    """누적 + 예상 == 한도 는 허용된다 (도메인 check_buy 의 경계와 같다)."""
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=1_700_000))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    verdict = gate.check_buy(decision, stock_code="005930",
                             stock_limit=1_100_000, now=NOW)
    assert verdict.allowed is True


def test_check_buy_blocks_one_won_over_the_total_limit(repo_two_stocks):
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=1_699_999))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    verdict = gate.check_buy(decision, stock_code="005930",
                             stock_limit=99_999_999, now=NOW)
    assert verdict.allowed is False
    assert "전체 총한도" in verdict.reason


def test_check_buy_uses_the_right_stocks_exposure(repo_two_stocks):
    """종목별 한도는 그 종목의 노출만 봐야 한다.

    per_stock 조회에서 종목 코드를 잘못 쓰면 다른 종목의 노출로 판정하게
    되고, 한도가 조용히 어긋난다 — 이 프로그램의 유일한 보호장치가 틀린
    숫자로 동작한다는 뜻이다.
    """
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    assert gate.check_buy(decision, stock_code="000660",
                          stock_limit=700_000, now=NOW).allowed is True
    blocked = gate.check_buy(decision, stock_code="005930",
                             stock_limit=700_000, now=NOW)
    assert blocked.allowed is False
    assert "종목 총한도" in blocked.reason


def test_unknown_stock_has_zero_exposure(repo_two_stocks):
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999))
    decision = BuyStage(stage_no=1, limit_price=10_000, qty=10, reason="r")
    assert gate.check_buy(decision, stock_code="035720",
                          stock_limit=100_000, now=NOW).allowed is True


def test_record_order_shrinks_the_budget_within_one_tick(repo_two_stocks):
    """Plan 1 핸드오버 2 — 한 틱이 여러 매도를 낼 수 있다.

    check_sell 은 상태 없는 술어이므로, 결정과 결정 사이에 record_order 를
    부르지 않으면 분당 3건 제한에서 한 틱에 7건이 나간다. 그 7건은 실제로
    브로커에 도달하고 호출 제한에 걸려 일부가 조용히 실패한다.
    """
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999,
                                                     max_orders_per_minute=2))
    sells = [SellStage(stage_no=n, limit_price=10_000, qty=10, reason="r")
             for n in (4, 3, 2)]
    results = []
    for s in sells:
        verdict = gate.check_sell(s, now=NOW)
        results.append(verdict.allowed)
        if verdict.allowed:
            gate.record_order(NOW)
    assert results == [True, True, False]


def test_recorded_orders_expire_after_the_window(repo_two_stocks):
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999,
                                                     max_orders_per_minute=1))
    decision = SellStage(stage_no=4, limit_price=10_000, qty=10, reason="r")
    gate.record_order(NOW)
    assert gate.check_sell(decision, now=NOW + timedelta(seconds=30)).allowed is False
    assert gate.check_sell(decision, now=NOW + timedelta(seconds=60)).allowed is True


def test_verdict_reason_comes_from_the_domain_verbatim(repo_two_stocks):
    """가드 이유 문자열을 엔진이 다시 쓰지 않는다.

    다시 쓰면 한도 숫자의 서식이 두 곳에 생기고, GuardBlocked 이벤트로 화면에
    나가는 문구가 도메인 테스트가 고정한 것과 달라진다.
    """
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=1))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    verdict = gate.check_buy(decision, stock_code="005930",
                             stock_limit=99_999_999, now=NOW)
    assert verdict.reason.startswith("전체 총한도 초과: 누적 1,600,000")
