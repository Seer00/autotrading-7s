"""긴급청산·강제 종료 다이얼로그 — 설계서 14.3절·11.4절.

**EC2 에서 import 되지 않는다.**

두 다이얼로그가 확인 문자열을 **자기가 정하지 않고 뷰에서 받는다.** 다시 쓰면
사용자가 정확히 입력했는데 버튼이 활성화되지 않는다 — 뷰가 `app/commands.py`
의 상수를 그대로 담는다.
"""

from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from autotrading7s.ui.text_render import format_pct, format_won
from autotrading7s.ui.view_model import EmergencyDialogView, ForceCloseDialogView


@dataclass(frozen=True, slots=True)
class DialogResult:
    reason: str | None
    confirmed_text: str | None


class _ConfirmDialog:
    """확인 텍스트와 사유를 받는 공통 껍데기."""

    def __init__(self, parent: tk.Misc, *, title: str, body: str,
                 required_text: str | None, reason_required: bool) -> None:
        self._required = required_text
        self._reason_required = reason_required
        self._result: DialogResult | None = None
        self._top = tk.Toplevel(parent)
        self._top.title(title)
        self._top.transient(parent)
        self._top.grab_set()

        ttk.Label(self._top, text=body, justify="left",
                  wraplength=520).pack(fill="x")
        ttk.Label(self._top, text="사유" + (" (필수)" if reason_required
                                          else " (선택)")).pack(anchor="w")
        self._reason = tk.StringVar()
        self._reason.trace_add("write", lambda *_: self._refresh())
        ttk.Entry(self._top, textvariable=self._reason,
                  width=60).pack(fill="x")

        self._typed = tk.StringVar()
        if required_text is not None:
            ttk.Label(self._top,
                      text=f"확인을 위해 `{required_text}` 를 입력하세요"
                      ).pack(anchor="w")
            self._typed.trace_add("write", lambda *_: self._refresh())
            ttk.Entry(self._top, textvariable=self._typed,
                      width=30).pack(fill="x")

        buttons = ttk.Frame(self._top)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="취소",
                   command=self._top.destroy).pack(side="right")
        self._run = ttk.Button(buttons, text="실행", command=self._on_run)
        self._run.pack(side="right")
        self._refresh()

    def _ok(self) -> bool:
        if self._required is not None and self._typed.get() != self._required:
            return False
        return not (self._reason_required and not self._reason.get().strip())

    def _refresh(self) -> None:
        self._run.state(["!disabled"] if self._ok() else ["disabled"])

    def _on_run(self) -> None:
        if not self._ok():
            return
        self._result = DialogResult(
            reason=self._reason.get().strip() or None,
            confirmed_text=self._typed.get() or None)
        self._top.destroy()

    def show(self) -> DialogResult | None:
        self._top.wait_window()
        return self._result


class EmergencyDialog(_ConfirmDialog):
    def __init__(self, parent: tk.Misc, view: EmergencyDialogView) -> None:
        body = (
            f"다음 종목을 시장가로 전량 매도합니다.\n"
            f"자동 트리거 로직을 우회하며, 실행 후 취소할 수 없습니다.\n\n"
            f"  종목       {view.stock_name or '-'} ({view.stock_code})\n"
            f"  보유수량   {format_won(view.held_qty)}주 "
            f"(보유 단계 {view.holding_stages}개)\n"
            f"  현재가     {format_won(view.current_price)}원\n"
            f"  예상금액   {format_won(view.estimated_amount)}원\n"
            f"  평균단가   {format_won(view.avg_price)}원  →  예상손익 "
            f"{format_won(view.estimated_pnl)}원 "
            f"({format_pct(view.estimated_pnl_pct)})\n"
        )
        if view.pending_orders:
            # 설계서 11.1절 ② — 빠뜨리면 긴급청산이 무력화된다.
            body += (f"\n▸ 미체결 주문 {view.pending_orders}건이 함께 "
                     f"취소됩니다.\n")
        super().__init__(parent, title="⚠ 긴급청산 확인", body=body,
                         required_text=view.required_text,
                         reason_required=False)


class ForceCloseDialog(_ConfirmDialog):
    def __init__(self, parent: tk.Misc, view: ForceCloseDialogView) -> None:
        attempt = ("청산 시도 없음" if view.attempts == 0 else
                   f"청산 시도 {view.attempts}회"
                   + (f", 마지막 {view.last_attempt_at:%H:%M}"
                      if view.last_attempt_at is not None else ""))
        body = (
            f"이 사이클은 청산이 완료되지 않았습니다.\n"
            f"강제 종료하면 내부 기록은 종료되지만 증권사 계좌에는 주식이 "
            f"그대로 남습니다.\n\n"
            f"  종목       {view.stock_name or '-'} ({view.stock_code})\n"
            f"  남은 보유  {format_won(view.remaining_qty)}주 "
            f"(보유 단계 {view.holding_stages}개)\n"
            f"  {attempt}  (이 세션 기준)\n"
            f"  미체결 사유 {view.last_failure_detail or '-'}\n\n"
            f"▸ 종료 후 이 {format_won(view.remaining_qty)}주는 프로그램이 "
            f"관리하지 않습니다. 증권사 앱에서 직접 처리하셔야 합니다.\n"
        )
        super().__init__(parent, title="⚠ 강제 종료 확인", body=body,
                         required_text=view.required_text,
                         reason_required=True)
