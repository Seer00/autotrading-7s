from __future__ import annotations

from decimal import Decimal

from autotrading7s.ui.text_render import (
    display_width,
    format_gap,
    format_pct,
    format_won,
    pad,
    render_holdings,
    render_ladder_preview,
    render_stage_detail,
    render_status_bar,
    wrap_to_width,
)
from autotrading7s.ui.view_model import (
    build_holdings,
    build_ladder_preview,
    build_stage_detail,
    build_status_bar,
)

from .conftest import PCT, config, idle_config, snapshot


def _preview():
    return build_ladder_preview(anchor_price=9_340, max_stages=7,
                                drop_pct=PCT, target_pct=PCT,
                                amount_per_stage=1_000_000,
                                stock_limit=7_000_000)


# ── 폭과 서식 ───────────────────────────────────────────────────────────
def test_display_width_counts_hangul_as_two_columns():
    """`len("삼성전자") == 4` 지만 고정폭 터미널에서 8칸을 차지한다.

    쓰지 않으면 종목명이 있는 행만 표가 어긋나고, 그것은 화면을 본 사람만
    아는 결함이다.
    """
    assert display_width("삼성전자") == 8
    assert display_width("NAVER") == 5
    assert display_width("카카오뱅크") == 10
    assert display_width("") == 0


def test_pad_uses_display_width_not_character_count():
    assert display_width(pad("삼성전자", 12)) == 12
    assert display_width(pad("NAVER", 12)) == 12
    assert pad("NAVER", 12).startswith("NAVER")
    assert pad("NAVER", 12, align="right").endswith("NAVER")
    assert display_width(pad("3/7", 6, align="center")) == 6


def test_pad_does_not_truncate_silently():
    """잘라내면 종목명이 조용히 사라진다 — 넘치는 것이 낫다.

    대신 긴 안내문은 `wrap_to_width` 로 줄바꿈해 정렬을 지킨다.
    """
    assert pad("아주긴종목이름입니다", 4) == "아주긴종목이름입니다"


def test_wrap_keeps_every_line_within_the_width():
    text = ("증권사 앱의 평균단가는 종목 전체 1개 값이고, 본 프로그램의 "
            "단계별 체결가는 내부 가상 넘버링 기준입니다.")
    lines = wrap_to_width(text, 40)
    assert len(lines) > 1
    assert all(display_width(line) <= 40 for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")


def test_wrap_breaks_a_word_longer_than_the_width():
    """한글 문장에는 공백 없는 긴 구간이 흔하다."""
    lines = wrap_to_width("가나다라마바사아자차카타파하", 6)
    assert all(display_width(line) <= 6 for line in lines)
    assert "".join(lines) == "가나다라마바사아자차카타파하"


def test_format_won_uses_thousands_separators():
    assert format_won(9_971_350) == "9,971,350"
    assert format_won(-430_880) == "-430,880"
    assert format_won(0) == "0"
    assert format_won(None) == "-"


def test_format_pct_keeps_the_sign():
    assert format_pct(Decimal("-1.25")) == "-1.25%"
    assert format_pct(Decimal("12.4")) == "+12.4%"
    assert format_pct(None) == "-"


def test_format_gap_marks_direction_and_meaning():
    """설계서 14.1절 — 보유는 `▲ +12.4% (1,160원)`, 대기는 `▼ -9.0% 하락 시 매수`."""
    view = build_stage_detail(config(), current_price=9_340)
    assert format_gap(view.rows[0]) == "▲ +12.4% (1,160원)"
    assert format_gap(view.rows[3]) == "▼ -9.0% 하락 시 매수"


def test_format_gap_reproduces_the_lower_stages_of_the_mockup():
    """목업의 5·6·7단계: `-14.3%`, `-19.7%`, `-25.1%`."""
    view = build_stage_detail(config(), current_price=9_340)
    assert [format_gap(r) for r in view.rows[4:]] == [
        "▼ -14.3% 하락 시 매수",
        "▼ -19.7% 하락 시 매수",
        "▼ -25.1% 하락 시 매수",
    ]


def test_format_gap_is_a_dash_without_a_reference():
    view = build_stage_detail(config(), current_price=None)
    assert format_gap(view.rows[0]) == "-"


# ── 보유현황 표 (설계서 14.1절) ─────────────────────────────────────────
def test_holdings_render_has_a_row_per_config(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot,
        prices={"005930": 9_340, "035720": 7_910, "035420": 161_200},
        mismatched_codes=()))
    assert "삼성전자" in text and "카카오" in text and "NAVER" in text
    assert "005930" in text                 # 종목코드가 함께 보인다


def test_holdings_render_shows_stage_progress_and_status(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={"005930": 9_340},
        mismatched_codes=()))
    assert "3/7" in text and "7/7" in text and "0/5" in text
    assert "감시" in text and "소진" in text and "IDLE" in text


def test_holdings_render_shows_the_mockup_numbers(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={"005930": 9_340}, mismatched_codes=()))
    assert "316" in text and "9,458" in text
    assert "-1.25%" in text and "-37,410" in text


def test_holdings_render_includes_the_totals_line(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={"005930": 9_340, "035720": 7_910},
        mismatched_codes=()))
    assert "합계" in text
    assert "투입" in text and "평가" in text and "손익" in text


def test_holdings_render_warns_about_missing_prices(three_row_snapshot):
    """합계가 일부 종목만 반영한다는 사실이 보여야 한다."""
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={"005930": 9_340}, mismatched_codes=()))
    assert "035720" in text
    assert "시세 미수신" in text


def test_holdings_render_carries_the_broker_notice(three_row_snapshot):
    """설계서 2.1절 — 없으면 사용자가 증권사 앱과 비교하고 프로그램이 틀렸다고
    판단한다."""
    text = render_holdings(build_holdings(three_row_snapshot, prices={},
                                          mismatched_codes=()))
    assert "증권사" in text


def test_holdings_render_shows_the_mismatch_label(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={}, mismatched_codes=("005930",)))
    assert "불일치" in text


def test_every_rendered_line_has_the_same_display_width(three_row_snapshot):
    """열이 어긋나면 표가 아니다. 한글 폭을 잘못 세면 정확히 그렇게 된다."""
    text = render_holdings(build_holdings(
        three_row_snapshot,
        prices={"005930": 9_340, "035420": 161_200},
        mismatched_codes=("005930",)))
    widths = {display_width(line) for line in text.splitlines() if line.strip()}
    assert len(widths) == 1, f"행 폭이 어긋난다: {sorted(widths)}"


# ── 단계별 상세 (설계서 14.1절) ─────────────────────────────────────────
def test_stage_detail_render_has_a_row_per_stage():
    text = render_stage_detail(build_stage_detail(config(),
                                                   current_price=9_340))
    assert "삼성전자" in text
    assert "사이클 #2" in text
    assert "앵커 10,000원" in text
    for header in ("단계", "발동가", "상태", "체결가", "수량", "목표가",
                   "목표까지 / 매수까지"):
        assert header in text
    assert text.count("보유") >= 3
    assert text.count("대기") >= 4


def test_stage_detail_render_aligns():
    text = render_stage_detail(build_stage_detail(config(),
                                                   current_price=9_340))
    widths = {display_width(line) for line in text.splitlines() if line.strip()}
    assert len(widths) == 1


def test_stage_detail_render_of_an_idle_config_explains_itself():
    """단계가 없으면 빈 표를 그리지 않고 이유를 말한다."""
    text = render_stage_detail(build_stage_detail(idle_config(),
                                                   current_price=161_200))
    assert "사이클이 없습니다" in text
    assert "시작" in text
    widths = {display_width(line) for line in text.splitlines() if line.strip()}
    assert len(widths) == 1


# ── 사다리 미리보기 (설계서 14.2절) ─────────────────────────────────────
def test_ladder_preview_render_matches_the_mockup_columns():
    text = render_ladder_preview(_preview())
    for header in ("단계", "발동가", "수량", "투입금액", "목표가", "누적투입"):
        assert header in text
    assert "예상 총투입" in text
    assert "전단계 보유 시 평단" in text
    assert "앵커 대비" in text
    assert "1단계 체결가" in text            # ⓘ 문구


def test_ladder_preview_render_shows_the_mockup_numbers():
    text = render_ladder_preview(_preview())
    assert "6,978,200" in text
    assert "21,800" in text
    assert "-30.1%" in text
    assert "7,823" in text
    assert "-16.2%" in text


def test_ladder_preview_render_shows_headroom_or_excess():
    assert "여유" in render_ladder_preview(_preview())
    over = render_ladder_preview(build_ladder_preview(
        anchor_price=9_340, max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, stock_limit=1_000_000))
    assert "초과" in over


def test_ladder_preview_render_aligns():
    text = render_ladder_preview(_preview())
    widths = {display_width(line) for line in text.splitlines() if line.strip()}
    assert len(widths) == 1


# ── 상태바 ──────────────────────────────────────────────────────────────
def test_status_bar_render_shows_the_three_facts():
    text = render_status_bar(build_status_bar(
        fallback_active=False, last_reconcile=None, total_used=9_971_350,
        total_limit=21_000_000))
    assert "WebSocket" in text
    assert "대사" in text
    assert "9,971,350" in text and "21,000,000" in text
    assert "47.5%" in text


def test_status_bar_render_without_a_limit_shows_a_dash():
    text = render_status_bar(build_status_bar(
        fallback_active=False, last_reconcile=None, total_used=0,
        total_limit=0))
    assert "(-)" in text
