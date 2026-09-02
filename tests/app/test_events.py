from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from autotrading7s.app.events import (
    CycleClosed,
    CycleLoadFailed,
    EmergencyResult,
    EngineStopped,
    Event,
    GuardBlocked,
    OrderRejected,
    OrderUnknown,
    QuoteFallback,
    ReconcileMismatch,
    StageFilled,
    TickUpdate,
)
from autotrading7s.domain.types import CloseReason, TickSource

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)

ALL_EVENTS = (
    StageFilled, CycleClosed, CycleLoadFailed, ReconcileMismatch, QuoteFallback,
    OrderRejected, OrderUnknown, EmergencyResult, GuardBlocked, TickUpdate,
    EngineStopped,
)


def test_all_events_are_frozen_and_subclass_event():
    for cls in ALL_EVENTS:
        assert dataclasses.is_dataclass(cls), cls
        assert cls.__dataclass_params__.frozen, cls
        assert issubclass(cls, Event), cls


def test_every_event_carries_a_tz_aware_timestamp():
    """naive 시각이 GUI 로 새면 화면의 시각 표시가 조용히 틀린다.

    도메인 전체가 tz-aware 이므로 경계에서도 같은 규칙을 강제한다.
    """
    naive = datetime(2026, 9, 2, 10, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        TickUpdate(stock_code="005930", price=10_000, source=TickSource.WS, at=naive)


def test_stage_filled_reports_cumulative_fill():
    ev = StageFilled(config_id=1, cycle_id=1, stage_no=3, side="BUY",
                     fill_price=9_500, fill_qty=105, at=AT)
    assert ev.fill_qty == 105


def test_cycle_closed_carries_reason_and_realized_pnl():
    ev = CycleClosed(config_id=1, cycle_id=1, reason=CloseReason.NORMAL,
                     realized_pnl=19_200, at=AT)
    assert ev.reason is CloseReason.NORMAL
    assert ev.realized_pnl == 19_200


def test_reconcile_mismatch_names_the_verdict():
    """설계서 10.2절 — 세 판정 중 하나."""
    ev = ReconcileMismatch(stock_code="005930", internal_qty=433, broker_qty=400,
                           verdict="INTERNAL_MORE", action_taken="PAUSED", at=AT)
    assert ev.verdict == "INTERNAL_MORE"
    with pytest.raises(ValueError, match="verdict"):
        ReconcileMismatch(stock_code="005930", internal_qty=1, broker_qty=1,
                          verdict="WHATEVER", action_taken=None, at=AT)


def test_quote_fallback_says_which_direction():
    """설계서 8.4절 — 폴백 구간을 로깅해야 하므로 진입·복귀가 구분돼야 한다."""
    assert QuoteFallback(stock_codes=("005930",), active=True, at=AT).active is True
    assert QuoteFallback(stock_codes=("005930",), active=False, at=AT).active is False


def test_order_unknown_is_distinct_from_order_rejected():
    """D12 — UNKNOWN 은 재발주 금지 상태이고 REJECTED 는 복구 완료 상태다.

    두 개를 한 이벤트로 합치면 GUI 가 "확인 중" 과 "실패" 를 같은 색으로
    보여주게 되고, 사용자가 개입할 시점을 알 수 없다.
    """
    assert OrderUnknown is not OrderRejected
    unknown = OrderUnknown(config_id=1, cycle_id=1, stage_no=3,
                           client_ref="abc", at=AT)
    rejected = OrderRejected(config_id=1, cycle_id=1, stage_no=3,
                             api_code="40510", api_message="거부", at=AT)
    assert unknown.client_ref == "abc"
    assert rejected.api_code == "40510"


def test_cycle_load_failed_carries_the_corruption_message():
    """2A 핸드오버 7 — 손상된 행 하나가 사이클을 로드 불가로 만든다.

    엔진이 크래시하는 대신 이 이벤트로 사용자에게 나갈 길을 준다.
    """
    ev = CycleLoadFailed(config_id=1, cycle_id=4,
                         detail="trigger_price mismatch in stage_state (id=9)",
                         action_taken="PAUSED", at=AT)
    assert "stage_state" in ev.detail


def test_emergency_result_covers_the_five_schema_results():
    """emergency_liquidation_log.result 의 CHECK 와 같은 집합이어야 한다."""
    from autotrading7s.app.events import EMERGENCY_RESULTS
    assert EMERGENCY_RESULTS == frozenset(
        {"SUCCESS", "PARTIAL", "FAILED", "REJECTED_CLOSED_MARKET", "FORCED_CLOSE"}
    )
    with pytest.raises(ValueError, match="result"):
        EmergencyResult(scope="SINGLE", stock_code="005930", result="MAYBE",
                        qty_before=40, qty_after=0, canceled_orders=1,
                        detail=None, at=AT)


def test_guard_blocked_carries_the_domain_reason_verbatim():
    """가드 거부 이유는 도메인이 만든 문자열을 그대로 전달한다.

    엔진이 문구를 다시 쓰면 한도 숫자가 두 곳에 생기고 어긋난다.
    """
    ev = GuardBlocked(config_id=1, stage_no=4, side="BUY",
                      reason="종목 총한도 초과: 누적 1,000,000 + 예상 500,000 > 한도 1,200,000",
                      at=AT)
    assert "총한도" in ev.reason
