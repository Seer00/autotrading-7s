from __future__ import annotations

from datetime import timedelta

from autotrading7s.app.events import (
    CommandFailed,
    ConfigRejected,
    ConfigSaved,
    CycleClosed,
    CycleLoadFailed,
    EmergencyResult,
    EngineStopped,
    GuardBlocked,
    OrderRejected,
    OrderUnknown,
    QuoteFallback,
    ReconcileMismatch,
    StageFilled,
    TickUpdate,
)
from autotrading7s.domain.types import CloseReason, TickSource
from autotrading7s.ui.presenter import Presenter

from .conftest import AT


def _presenter(env="mock") -> Presenter:
    return Presenter(env)


def _tick(code="005930", price=9_340):
    return TickUpdate(stock_code=code, price=price, source=TickSource.WS,
                      at=AT)


def _mismatch(code="005930"):
    return ReconcileMismatch(stock_code=code, internal_qty=316,
                             broker_qty=300, verdict="INTERNAL_MORE",
                             action_taken="PAUSED", at=AT)


def test_holdings_is_empty_before_the_first_snapshot():
    """기동 직후 스냅샷이 오기 전에도 화면을 그릴 수 있어야 한다."""
    view = _presenter().holdings()
    assert view.rows == ()
    assert view.totals.invested == 0


def test_snapshot_populates_the_holdings_view(three_row_snapshot):
    p = _presenter()
    p.consume(three_row_snapshot)
    assert [r.stock_code for r in p.holdings().rows] == [
        "005930", "035720", "035420"]


def test_ticks_feed_the_price_column(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick()])
    assert p.holdings().rows[0].current_price == 9_340
    assert p.holdings().rows[0].pnl == -37_410


def test_a_later_tick_replaces_an_earlier_one(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(price=9_340),
                   _tick(price=9_500)])
    assert p.holdings().rows[0].current_price == 9_500


def test_mismatch_marks_the_row_and_survives_new_snapshots(three_row_snapshot):
    """대사는 일치할 때 이벤트를 내지 않으므로 해소를 알 방법이 없다.

    새 스냅샷이 온다고 지우면, 5분마다 오는 다음 대사까지 경고가 사라져
    사용자가 그 사이에 아무 문제도 없다고 믿는다.
    """
    p = _presenter()
    p.consume_all([three_row_snapshot, _mismatch()])
    assert p.holdings().rows[0].status_label == "⚠불일치"

    p.consume(three_row_snapshot)
    assert p.holdings().rows[0].status_label == "⚠불일치"


def test_clear_mismatch_removes_the_warning(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _mismatch()])
    p.clear_mismatch("005930")
    assert p.holdings().rows[0].status_label != "⚠불일치"


def test_cycle_closed_clears_the_mismatch(three_row_snapshot):
    """사이클이 끝나면 그 불일치는 더 이상 이 사이클의 문제가 아니다."""
    p = _presenter()
    p.consume_all([three_row_snapshot, _mismatch()])
    p.consume(CycleClosed(config_id=1, cycle_id=2,
                          reason=CloseReason.NORMAL, realized_pnl=0, at=AT))
    assert p.holdings().rows[0].status_label != "⚠불일치"


def test_a_mismatch_on_one_stock_does_not_mark_another(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _mismatch("005930")])
    labels = [r.status_label for r in p.holdings().rows]
    assert labels[0] == "⚠불일치"
    assert labels[1] == "소진"


def test_quote_fallback_flows_to_the_banner_and_status_bar():
    p = _presenter()
    p.consume(QuoteFallback(stock_codes=("005930",), active=True, at=AT))
    assert "폴백" in p.banner().connection_label
    assert "폴백" in p.status_bar().quote_source_label

    p.consume(QuoteFallback(stock_codes=("005930",), active=False, at=AT))
    assert "WS" in p.banner().connection_label


def test_status_bar_limit_usage_comes_from_the_snapshot(three_row_snapshot):
    from autotrading7s.domain import pnl

    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(),
                   _tick(code="035720", price=7_910)])
    bar = p.status_bar()
    assert bar.total_limit == three_row_snapshot.total_limit
    assert bar.total_used == sum(pnl.invested_amount(c.stages)
                                 for c in three_row_snapshot.configs)


def test_stage_detail_selects_by_config_id(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick()])
    view = p.stage_detail(1)
    assert view is not None and view.stock_name == "삼성전자"
    assert view.rows[0].gap_kind == "TARGET"
    assert p.stage_detail(999) is None


def test_engine_error_reaches_the_banner():
    """설계서 18.1 리스크 6 — 조용히 죽은 엔진이 최악이다."""
    p = _presenter()
    p.consume(EngineStopped(detail="시세 재연결 3회 실패", at=AT))
    assert p.banner().engine_error is not None

    p2 = _presenter()
    p2.note_engine_error("RuntimeError: 복구 실패")
    assert "복구 실패" in p2.banner().engine_error


# ── 로그 뷰 (설계서 14.1절 [로그]) ──────────────────────────────────────
def test_order_unknown_and_rejected_are_different_kinds():
    """2B 핸드오버 4 — 같은 색으로 그리면 안 된다.

    문구만 담으면 위젯이 문자열을 검사하게 되고 그것은 사각지대의 로직이다.
    """
    p = _presenter()
    p.consume_all([
        OrderUnknown(config_id=1, cycle_id=2, stage_no=3, client_ref="abc",
                     at=AT),
        OrderRejected(config_id=1, cycle_id=2, stage_no=3, api_code="40510",
                      api_message="거부", at=AT),
    ])
    kinds = [line.kind for line in p.log_lines()]
    assert kinds == ["OrderUnknown", "OrderRejected"]
    assert len(set(kinds)) == 2
    # 문구도 서로 다른 사실을 말한다
    assert "재발주하지 않는다" in p.log_lines()[0].text
    assert "40510" in p.log_lines()[1].text


def test_severities_separate_warnings_from_information():
    p = _presenter()
    p.consume_all([
        StageFilled(config_id=1, cycle_id=2, stage_no=1, side="BUY",
                    fill_price=10_000, fill_qty=100, at=AT),
        GuardBlocked(config_id=1, stage_no=4, side="BUY",
                     reason="전체 총한도 초과: 누적 1 + 예상 2 > 한도 1", at=AT),
        OrderUnknown(config_id=1, cycle_id=2, stage_no=3, client_ref="a",
                     at=AT),
        CycleLoadFailed(config_id=1, cycle_id=2, detail="corrupt row",
                        action_taken="PAUSED", at=AT),
        CommandFailed(command="StartCycle", detail="KeyError: 9999", at=AT),
    ])
    by_kind = {line.kind: line.severity for line in p.log_lines()}
    assert by_kind["StageFilled"] == "INFO"
    assert by_kind["GuardBlocked"] == "INFO"
    assert by_kind["OrderUnknown"] == "WARN"
    assert by_kind["CommandFailed"] == "WARN"
    assert by_kind["CycleLoadFailed"] == "ERROR"


def test_guard_reason_is_carried_verbatim():
    """2B 핸드오버 7 — 도메인이 만든 문장을 다시 쓰지 않는다.

    다시 쓰면 한도 숫자의 서식이 두 곳에 생기고 도메인 테스트가 고정한 문구와
    화면의 문구가 어긋난다.
    """
    reason = "종목 총한도 초과: 누적 1,000,000 + 예상 500,000 > 한도 1,200,000"
    p = _presenter()
    p.consume(GuardBlocked(config_id=1, stage_no=4, side="BUY", reason=reason,
                           at=AT))
    assert p.log_lines()[0].text == reason


def test_log_is_bounded():
    """장중 내내 돌면 로그가 메모리를 먹는다."""
    p = Presenter("mock", log_capacity=10)
    for i in range(50):
        p.consume(StageFilled(config_id=1, cycle_id=2, stage_no=1, side="BUY",
                              fill_price=10_000, fill_qty=100,
                              at=AT + timedelta(seconds=i)))
    assert len(p.log_lines()) == 10
    assert p.log_lines()[-1].at == AT + timedelta(seconds=49)


def test_snapshots_and_ticks_do_not_flood_the_log(three_row_snapshot):
    """스냅샷과 틱은 초당 여러 번 온다 — 로그에 넣으면 사용자가 읽을 수 없다."""
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(), _tick(price=9_400)])
    assert p.log_lines() == ()


# ── 설정 저장 피드백 ────────────────────────────────────────────────────
def test_config_feedback_is_taken_once():
    """다이얼로그가 한 번 읽고 지운다 — 남아 있으면 다음에 열 때 옛 오류가 뜬다."""
    p = _presenter()
    p.consume(ConfigRejected(config_id=None, detail="max_stages must be 2..7",
                             at=AT))
    feedback = p.take_config_feedback()
    assert feedback is not None and feedback.ok is False
    assert "max_stages" in feedback.detail
    assert p.take_config_feedback() is None


def test_config_saved_feedback_carries_the_id():
    p = _presenter()
    p.consume(ConfigSaved(config_id=7, at=AT))
    feedback = p.take_config_feedback()
    assert feedback.ok is True and feedback.config_id == 7


# ── 긴급청산·강제 종료 다이얼로그 ───────────────────────────────────────
def test_emergency_attempts_accumulate_from_events(three_row_snapshot):
    """설계서 11.4절 — `청산 시도 3회, 마지막 15:28`.

    그 이력은 emergency_liquidation_log 에 있고 GUI 는 DB 를 읽을 수 없으므로
    프레젠터가 이벤트에서 누적한다. 강제 종료는 청산 실패 직후에 하는 일이므로
    같은 세션이 정상 경로다.
    """
    p = _presenter()
    p.consume(three_row_snapshot)
    for i in range(3):
        p.consume(EmergencyResult(
            scope="SINGLE", stock_code="005930", result="FAILED",
            qty_before=316, qty_after=316, canceled_orders=0,
            detail="거래정지", at=AT + timedelta(minutes=i)))

    view = p.force_close(1)
    assert view.attempts == 3
    assert view.last_attempt_at == AT + timedelta(minutes=2)
    assert "거래정지" in view.last_failure_detail


def test_successful_liquidation_does_not_count_as_a_failed_attempt(
    three_row_snapshot,
):
    """성공한 청산을 시도 횟수에 넣으면 강제 종료 다이얼로그의 근거가 흐려진다."""
    p = _presenter()
    p.consume(three_row_snapshot)
    p.consume(EmergencyResult(scope="SINGLE", stock_code="005930",
                              result="FAILED", qty_before=316, qty_after=316,
                              canceled_orders=0, detail="거래정지", at=AT))
    p.consume(EmergencyResult(scope="SINGLE", stock_code="005930",
                              result="SUCCESS", qty_before=316, qty_after=0,
                              canceled_orders=1, detail=None, at=AT))
    view = p.force_close(1)
    assert view.attempts == 0


def test_emergency_view_uses_the_latest_price(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(price=9_340)])
    view = p.emergency(1, scope="SINGLE")
    assert view.current_price == 9_340
    assert view.estimated_amount == 2_951_440


def test_dialogs_return_none_for_an_unknown_config(three_row_snapshot):
    p = _presenter()
    p.consume(three_row_snapshot)
    assert p.emergency(999, scope="SINGLE") is None
    assert p.force_close(999) is None


def test_dialogs_return_none_when_there_is_nothing_to_sell(three_row_snapshot):
    """뷰모델이 ValueError 를 내는 경우를 프레젠터가 None 으로 바꾼다 —
    위젯이 예외를 처리하게 하면 그 처리 코드가 사각지대에 들어간다."""
    p = _presenter()
    p.consume(three_row_snapshot)
    assert p.emergency(3, scope="SINGLE") is None      # NAVER, 보유 0
    assert p.force_close(3) is None


def test_dialogs_return_none_before_the_first_snapshot():
    p = _presenter()
    assert p.emergency(1, scope="SINGLE") is None
    assert p.force_close(1) is None
    assert p.stage_detail(1) is None
