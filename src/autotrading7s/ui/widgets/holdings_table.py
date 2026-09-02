"""보유현황 표 — 설계서 14.1절.

**이 파일은 EC2 에서 import 되지 않는다** (tkinter 부재). 그러므로 로직을 한 줄도
두지 않는다 — 값은 `HoldingsView` 가 이미 계산했고 서식은 `text_render` 의 함수가
한다. `tests/test_g4_prep_gate.py` 가 그 규칙을 강제한다.

**부분 갱신을 하지 않는다.** 어느 행이 바뀌었는지 계산하는 것이 로직이고, 그
로직은 사각지대에 들어간다. 200ms 마다 수십 행을 다시 그리는 비용은 무해하다.
"""

from __future__ import annotations

from collections.abc import Callable
import tkinter as tk
from tkinter import ttk

from autotrading7s.ui.text_render import format_pct, format_won
from autotrading7s.ui.view_model import HoldingsView

_COLUMNS = ("code", "label", "qty", "avg", "price", "pnl_pct", "pnl",
            "stages", "status")
_HEADINGS = {
    "code": "종목코드", "label": "설정", "qty": "보유수량",
    "avg": "평균단가", "price": "현재가", "pnl_pct": "평가손익률",
    "pnl": "평가손익", "stages": "단계", "status": "상태",
}


class HoldingsTable:
    def __init__(self, parent: tk.Misc, *,
                 on_select: Callable[[int], None]) -> None:
        self._on_select = on_select
        self._frame = ttk.LabelFrame(parent, text="보유현황")
        self._frame.pack(fill="both", expand=True)
        self._tree = ttk.Treeview(self._frame, columns=_COLUMNS,
                                  show="tree headings", height=8)
        self._tree.heading("#0", text="종목")
        for name in _COLUMNS:
            self._tree.heading(name, text=_HEADINGS[name])
            self._tree.column(name, anchor="e", width=90)
        self._tree.column("code", anchor="w")
        self._tree.column("label", anchor="w")
        self._tree.column("status", anchor="w")
        self._tree.tag_configure("mismatch", foreground="red")
        self._tree.bind("<<TreeviewSelect>>", self._selected)
        self._tree.pack(fill="both", expand=True)
        self._notice = ttk.Label(self._frame, text="", wraplength=760)
        self._notice.pack(fill="x")

    def _selected(self, _event: object) -> None:
        for item in self._tree.selection():
            self._on_select(int(item))

    def render(self, view: HoldingsView) -> None:
        selected = self._tree.selection()
        self._tree.delete(*self._tree.get_children())
        for row in view.rows:
            self._tree.insert(
                "", "end", iid=str(row.config_id),
                text=row.stock_name or row.stock_code,
                values=(row.stock_code, row.label or "-",
                        format_won(row.held_qty), format_won(row.avg_price),
                        format_won(row.current_price),
                        format_pct(row.pnl_pct), format_won(row.pnl),
                        f"{row.holding_stages}/{row.max_stages}",
                        row.status_label),
                tags=("mismatch",) if row.status_label == "⚠불일치" else (),
            )
        for item in selected:
            if self._tree.exists(item):
                self._tree.selection_set(item)
        totals = view.totals
        text = (f"합계  투입 {format_won(totals.invested)}   "
                f"평가 {format_won(totals.valuation)}   "
                f"손익 {format_won(totals.pnl)} "
                f"({format_pct(totals.pnl_pct)})")
        if totals.missing_prices:
            text += ("\n⚠ 시세 미수신으로 합계에서 제외: "
                     + ", ".join(totals.missing_prices))
        # 설계서 2.1절 — 없으면 사용자가 증권사 앱과 비교하고 프로그램이
        # 틀렸다고 판단한다.
        text += "\nⓘ " + view.broker_avg_notice
        self._notice.configure(text=text)
