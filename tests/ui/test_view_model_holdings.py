from __future__ import annotations

import dataclasses

from autotrading7s.domain import pnl
from autotrading7s.domain.types import CycleStatus, StageStatus
from autotrading7s.ui.view_model import build_holdings, status_label

from .conftest import config, exhausted_config, idle_config, snapshot


def test_row_order_follows_the_snapshot(three_row_snapshot):
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert [r.stock_code for r in view.rows] == ["005930", "035720", "035420"]


def test_row_carries_config_id_so_buttons_can_send_commands(three_row_snapshot):
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert [r.config_id for r in view.rows] == [1, 2, 3]


def test_quantities_and_average_price_come_from_domain_pnl(three_row_snapshot):
    """설계서 14.4절 — 표시용 계산조차 domain/pnl.py 를 호출한다.

    목업의 `316주 / 9,458원` 이 그대로 나온다.
    """
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    samsung = view.rows[0]
    stages = three_row_snapshot.configs[0].stages
    assert samsung.held_qty == pnl.held_qty(stages) == 316
    assert samsung.avg_price == pnl.avg_price(stages) == 9_458
    assert samsung.holding_stages == pnl.holding_stage_count(stages) == 3
    assert samsung.max_stages == 7


def test_pnl_is_none_until_a_price_arrives(three_row_snapshot):
    """첫 틱 전에는 평가손익을 알 수 없다 — 0 으로 보여주면 안 된다."""
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert view.rows[0].current_price is None
    assert view.rows[0].pnl is None
    assert view.rows[0].pnl_pct is None


def test_pnl_uses_domain_pnl_with_the_latest_price(three_row_snapshot):
    """목업: 현재가 9,340 에서 `-1.25% / -37,410원`."""
    stages = three_row_snapshot.configs[0].stages
    view = build_holdings(three_row_snapshot, prices={"005930": 9_340},
                          mismatched_codes=())
    row = view.rows[0]
    assert row.current_price == 9_340
    assert row.pnl == pnl.unrealized_pnl(stages, 9_340) == -37_410
    assert row.pnl_pct == pnl.unrealized_pnl_pct(stages, 9_340)


def test_an_idle_config_shows_zero_held_and_no_average(three_row_snapshot):
    """보유가 없으면 평균단가는 None 이다 — 0 원으로 보여주면 안 된다."""
    view = build_holdings(three_row_snapshot, prices={"035420": 161_200},
                          mismatched_codes=())
    naver = view.rows[2]
    assert naver.held_qty == 0
    assert naver.avg_price is None
    assert naver.pnl is None
    assert naver.current_price == 161_200
    assert (naver.holding_stages, naver.max_stages) == (0, 5)


# ── 상태 표기 (설계서 14.1절) ───────────────────────────────────────────
def test_status_labels_cover_the_six_documented_values():
    assert status_label(idle_config(), mismatched=False) == "IDLE"
    assert status_label(config(), mismatched=False) == "감시"
    assert status_label(exhausted_config(), mismatched=False) == "소진"
    paused = dataclasses.replace(config(), cycle_status=CycleStatus.PAUSED)
    assert status_label(paused, mismatched=False) == "일시정지"
    liquidating = dataclasses.replace(config(),
                                      cycle_status=CycleStatus.LIQUIDATING)
    assert status_label(liquidating, mismatched=False) == "청산중"
    assert status_label(config(), mismatched=True) == "⚠불일치"


def test_mismatch_overrides_every_other_label():
    """대사 불일치는 사용자가 가장 먼저 알아야 하는 것이다.

    그 상태에서 사이클은 이미 PAUSED 이므로 "일시정지" 는 같은 사실의 덜
    중요한 절반이다.
    """
    paused = dataclasses.replace(config(), cycle_status=CycleStatus.PAUSED)
    assert status_label(paused, mismatched=True) == "⚠불일치"
    assert status_label(idle_config(), mismatched=True) == "⚠불일치"


def test_starting_reads_as_watching():
    """설계서는 여섯 표기만 규정한다. STARTING 은 한 틱만 지속되며 사용자가
    보기엔 "시작을 눌렀고 감시 중" 이다."""
    starting = dataclasses.replace(config(), cycle_status=CycleStatus.STARTING,
                                   stages=(), ladder=None, anchor_price=None)
    assert status_label(starting, mismatched=False) == "감시"


def test_exhausted_needs_every_stage_holding():
    """`소진` 은 전 단계 보유다 — 6/7 은 아직 감시 중이다."""
    full = exhausted_config()
    last_waiting = dataclasses.replace(
        full.stages[6], status=StageStatus.WAITING,
        fill_price=None, fill_qty=None, bought_at=None)
    six = dataclasses.replace(full, stages=full.stages[:6] + (last_waiting,))
    assert status_label(six, mismatched=False) == "감시"


# ── 합계 ────────────────────────────────────────────────────────────────
def test_totals_sum_invested_and_valuation(three_row_snapshot):
    prices = {"005930": 9_340, "035720": 7_910}
    view = build_holdings(three_row_snapshot, prices=prices,
                          mismatched_codes=())
    invested = sum(pnl.invested_amount(c.stages)
                   for c in three_row_snapshot.configs)
    assert view.totals.invested == invested
    valuation = sum(pnl.held_qty(c.stages) * prices[c.stock_code]
                    for c in three_row_snapshot.configs
                    if c.stock_code in prices)
    assert view.totals.valuation == valuation
    assert view.totals.pnl == valuation - invested


def test_totals_exclude_stocks_without_a_price_and_say_so(three_row_snapshot):
    """투입금액으로 대체하면 손익 0 으로 보여 사용자가 반영됐다고 믿는다.

    기동 직후와 장 시작 전에 정확히 그 상태가 된다.
    """
    view = build_holdings(three_row_snapshot, prices={"005930": 9_340},
                          mismatched_codes=())
    assert view.totals.missing_prices == ("035720",)
    stages = three_row_snapshot.configs[0].stages
    assert view.totals.valuation == pnl.held_qty(stages) * 9_340
    assert view.totals.invested == pnl.invested_amount(stages)


def test_a_stock_with_no_holdings_is_not_a_missing_price(three_row_snapshot):
    """NAVER 는 보유가 0 이므로 가격이 없어도 합계에 영향이 없다."""
    view = build_holdings(three_row_snapshot,
                          prices={"005930": 9_340, "035720": 7_910},
                          mismatched_codes=())
    assert view.totals.missing_prices == ()


def test_totals_pct_is_none_when_nothing_is_invested():
    view = build_holdings(snapshot(idle_config()), prices={},
                          mismatched_codes=())
    assert view.totals.invested == 0
    assert view.totals.pnl_pct is None


def test_broker_average_notice_is_present(three_row_snapshot):
    """설계서 2.1절 — 증권사 앱의 평균단가와 다르다는 안내가 화면에 있어야 한다.

    이 문구가 없으면 사용자가 두 숫자를 비교하고 프로그램이 틀렸다고 판단한다.
    """
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert "증권사" in view.broker_avg_notice
    assert "단계별 체결가" in view.broker_avg_notice
