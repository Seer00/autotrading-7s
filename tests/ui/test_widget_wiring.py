"""위젯 배선 검사 — `tkinter` 를 스텁으로 대체해 import 하고 `render()` 를 부른다.

**이 테스트가 검증하지 않는 것부터.** 화면이 어떻게 보이는지, 열 폭이 맞는지,
색이 제대로 나오는지는 전혀 검증하지 않는다. Tk 위젯이 하는 일은 전부 스텁이
삼킨다. 그것은 여전히 Windows 에서 사람이 확인해야 한다
(`docs/superpowers/records/2026-09-02-plan4-windows-checklist.md`).

**검증하는 것.** 위젯 층은 EC2 에 `tkinter` 가 없어서 import 조차 되지 않으므로,
그 안의 **모든 이름 오류가 Windows 에서 처음 실행할 때까지 숨는다** —
`view.pnl_pct` 를 `view.pnl_percent` 로 쓴 오타, 없는 함수 호출, 잘못된 import.
스텁을 넣으면 그 부류가 여기서 잡힌다. 사각지대를 "위젯 코드 전부" 에서
"실제 Tk 동작" 으로 줄이는 것이 이 테스트의 값어치다.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest

from autotrading7s.ui.presenter import Presenter
from autotrading7s.ui.view_model import build_ladder_preview

from .conftest import PCT, config, snapshot


class _Var:
    """`StringVar`·`BooleanVar` 의 최소 대역.

    `MagicMock` 으로 두면 `.get()` 이 Mock 을 반환해 `parse_config_form` 이
    문자열을 받지 못한다 — 그러면 이 테스트가 검증하려는 경로를 지나가지 못하고
    통과한다. 실제 값을 담는 것이 요점이다.
    """

    def __init__(self, value=None, **_kw):
        self._value = "" if value is None else value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value

    def trace_add(self, *_args, **_kw):
        return "trace"


def _install_tkinter_stub():
    tk = MagicMock(name="tkinter")
    tk.StringVar = _Var
    tk.BooleanVar = lambda value=False, **kw: _Var(value)
    ttk = MagicMock(name="tkinter.ttk")
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    tk.ttk = ttk
    return tk, ttk


@pytest.fixture
def tkinter_stub():
    saved = {k: sys.modules.get(k) for k in ("tkinter", "tkinter.ttk")}
    yield _install_tkinter_stub()
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    for name in list(sys.modules):
        if name.startswith("autotrading7s.ui.widgets") or name == "autotrading7s.__main__":
            sys.modules.pop(name, None)


def _module(name: str):
    return importlib.import_module(f"autotrading7s.ui.widgets.{name}")


@pytest.mark.parametrize("name", [
    "holdings_table", "stage_detail", "log_view", "config_dialog",
    "emergency_dialog", "main_window",
])
def test_every_widget_module_imports(tkinter_stub, name):
    """import 만으로도 잘못된 import·모듈 수준 이름 오류가 잡힌다."""
    assert _module(name) is not None


def test_main_entry_point_imports(tkinter_stub):
    """설계서 16절 `python -m autotrading7s`."""
    assert importlib.import_module("autotrading7s.__main__") is not None


def test_holdings_table_renders_a_view(tkinter_stub, three_row_snapshot):
    """`HoldingsView` 의 필드 이름을 위젯이 정확히 쓰는지 확인한다.

    오타 하나가 Windows 에서 처음 실행할 때까지 숨는다.
    """
    from autotrading7s.ui.view_model import build_holdings

    table = _module("holdings_table").HoldingsTable(
        MagicMock(), on_select=lambda _cid: None)
    view = build_holdings(three_row_snapshot,
                          prices={"005930": 9_340},
                          mismatched_codes=("005930",))
    table.render(view)                     # 이름 오류가 있으면 여기서 터진다
    table.render(build_holdings(snapshot(), prices={}, mismatched_codes=()))


def test_stage_detail_table_renders_both_shapes(tkinter_stub):
    from autotrading7s.ui.view_model import build_stage_detail

    from .conftest import idle_config

    table = _module("stage_detail").StageDetailTable(MagicMock())
    table.render(build_stage_detail(config(), current_price=9_340))
    table.render(build_stage_detail(config(), current_price=None))
    table.render(build_stage_detail(idle_config(), current_price=None))


def test_log_view_renders_every_event_kind(tkinter_stub):
    """모든 이벤트 종류가 로그 줄로 그려져야 한다 — 태그 이름 오류도 잡는다."""
    from datetime import UTC, datetime

    from autotrading7s.app.events import (
        CommandFailed,
        CycleLoadFailed,
        GuardBlocked,
        OrderRejected,
        OrderUnknown,
        QuoteFallback,
        StageFilled,
    )

    at = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
    presenter = Presenter("mock")
    presenter.consume_all([
        StageFilled(config_id=1, cycle_id=1, stage_no=1, side="BUY",
                    fill_price=10_000, fill_qty=100, at=at),
        GuardBlocked(config_id=1, stage_no=2, side="BUY", reason="한도", at=at),
        OrderUnknown(config_id=1, cycle_id=1, stage_no=3, client_ref="a",
                     at=at),
        OrderRejected(config_id=1, cycle_id=1, stage_no=3, api_code="40510",
                      api_message="거부", at=at),
        CycleLoadFailed(config_id=1, cycle_id=1, detail="corrupt",
                        action_taken="PAUSED", at=at),
        CommandFailed(command="StartCycle", detail="KeyError", at=at),
        QuoteFallback(stock_codes=("005930",), active=True, at=at),
    ])
    _module("log_view").LogView(MagicMock()).render(presenter.log_lines())


def test_config_dialog_builds_and_previews(tkinter_stub):
    """폼 기본값이 `parse_config_form` → `build_ladder_preview` →
    `render_ladder_preview` 를 통과해야 한다.

    이름이 하나라도 어긋나면 [설정관리]를 누른 순간 다이얼로그가 터진다.
    """
    dialog = _module("config_dialog").ConfigDialog(MagicMock(),
                                                    Presenter("mock"))
    dialog._refresh()                      # 기본값으로 미리보기가 만들어진다
    assert dialog.show.__name__ == "show"


def test_config_dialog_survives_bad_input(tkinter_stub):
    """`FormError` 경로도 지나가야 한다 — 그 분기가 터지면 사용자는 오타를
    고칠 수 없다."""
    dialog = _module("config_dialog").ConfigDialog(MagicMock(),
                                                    Presenter("mock"))
    dialog._vars["drop_pct"].set("abc")
    dialog._refresh()
    dialog._on_save()
    assert dialog._result is None          # 저장되지 않았다


def test_emergency_dialogs_build_from_views(tkinter_stub, three_row_snapshot):
    """다이얼로그 본문이 뷰의 모든 필드를 정확히 읽는지 확인한다."""
    module = _module("emergency_dialog")
    presenter = Presenter("mock")
    presenter.consume(three_row_snapshot)

    single = presenter.emergency(1, scope="SINGLE")
    module.EmergencyDialog(MagicMock(), single)
    every = presenter.emergency(1, scope="ALL")
    module.EmergencyDialog(MagicMock(), every)

    from datetime import UTC, datetime

    from autotrading7s.app.events import EmergencyResult

    presenter.consume(EmergencyResult(
        scope="SINGLE", stock_code="005930", result="FAILED", qty_before=316,
        qty_after=316, canceled_orders=0, detail="거래정지",
        at=datetime(2026, 9, 2, 15, 28, tzinfo=UTC)))
    module.ForceCloseDialog(MagicMock(), presenter.force_close(1))


def test_main_window_builds_and_refreshes(tkinter_stub, three_row_snapshot):
    """`_refresh` 가 배너·표·로그·상태바를 모두 읽는다 — 그 경로의 이름 오류가
    가장 비싸다(창이 열리자마자 200ms 뒤에 터진다)."""
    presenter = Presenter("mock")
    presenter.consume(three_row_snapshot)
    thread = MagicMock()
    thread.drain_events.return_value = []
    thread.raise_if_failed.return_value = None

    window = _module("main_window").MainWindow(thread=thread,
                                               presenter=presenter)
    window._pump()                         # 이벤트 소비 → 확인 → 다시 그리기
    window._on_select(1)
    window._refresh()


def test_main_window_surfaces_a_dead_engine(tkinter_stub, three_row_snapshot):
    """`raise_if_failed()` 가 던진 것이 배너에 도달해야 한다.

    설계서 18.1 리스크 6 — 조용히 죽은 엔진이 최악이다.
    """
    presenter = Presenter("mock")
    presenter.consume(three_row_snapshot)
    thread = MagicMock()
    thread.drain_events.return_value = []
    thread.raise_if_failed.side_effect = RuntimeError("복구 실패")

    window = _module("main_window").MainWindow(thread=thread,
                                               presenter=presenter)
    window._pump()

    assert "복구 실패" in presenter.banner().engine_error


def test_main_window_commands_reach_the_thread(tkinter_stub,
                                               three_row_snapshot):
    """버튼이 만드는 명령의 타입과 인자를 확인한다.

    `send_priority` 는 `PriorityCommand` 만 받으므로, 긴급청산을 `send` 로
    보내는 오타는 런타임에만 드러난다 — 여기서 잡는다.
    """
    from autotrading7s.app.commands import PauseCycle, StartCycle

    presenter = Presenter("mock")
    presenter.consume(three_row_snapshot)
    thread = MagicMock()
    thread.drain_events.return_value = []
    thread.raise_if_failed.return_value = None
    window = _module("main_window").MainWindow(thread=thread,
                                               presenter=presenter)
    window._on_select(1)

    window._start()
    window._pause()
    sent = [c.args[0] for c in thread.send.call_args_list]
    assert any(isinstance(c, StartCycle) for c in sent)
    assert any(isinstance(c, PauseCycle) for c in sent)


def test_main_window_reset_baseline_clears_the_local_warning(
    tkinter_stub, three_row_snapshot,
):
    """대사는 일치할 때 이벤트를 내지 않으므로, 초기화를 보낸 쪽이 경고를
    지워야 한다 (2B 핸드오버 8)."""
    from autotrading7s.app.commands import ResetReconcileBaseline
    from autotrading7s.app.events import ReconcileMismatch

    from .conftest import AT

    presenter = Presenter("mock")
    presenter.consume_all([three_row_snapshot, ReconcileMismatch(
        stock_code="005930", internal_qty=316, broker_qty=300,
        verdict="INTERNAL_MORE", action_taken="PAUSED", at=AT)])
    thread = MagicMock()
    thread.drain_events.return_value = []
    thread.raise_if_failed.return_value = None
    window = _module("main_window").MainWindow(thread=thread,
                                               presenter=presenter)
    window._on_select(1)
    assert presenter.holdings().rows[0].status_label == "⚠불일치"

    window._reset_baseline()

    sent = [c.args[0] for c in thread.send.call_args_list]
    assert any(isinstance(c, ResetReconcileBaseline) for c in sent)
    assert presenter.holdings().rows[0].status_label != "⚠불일치"
