"""ASCII 렌더러 — 설계서 14.1·14.2절 목업의 텍스트 재현.

**이 모듈이 레이아웃을 EC2 에서 테스트 가능하게 만든다.** Tkinter 위젯은 같은
뷰모델의 값을 Treeview 열에 옮기는 일만 하므로, 열·순서·서식·방향 기호가 여기서
맞으면 위젯에서 틀릴 여지가 열 매핑뿐이다. `cli.py --status` 의 headless 상태
화면도 이것을 쓴다.

**목업을 문자 단위로 재현하지 않는다.** 목업은 사람이 그린 것이라 열 폭이 일정하지
않다. 정확한 공백 수를 단정하면 열 하나를 넓히는 것이 스무 개 테스트를 깨뜨리고,
그 테스트는 레이아웃이 아니라 자기 자신을 지킨다.

**한글 폭을 세야 한다.** `len("삼성전자") == 4` 지만 고정폭에서 8칸이다. 쓰지
않으면 종목명이 있는 행만 표가 어긋나고, 그것은 화면을 본 사람만 아는 결함이다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from autotrading7s.ui.view_model import (
    HoldingsView,
    LadderPreview,
    StageDetailView,
    StageRowView,
    StatusBarView,
)

_SEP = " │ "


def display_width(text: str) -> int:
    """고정폭 터미널에서 차지하는 칸 수. 한글·전각 문자는 2 다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def pad(text: str, width: int, *, align: str = "left") -> str:
    """표시 폭 기준으로 채운다.

    **넘치면 자르지 않는다** — 잘라내면 종목명이 조용히 사라지고, 어긋난 행이
    잘린 이름보다 낫다.
    """
    fill = max(0, width - display_width(text))
    if align == "right":
        return " " * fill + text
    if align == "center":
        left = fill // 2
        return " " * left + text + " " * (fill - left)
    return text + " " * fill


def format_won(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def format_pct(value: Decimal | None) -> str:
    """부호를 유지한다 — `+12.4%` 와 `12.4%` 는 사용자에게 다른 의미다."""
    if value is None:
        return "-"
    return f"{value:+}%" if value > 0 else f"{value}%"


def format_gap(row: StageRowView) -> str:
    """설계서 14.1절 "목표까지 / 매수까지" 열.

    보유는 `▲ +12.4% (1,160원)`, 대기는 `▼ -9.0% 하락 시 매수`.
    """
    if row.gap_kind is None or row.gap_pct is None or row.gap_won is None:
        return "-"
    arrow = "▲" if row.gap_won > 0 else "▼"
    if row.gap_kind == "TARGET":
        return f"{arrow} {format_pct(row.gap_pct)} ({format_won(row.gap_won)}원)"
    return f"{arrow} {format_pct(row.gap_pct)} 하락 시 매수"


# ── 표 조립 ─────────────────────────────────────────────────────────────
def _inner_width(widths: Sequence[int]) -> int:
    return sum(widths) + len(_SEP) * (len(widths) - 1)


def _row(cells: Sequence[str], widths: Sequence[int],
         aligns: Sequence[str]) -> str:
    padded = [pad(c, w, align=a) for c, w, a in zip(cells, widths, aligns)]
    return "│ " + _SEP.join(padded) + " │"


def wrap_to_width(text: str, width: int) -> list[str]:
    """표시 폭 기준으로 줄바꿈한다. 최소 한 줄을 반환한다.

    자르는 대신 줄바꿈하는 이유: `pad` 가 넘치는 텍스트를 자르지 않으므로
    (잘라내면 내용이 조용히 사라진다) 긴 안내문이 표의 정렬을 깨뜨린다.
    줄바꿈은 내용도 정렬도 지킨다.

    공백으로 끊고, 한 낱말이 폭보다 길면 글자 단위로 끊는다 — 한글 문장에는
    공백 없는 긴 구간이 흔하다.
    """
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = word if not current else f"{current} {word}"
        if display_width(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while display_width(word) > width:
            cut = ""
            for ch in word:
                if display_width(cut + ch) > width:
                    break
                cut += ch
            lines.append(cut)
            word = word[len(cut):]
        current = word
    lines.append(current)
    return lines


def _wide(text: str, widths: Sequence[int]) -> list[str]:
    """열을 무시하고 한 줄(또는 줄바꿈된 여러 줄)을 쓴다. 폭은 표와 같다."""
    inner = _inner_width(widths)
    return ["│ " + pad(line, inner) + " │"
            for line in wrap_to_width(text, inner)]


def _rule(widths: Sequence[int], *, char: str = "─") -> str:
    return "├" + char * (_inner_width(widths) + 2) + "┤"


def _top(widths: Sequence[int]) -> str:
    return "┌" + "─" * (_inner_width(widths) + 2) + "┐"


def _bottom(widths: Sequence[int]) -> str:
    return "└" + "─" * (_inner_width(widths) + 2) + "┘"


# ── 보유현황 (설계서 14.1절) ────────────────────────────────────────────
_HOLDINGS_COLS = (
    ("종목", 14, "left"), ("설정", 8, "left"), ("보유수량", 8, "right"),
    ("평균단가", 9, "right"), ("현재가", 8, "right"), ("평가손익", 12, "right"),
    ("단계", 6, "center"), ("상태", 8, "left"),
)


def render_holdings(view: HoldingsView) -> str:
    headers = [c[0] for c in _HOLDINGS_COLS]
    widths = [c[1] for c in _HOLDINGS_COLS]
    aligns = [c[2] for c in _HOLDINGS_COLS]
    lines = [_top(widths), *_wide("보유현황", widths), _rule(widths),
             _row(headers, widths, aligns), _rule(widths)]
    for row in view.rows:
        lines.append(_row([
            row.stock_name or row.stock_code, row.label or "-",
            format_won(row.held_qty), format_won(row.avg_price),
            format_won(row.current_price), format_pct(row.pnl_pct),
            f"{row.holding_stages}/{row.max_stages}", row.status_label,
        ], widths, aligns))
        # 둘째 줄에 종목코드와 평가손익 금액을 둔다 — 목업과 같은 배치다.
        lines.append(_row([
            f"  {row.stock_code}", "", "", "", "", format_won(row.pnl), "", "",
        ], widths, aligns))
    lines.append(_rule(widths))
    totals = view.totals
    lines.extend(_wide(
        f"합계   투입 {format_won(totals.invested)}   "
        f"평가 {format_won(totals.valuation)}   "
        f"손익 {format_won(totals.pnl)} ({format_pct(totals.pnl_pct)})",
        widths))
    if totals.missing_prices:
        lines.extend(_wide(
            f"⚠ 시세 미수신으로 합계에서 제외: "
            f"{', '.join(totals.missing_prices)}", widths))
    lines.extend(_wide(f"ⓘ {view.broker_avg_notice}", widths))
    lines.append(_bottom(widths))
    return "\n".join(lines)


# ── 단계별 상세 (설계서 14.1절) ─────────────────────────────────────────
_STAGE_COLS = (
    ("단계", 4, "center"), ("발동가", 8, "right"), ("상태", 8, "left"),
    ("체결가", 8, "right"), ("수량", 6, "right"), ("목표가", 8, "right"),
    ("목표까지 / 매수까지", 24, "left"),
)


def render_stage_detail(view: StageDetailView) -> str:
    headers = [c[0] for c in _STAGE_COLS]
    widths = [c[1] for c in _STAGE_COLS]
    aligns = [c[2] for c in _STAGE_COLS]
    name = view.stock_name or "-"
    label = view.label or "-"
    if view.cycle_seq is None:
        header = f"단계별 상세 — {name} / {label}  (사이클 없음)"
    else:
        header = (f"단계별 상세 — {name} / {label}  "
                  f"(사이클 #{view.cycle_seq}, "
                  f"앵커 {format_won(view.anchor_price)}원)")
    lines = [_top(widths), *_wide(header, widths), _rule(widths)]
    if not view.rows:
        lines.extend(_wide("사이클이 없습니다 — [시작]을 누르면 첫 틱의 "
                           "가격으로 앵커가 확정됩니다.", widths))
        lines.append(_bottom(widths))
        return "\n".join(lines)
    lines.append(_row(headers, widths, aligns))
    lines.append(_rule(widths))
    for row in view.rows:
        lines.append(_row([
            str(row.stage_no), format_won(row.trigger_price),
            row.status_label, format_won(row.fill_price),
            format_won(row.fill_qty), format_won(row.target_price),
            format_gap(row),
        ], widths, aligns))
    lines.append(_bottom(widths))
    return "\n".join(lines)


# ── 사다리 미리보기 (설계서 14.2절) ─────────────────────────────────────
_PREVIEW_COLS = (
    ("단계", 4, "center"), ("발동가", 8, "right"), ("수량", 6, "right"),
    ("투입금액", 11, "right"), ("목표가", 8, "right"),
    ("누적투입", 11, "right"),
)


def render_ladder_preview(view: LadderPreview) -> str:
    headers = [c[0] for c in _PREVIEW_COLS]
    widths = [c[1] for c in _PREVIEW_COLS]
    aligns = [c[2] for c in _PREVIEW_COLS]
    lines = [_top(widths), *_wide("사다리 미리보기", widths), _rule(widths),
             _row(headers, widths, aligns), _rule(widths)]
    for row in view.rows:
        lines.append(_row([
            str(row.stage_no), format_won(row.trigger_price),
            format_won(row.qty), format_won(row.investment),
            format_won(row.target_price), format_won(row.cumulative),
        ], widths, aligns))
    lines.append(_rule(widths))
    verdict = ("✕ 초과 " + format_won(-view.headroom) if view.over_limit
               else "✓ 여유 " + format_won(view.headroom))
    lines.extend(_wide(
        f"예상 총투입 {format_won(view.total_investment)}원  /  "
        f"한도 {format_won(view.stock_limit)}원   {verdict}", widths))
    lines.extend(_wide(
        f"{len(view.rows)}단계 발동가는 앵커 대비 "
        f"{format_pct(view.last_drop_pct)}  (호가단위 내림 반영)", widths))
    lines.extend(_wide(
        f"전단계 보유 시 평단 {format_won(view.full_avg_price)}원  "
        f"(앵커 대비 {format_pct(view.full_avg_drop_pct)})", widths))
    lines.extend(_wide(f"ⓘ {view.notice}", widths))
    lines.append(_bottom(widths))
    return "\n".join(lines)


def render_status_bar(view: StatusBarView) -> str:
    """설계서 14.1절 하단 한 줄."""
    used = format_pct(view.used_pct) if view.used_pct is not None else "-"
    return (f"{view.quote_source_label} │ {view.last_reconcile_label} │ "
            f"총한도 {format_won(view.total_used)} / "
            f"{format_won(view.total_limit)} ({used})")
