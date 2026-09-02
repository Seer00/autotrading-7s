"""단계별 상세 표 — 설계서 14.1절.

**EC2 에서 import 되지 않는다.** "목표까지 / 매수까지" 열의 문자열은
`text_render.format_gap` 이 만든다 — 여기서 방향 기호나 백분율을 조립하면 그
조립이 사각지대에 들어간다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from autotrading7s.ui.text_render import format_gap, format_won
from autotrading7s.ui.view_model import StageDetailView

_COLUMNS = ("trigger", "status", "fill", "qty", "target", "gap")
_HEADINGS = {
    "trigger": "발동가", "status": "상태", "fill": "체결가", "qty": "수량",
    "target": "목표가", "gap": "목표까지 / 매수까지",
}


class StageDetailTable:
    def __init__(self, parent: tk.Misc) -> None:
        self._frame = ttk.LabelFrame(parent, text="단계별 상세")
        self._frame.pack(fill="both", expand=True)
        self._header = ttk.Label(self._frame, text="")
        self._header.pack(fill="x")
        self._tree = ttk.Treeview(self._frame, columns=_COLUMNS,
                                  show="tree headings", height=7)
        self._tree.heading("#0", text="단계")
        self._tree.column("#0", width=60, anchor="center")
        for name in _COLUMNS:
            self._tree.heading(name, text=_HEADINGS[name])
            self._tree.column(name, anchor="e", width=100)
        self._tree.column("status", anchor="w")
        self._tree.column("gap", anchor="w", width=220)
        self._tree.pack(fill="both", expand=True)

    def render(self, view: StageDetailView) -> None:
        name = view.stock_name or "-"
        label = view.label or "-"
        if view.cycle_seq is None:
            self._header.configure(
                text=f"{name} / {label} — 사이클이 없습니다. [시작]을 누르면 "
                     f"첫 틱의 가격으로 앵커가 확정됩니다.")
        else:
            self._header.configure(
                text=f"{name} / {label}  (사이클 #{view.cycle_seq}, "
                     f"앵커 {format_won(view.anchor_price)}원)")
        self._tree.delete(*self._tree.get_children())
        for row in view.rows:
            self._tree.insert(
                "", "end", text=str(row.stage_no),
                values=(format_won(row.trigger_price), row.status_label,
                        format_won(row.fill_price), format_won(row.fill_qty),
                        format_won(row.target_price), format_gap(row)),
            )
