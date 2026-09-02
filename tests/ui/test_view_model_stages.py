from __future__ import annotations

import dataclasses
from decimal import Decimal

from autotrading7s.domain.ladder import target_price
from autotrading7s.domain.types import StageStatus
from autotrading7s.ui.view_model import build_stage_detail

from .conftest import PCT, config, idle_config, ladder, stages_of


def test_header_identifies_the_cycle():
    """설계서 14.1절 — `단계별 상세 — 삼성전자 / 기본 (사이클 #2, 앵커 10,000원…)`."""
    view = build_stage_detail(config(), current_price=9_340)
    assert (view.stock_name, view.label) == ("삼성전자", "기본")
    assert view.cycle_seq == 2
    assert view.anchor_price == 10_000
    assert view.started_at is not None
    assert view.config_id == 1


def test_one_row_per_stage_in_ascending_order():
    view = build_stage_detail(config(), current_price=9_340)
    assert [r.stage_no for r in view.rows] == [1, 2, 3, 4, 5, 6, 7]


def test_holding_rows_reproduce_the_mockup_targets():
    """목업의 목표가 열: 1단계 10,500 / 2단계 9,960 / 3단계 9,400.

    체결가 기준 올림이므로 9,480 × 1.05 = 9,954 → 9,960 이다.
    """
    view = build_stage_detail(config(), current_price=9_340)
    assert [r.target_price for r in view.rows[:3]] == [10_500, 9_960, 9_400]
    assert [r.fill_price for r in view.rows[:3]] == [10_000, 9_480, 8_950]
    assert [r.fill_qty for r in view.rows[:3]] == [100, 105, 111]
    assert all(r.status_label == "보유" for r in view.rows[:3])


def test_waiting_rows_have_no_fill_or_target():
    view = build_stage_detail(config(), current_price=9_340)
    fourth = view.rows[3]
    assert fourth.status_label == "대기"
    assert (fourth.fill_price, fourth.fill_qty, fourth.target_price) == (
        None, None, None)


def test_gap_reproduces_the_mockup_numbers_exactly():
    """목업의 "목표까지 / 매수까지" 열 네 줄이 그대로 나온다.

    1단계 `▲ +12.4% (1,160원)`, 2단계 `+6.6% (620원)`, 3단계 `+0.6% (60원)`,
    4단계 `▼ -9.0%`. **분모가 현재가**이기 때문에 이 숫자가 된다 — 기준가로
    나누면 목업과 어긋나고, 그 어긋남은 사용자에게 "몇 % 남았는지" 의 오답이다.
    """
    view = build_stage_detail(config(), current_price=9_340)
    assert [r.gap_won for r in view.rows[:4]] == [1_160, 620, 60, -840]
    assert [r.gap_pct for r in view.rows[:4]] == [
        Decimal("12.4"), Decimal("6.6"), Decimal("0.6"), Decimal("-9.0")]
    assert [r.gap_kind for r in view.rows[:4]] == [
        "TARGET", "TARGET", "TARGET", "TRIGGER"]


def test_the_gap_column_carries_both_meanings_in_one_field():
    """설계서 1.1절 5항 — 같은 열에 방향 기호로 두 의미를 담는다.

    한 열만 훑어도 다음에 무슨 일이 일어날지 알 수 있어야 한다.
    """
    view = build_stage_detail(config(), current_price=9_340)
    kinds = {r.gap_kind for r in view.rows}
    assert kinds == {"TARGET", "TRIGGER"}
    assert all(r.gap_pct is not None for r in view.rows)


def test_gap_is_none_without_a_price():
    view = build_stage_detail(config(), current_price=None)
    assert all(r.gap_pct is None and r.gap_won is None for r in view.rows)
    assert all(r.gap_kind is None for r in view.rows)


def test_sold_stage_has_no_gap_but_keeps_its_rebuy_count():
    """`SOLD` 인 순간에는 쿨다운이 끝나기 전이므로 "하락 시 매수" 가 사실이 아니다."""
    lad = ladder(10_000)
    sold = dataclasses.replace(config(), stages=stages_of(lad, sold=(1,)))
    view = build_stage_detail(sold, current_price=9_340)
    row = view.rows[0]
    assert row.status_label == "매도완료"
    assert row.gap_kind is None and row.gap_pct is None
    assert row.rebuy_count == 1


def test_buy_pending_has_no_gap():
    """이미 주문이 나갔으므로 "몇 % 남았는가" 가 답이 아니다."""
    lad = ladder(10_000)
    stages = list(stages_of(lad))
    stages[3] = dataclasses.replace(stages[3], status=StageStatus.BUY_PENDING)
    view = build_stage_detail(dataclasses.replace(config(),
                                                  stages=tuple(stages)),
                              current_price=9_340)
    assert view.rows[3].status_label == "매수대기"
    assert view.rows[3].gap_kind is None


def test_sell_pending_still_shows_its_target():
    """매도대기는 목표가로 주문이 나간 상태다 — 목표가가 사라지면 사용자가
    무슨 가격에 팔리는지 알 수 없다."""
    lad = ladder(10_000)
    stages = list(stages_of(lad, holding={1: (10_000, 100)}))
    stages[0] = dataclasses.replace(stages[0],
                                    status=StageStatus.SELL_PENDING)
    view = build_stage_detail(dataclasses.replace(config(),
                                                  stages=tuple(stages)),
                              current_price=9_340)
    assert view.rows[0].status_label == "매도대기"
    assert view.rows[0].target_price == target_price(10_000, PCT)
    assert view.rows[0].gap_kind == "TARGET"


def test_an_idle_config_has_no_stage_rows():
    view = build_stage_detail(idle_config(), current_price=161_200)
    assert view.rows == ()
    assert view.anchor_price is None
    assert view.cycle_seq is None
