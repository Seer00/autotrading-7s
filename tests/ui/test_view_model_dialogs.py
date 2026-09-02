from __future__ import annotations

import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrading7s.app.commands import (
    FORCE_CLOSE_CONFIRMATION,
    LIQUIDATE_ALL_CONFIRMATION,
)
from autotrading7s.app.events import ReconcileMismatch
from autotrading7s.domain import pnl
from autotrading7s.ui.view_model import (
    build_banner,
    build_emergency_view,
    build_force_close_view,
    build_status_bar,
)

from .conftest import AT, config, idle_config


# ── 14.3 긴급청산 다이얼로그 ────────────────────────────────────────────
def test_emergency_view_reproduces_the_mockup():
    """설계서 14.3절 목업: 보유 316주(3개 단계), 현재가 9,340,
    예상금액 2,951,440, 평균단가 9,458, 예상손익 -37,410 (-1.25%)."""
    view = build_emergency_view(config(), current_price=9_340, scope="SINGLE")
    assert view.stock_code == "005930"
    assert view.held_qty == 316
    assert view.holding_stages == 3
    assert view.current_price == 9_340
    assert view.estimated_amount == 2_951_440
    assert view.avg_price == 9_458
    assert view.estimated_pnl == -37_410
    assert view.estimated_pnl_pct == Decimal("-1.25")


def test_emergency_view_delegates_to_domain_pnl():
    """설계서 14.4절 — 표시용 계산조차 domain/pnl.py 를 호출한다."""
    stages = config().stages
    view = build_emergency_view(config(), current_price=9_340, scope="SINGLE")
    assert view.estimated_pnl == pnl.unrealized_pnl(stages, 9_340)
    assert view.estimated_pnl_pct == pnl.unrealized_pnl_pct(stages, 9_340)
    assert view.avg_price == pnl.avg_price(stages)


def test_emergency_view_announces_orders_that_will_be_canceled():
    """설계서 14.3절 — `미체결 매수주문 2건이 함께 취소됩니다`.

    ②를 빠뜨리면 긴급청산이 무력화된다는 것이 설계서 11.1절의 경고이고,
    사용자가 그것이 함께 일어난다는 것을 알아야 한다.
    """
    view = build_emergency_view(dataclasses.replace(config(),
                                                     pending_orders=2),
                                current_price=9_340, scope="SINGLE")
    assert view.pending_orders == 2


def test_single_scope_needs_no_text_confirmation():
    view = build_emergency_view(config(), current_price=9_340, scope="SINGLE")
    assert view.required_text is None


def test_all_scope_requires_the_exact_confirmation_text():
    """설계서 11.2절 — 전체 청산은 `전체청산` 을 직접 입력해야 한다.

    상수를 명령 모듈에서 가져오므로 어긋날 수 없다 — 어긋나면 사용자가
    정확히 입력했는데 버튼이 활성화되지 않는다.
    """
    view = build_emergency_view(config(), current_price=9_340, scope="ALL")
    assert view.required_text == LIQUIDATE_ALL_CONFIRMATION == "전체청산"


def test_emergency_view_without_a_price_has_no_estimate():
    """현재가를 모르면 예상금액을 추측하지 않는다 — 사용자가 그 숫자를 근거로
    실행 여부를 판단한다."""
    view = build_emergency_view(config(), current_price=None, scope="SINGLE")
    assert view.current_price is None
    assert view.estimated_amount is None
    assert view.estimated_pnl is None
    assert view.estimated_pnl_pct is None
    assert view.avg_price == 9_458        # 평균단가는 현재가와 무관하다


def test_emergency_view_rejects_a_config_with_nothing_to_sell():
    """팔 것이 없는 종목에 긴급청산 다이얼로그를 띄우면 사용자를 오도한다."""
    with pytest.raises(ValueError, match="보유"):
        build_emergency_view(idle_config(), current_price=161_200,
                             scope="SINGLE")


# ── 11.4 강제 종료 다이얼로그 ───────────────────────────────────────────
def test_force_close_view_shows_the_remainder_and_the_attempts():
    """설계서 11.4절 — `남은 보유 40주 (보유 단계 1개)`, `청산 시도 3회, 마지막 15:28`."""
    view = build_force_close_view(
        config(), attempts=3, last_attempt_at=AT + timedelta(hours=6),
        last_failure_detail="거래정지 (API 응답 코드 40510)")
    assert view.remaining_qty == 316
    assert view.holding_stages == 3
    assert view.attempts == 3
    assert view.last_attempt_at == AT + timedelta(hours=6)
    assert "거래정지" in view.last_failure_detail


def test_force_close_view_requires_the_exact_confirmation_text():
    view = build_force_close_view(config(), attempts=1, last_attempt_at=AT,
                                  last_failure_detail=None)
    assert view.required_text == FORCE_CLOSE_CONFIRMATION == "강제종료"


def test_force_close_view_rejects_a_config_with_no_remainder():
    """잔량 0 의 강제 종료는 의미가 없다 (설계서 11.4절 절차 ③).

    엔진도 그것을 정상 종료로 처리하므로, 다이얼로그가 애초에 뜨면 안 된다.
    """
    with pytest.raises(ValueError, match="잔량"):
        build_force_close_view(idle_config(), attempts=1, last_attempt_at=AT,
                               last_failure_detail=None)


# ── 상태바 (설계서 14.1절 하단) ─────────────────────────────────────────
def test_status_bar_shows_the_quote_source():
    ws = build_status_bar(fallback_active=False, last_reconcile=None,
                          total_used=9_971_350, total_limit=21_000_000)
    assert "WebSocket" in ws.quote_source_label
    rest = build_status_bar(fallback_active=True, last_reconcile=None,
                            total_used=0, total_limit=1)
    assert "폴백" in rest.quote_source_label


def test_status_bar_shows_limit_usage():
    """목업: `총한도 9,971,350 / 21,000,000 (47%)`."""
    bar = build_status_bar(fallback_active=False, last_reconcile=None,
                           total_used=9_971_350, total_limit=21_000_000)
    assert bar.total_used == 9_971_350
    assert bar.total_limit == 21_000_000
    assert bar.used_pct == Decimal("47.5")


def test_status_bar_handles_a_zero_limit_without_dividing():
    """한도 설정 전이나 잘못된 설정에서 그 상태가 된다."""
    bar = build_status_bar(fallback_active=False, last_reconcile=None,
                           total_used=0, total_limit=0)
    assert bar.used_pct is None


def test_status_bar_reports_the_last_reconcile():
    """목업: `대사 09:40 일치`. 불일치면 그 사실이 보여야 한다."""
    quiet = build_status_bar(fallback_active=False, last_reconcile=None,
                             total_used=0, total_limit=1)
    assert "일치" in quiet.last_reconcile_label
    mismatch = ReconcileMismatch(stock_code="005930", internal_qty=316,
                                 broker_qty=300, verdict="INTERNAL_MORE",
                                 action_taken="PAUSED", at=AT)
    noisy = build_status_bar(fallback_active=False, last_reconcile=mismatch,
                             total_used=0, total_limit=1)
    assert "005930" in noisy.last_reconcile_label
    assert "INTERNAL_MORE" in noisy.last_reconcile_label


# ── 배너 (설계서 14.1절 상단) ───────────────────────────────────────────
def test_banner_distinguishes_mock_from_real():
    """실전 프로파일은 붉은 `▣ 실전투자` 다 — 색은 위젯이 `is_real` 로 정한다.

    이 구분이 흐려지면 사용자가 실전 계좌에서 시험한다.
    """
    mock = build_banner(env="mock", fallback_active=False, engine_error=None)
    assert mock.env_label == "▣ 모의투자"
    assert mock.is_real is False
    real = build_banner(env="real", fallback_active=False, engine_error=None)
    assert real.env_label == "▣ 실전투자"
    assert real.is_real is True


def test_banner_rejects_an_unknown_environment():
    """조용히 모의투자로 떨어지면 사용자가 실전이라고 믿는 채로 돌린다."""
    with pytest.raises(ValueError, match="env"):
        build_banner(env="prod", fallback_active=False, engine_error=None)


def test_banner_shows_the_connection_state():
    ws = build_banner(env="mock", fallback_active=False, engine_error=None)
    assert "WS" in ws.connection_label
    rest = build_banner(env="mock", fallback_active=True, engine_error=None)
    assert "폴백" in rest.connection_label


def test_banner_surfaces_a_dead_engine():
    """조용히 죽은 엔진은 "프로그램이 켜져 있는데 트리거를 놓치는" 최악의
    상태다 (설계서 18.1 리스크 6)."""
    dead = build_banner(env="mock", fallback_active=False,
                        engine_error="RuntimeError: 복구 실패")
    assert dead.engine_error is not None
    assert "복구 실패" in dead.engine_error
