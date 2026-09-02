"""설정 등록 다이얼로그 — 설계서 14.2절.

**EC2 에서 import 되지 않는다.** 파싱은 `parse_config_form`, 미리보기 계산은
`build_ladder_preview`, 미리보기 서식은 `render_ladder_preview` 가 한다 —
여기서 하는 일은 입력을 모아 넘기고 결과를 붙이는 것뿐이다.

입력이 바뀔 때마다 미리보기를 갱신하고, `FormError` 면 그 메시지를 미리보기
영역에 표시한다. 오류 메시지에 필드 이름이 들어 있으므로 사용자가 어느 입력란
문제인지 알 수 있다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from autotrading7s.ui.presenter import Presenter
from autotrading7s.ui.text_render import render_ladder_preview
from autotrading7s.ui.view_model import (
    FormError,
    build_ladder_preview,
    parse_config_form,
)

_FIELDS = (
    ("stock_code", "종목코드", "005930"),
    ("stock_name", "종목명", ""),
    ("label", "설정이름", "기본"),
    ("max_stages", "분할 단계 수", "7"),
    ("drop_pct", "단계별 하락률 (%)", "5.0"),
    ("target_pct", "단계별 목표수익률 (%)", "5.0"),
    ("amount_per_stage", "단계당 투입금액 (원)", "1,000,000"),
    ("total_limit", "종목 총한도 (원)", "7,000,000"),
    ("rebuy_cooldown_sec", "재매수 쿨다운 (초)", "60"),
)


class ConfigDialog:
    """`show()` 는 [저장] 시 `parse_config_form` 의 결과를, 취소 시 `None` 을
    반환한다 — 호출자는 그것을 그대로 `SaveConfig(config_id=None, **fields)`
    에 넘길 수 있다."""

    def __init__(self, parent: tk.Misc, presenter: Presenter) -> None:
        self._presenter = presenter
        self._result: dict[str, object] | None = None
        self._top = tk.Toplevel(parent)
        self._top.title("분할 설정 등록")
        self._top.transient(parent)
        self._top.grab_set()

        self._vars: dict[str, tk.StringVar] = {}
        form = ttk.Frame(self._top)
        form.pack(fill="x")
        for row, (name, label, default) in enumerate(_FIELDS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value=default)
            var.trace_add("write", lambda *_: self._refresh())
            ttk.Entry(form, textvariable=var, width=20).grid(row=row, column=1)
            self._vars[name] = var
        self._rebuy = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="매도된 단계 재매수 허용",
                        variable=self._rebuy,
                        command=self._refresh).grid(
            row=len(_FIELDS), column=0, columnspan=2, sticky="w")

        self._preview = tk.Text(self._top, height=20, width=80, wrap="none",
                                state="disabled")
        self._preview.pack(fill="both", expand=True)

        buttons = ttk.Frame(self._top)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="취소",
                   command=self._top.destroy).pack(side="right")
        self._save = ttk.Button(buttons, text="저장", command=self._on_save)
        self._save.pack(side="right")
        self._refresh()

    def _fields(self) -> dict[str, str]:
        fields = {name: var.get() for name, var in self._vars.items()}
        fields["allow_rebuy"] = "1" if self._rebuy.get() else "0"
        return fields

    def _refresh(self) -> None:
        try:
            parsed = parse_config_form(self._fields())
            preview = build_ladder_preview(
                anchor_price=int(parsed["amount_per_stage"]),  # type: ignore[arg-type]
                max_stages=int(parsed["max_stages"]),          # type: ignore[arg-type]
                drop_pct=parsed["drop_pct"],                   # type: ignore[arg-type]
                target_pct=parsed["target_pct"],               # type: ignore[arg-type]
                amount_per_stage=int(parsed["amount_per_stage"]),  # type: ignore[arg-type]
                stock_limit=int(parsed["total_limit"]),        # type: ignore[arg-type]
            )
            text = render_ladder_preview(preview)
            self._save.state(["!disabled"])
        except (FormError, ValueError, TypeError) as exc:
            text = f"⚠ {exc}"
            self._save.state(["disabled"])
        feedback = self._presenter.take_config_feedback()
        if feedback is not None and not feedback.ok:
            text = f"⚠ 저장 거부: {feedback.detail}\n\n{text}"
        self._preview.configure(state="normal")
        self._preview.delete("1.0", "end")
        self._preview.insert("1.0", text)
        self._preview.configure(state="disabled")

    def _on_save(self) -> None:
        try:
            self._result = parse_config_form(self._fields())
        except FormError:
            self._refresh()
            return
        self._top.destroy()

    def show(self) -> dict[str, object] | None:
        self._top.wait_window()
        return self._result
