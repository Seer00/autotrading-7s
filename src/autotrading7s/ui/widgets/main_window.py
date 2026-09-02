"""메인 윈도우 — 설계서 14.1절.

**이 파일은 EC2 에서 import 되지 않는다** (tkinter 부재). 그러므로 로직을 한 줄도
두지 않는다 — 계산이 필요하면 `ui/view_model.py` 에 함수를 추가하고 그것을
호출한다. `tests/test_g4_prep_gate.py` 가 그 규칙을 강제한다.

`_pump` 가 200ms 마다 세 가지를 한다: 이벤트를 프레젠터에 먹이고,
`raise_if_failed()` 를 확인하고, 화면을 다시 그린다. **두 번째가 빠지면 조용히
죽은 엔진을 아무도 보지 못한다 — "프로그램이 켜져 있는데 트리거를 놓치는" 최악의
상태다** (설계서 18.1 리스크 6).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from autotrading7s.app.commands import (
    EmergencyLiquidate,
    ForceClose,
    PauseCycle,
    ResetReconcileBaseline,
    ResumeCycle,
    SaveConfig,
    Shutdown,
    StartCycle,
)
from autotrading7s.app.engine_thread import EngineThread
from autotrading7s.ui.presenter import Presenter
from autotrading7s.ui.text_render import format_pct, format_won
from autotrading7s.ui.widgets.config_dialog import ConfigDialog
from autotrading7s.ui.widgets.emergency_dialog import (
    EmergencyDialog,
    ForceCloseDialog,
)
from autotrading7s.ui.widgets.holdings_table import HoldingsTable
from autotrading7s.ui.widgets.log_view import LogView
from autotrading7s.ui.widgets.stage_detail import StageDetailTable

PUMP_MS = 200


class MainWindow:
    def __init__(self, *, thread: EngineThread, presenter: Presenter) -> None:
        self._thread = thread
        self._presenter = presenter
        self._selected: int | None = None

        self.root = tk.Tk()
        self.root.title("AutoTrading 7s")

        self._banner = ttk.Label(self.root, text="")
        self._banner.pack(fill="x")

        bar = ttk.Frame(self.root)
        bar.pack(fill="x")
        for text, command in (
            ("설정관리", self._open_config),
            ("시작", self._start),
            ("일시정지", self._pause),
            ("재개", self._resume),
            ("긴급청산", self._emergency),
            ("전체 청산", self._emergency_all),
            ("강제 종료", self._force_close),
            # 2B 핸드오버 8 — 기준선 초기화의 UI 입구
            ("대사 기준선 초기화", self._reset_baseline),
        ):
            ttk.Button(bar, text=text, command=command).pack(side="left")

        self._holdings = HoldingsTable(self.root, on_select=self._on_select)
        self._stages = StageDetailTable(self.root)
        self._log = LogView(self.root)
        self._status = ttk.Label(self.root, text="")
        self._status.pack(fill="x")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(PUMP_MS, self._pump)

    # ── 펌프 ────────────────────────────────────────────────────────────
    def _pump(self) -> None:
        self._presenter.consume_all(self._thread.drain_events())
        # 조용히 죽은 엔진은 "프로그램이 켜져 있는데 트리거를 놓치는" 최악의
        # 상태다 (설계서 18.1 리스크 6). 확인 비용은 None 비교 하나다.
        try:
            self._thread.raise_if_failed()
        except BaseException as exc:                      # noqa: BLE001
            self._presenter.note_engine_error(f"{type(exc).__name__}: {exc}")
        self._refresh()
        self.root.after(PUMP_MS, self._pump)

    def _refresh(self) -> None:
        banner = self._presenter.banner()
        text = f"{banner.env_label}   {banner.connection_label}"
        if banner.engine_error is not None:
            text += f"   ⚠ 엔진 정지: {banner.engine_error}"
        self._banner.configure(
            text=text,
            foreground=("red" if banner.is_real or banner.engine_error
                        else "black"))
        self._holdings.render(self._presenter.holdings())
        if self._selected is not None:
            detail = self._presenter.stage_detail(self._selected)
            if detail is not None:
                self._stages.render(detail)
        self._log.render(self._presenter.log_lines())
        bar = self._presenter.status_bar()
        self._status.configure(
            text=f"{bar.quote_source_label} │ {bar.last_reconcile_label} │ "
                 f"총한도 {format_won(bar.total_used)} / "
                 f"{format_won(bar.total_limit)} "
                 f"({format_pct(bar.used_pct)})")

    def _on_select(self, config_id: int) -> None:
        self._selected = config_id
        self._refresh()

    def _selected_row(self):
        if self._selected is None:
            return None
        for row in self._presenter.holdings().rows:
            if row.config_id == self._selected:
                return row
        return None

    # ── 명령 ────────────────────────────────────────────────────────────
    def _start(self) -> None:
        if self._selected is not None:
            self._thread.send(StartCycle(config_id=self._selected))

    def _pause(self) -> None:
        if self._selected is not None:
            self._thread.send(PauseCycle(config_id=self._selected))

    def _resume(self) -> None:
        if self._selected is not None:
            self._thread.send(ResumeCycle(config_id=self._selected))

    def _reset_baseline(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self._thread.send(ResetReconcileBaseline(stock_code=row.stock_code))
        # 대사는 일치할 때 이벤트를 내지 않으므로 프레젠터가 해소를 알 수 없다.
        self._presenter.clear_mismatch(row.stock_code)

    def _emergency(self) -> None:
        if self._selected is None:
            return
        view = self._presenter.emergency(self._selected, scope="SINGLE")
        if view is None:
            return
        result = EmergencyDialog(self.root, view).show()
        if result is not None:
            self._thread.send_priority(EmergencyLiquidate(
                scope="SINGLE", config_id=self._selected,
                reason=result.reason, confirmed_text=result.confirmed_text))

    def _emergency_all(self) -> None:
        if self._selected is None:
            return
        # 전체 청산도 확인 화면은 한 종목의 정보를 보여준다 — 설계서 11.2절이
        # 요구하는 종목별 나열은 Plan 3 이후의 개선으로 남긴다.
        view = self._presenter.emergency(self._selected, scope="ALL")
        if view is None:
            return
        result = EmergencyDialog(self.root, view).show()
        if result is not None:
            self._thread.send_priority(EmergencyLiquidate(
                scope="ALL", config_id=None, reason=result.reason,
                confirmed_text=result.confirmed_text))

    def _force_close(self) -> None:
        if self._selected is None:
            return
        view = self._presenter.force_close(self._selected)
        if view is None:
            return
        result = ForceCloseDialog(self.root, view).show()
        if result is not None and result.reason is not None:
            self._thread.send_priority(ForceClose(
                config_id=self._selected, reason=result.reason,
                confirmed_text=result.confirmed_text or ""))

    def _open_config(self) -> None:
        fields = ConfigDialog(self.root, self._presenter).show()
        if fields is not None:
            self._thread.send(SaveConfig(config_id=None, **fields))

    def _on_close(self) -> None:
        self._thread.send(Shutdown())
        self._thread.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
