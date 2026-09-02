"""로그 뷰 — 설계서 14.1절 [로그].

**EC2 에서 import 되지 않는다.**

`OrderUnknown` 과 `OrderRejected` 를 **같은 색으로 그리면 안 된다** (2B
핸드오버 4). 앞의 것은 "재발주 금지 상태에서 조회로 확인 중" 이고 뒤의 것은
"복구가 끝난 상태" 다 — 합치면 사용자가 개입할 시점을 알 수 없다. 종류별 태그가
심각도 태그를 덮는다.
"""

from __future__ import annotations

from collections.abc import Sequence
import tkinter as tk
from tkinter import ttk

from autotrading7s.ui.presenter import LogLine

_SEVERITY_COLORS = {"ERROR": "red", "WARN": "#b06000", "INFO": "black"}
# "확인 중" 은 실패가 아니다 — 거부와 다른 색을 준다.
_KIND_COLORS = {"OrderUnknown": "blue"}


class LogView:
    def __init__(self, parent: tk.Misc) -> None:
        self._frame = ttk.LabelFrame(parent, text="로그")
        self._frame.pack(fill="both", expand=True)
        self._text = tk.Text(self._frame, height=8, state="disabled",
                             wrap="none")
        for severity, color in _SEVERITY_COLORS.items():
            self._text.tag_configure(severity, foreground=color)
        for kind, color in _KIND_COLORS.items():
            self._text.tag_configure(kind, foreground=color)
        self._text.pack(fill="both", expand=True)

    def render(self, lines: Sequence[LogLine]) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        for line in lines:
            tag = line.kind if line.kind in _KIND_COLORS else line.severity
            self._text.insert(
                "end", f"{line.at:%H:%M:%S} [{line.kind}] {line.text}\n",
                (tag,))
        self._text.see("end")
        self._text.configure(state="disabled")
