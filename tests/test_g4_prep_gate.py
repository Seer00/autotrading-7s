"""G4 준비 게이트 — GUI 층의 경계를 못 박는다.

설계서 14.4절의 규칙("ui/ 는 표시·입력 수집·큐 넣기만 한다")은 EC2 에
`tkinter` 가 없다는 사실 때문에 단순한 스타일 규칙이 아니다: **위젯으로 넘어간
로직은 자동 검증이 영원히 닿지 않는다.** 그러므로 두 경계를 테스트가 지킨다.

이 게이트가 통과해도 "화면이 제대로 그려지는가" 는 검증되지 않는다. 그것은
Windows 에서 사람이 확인해야 하며, 그 절차가
`docs/superpowers/records/2026-09-02-plan4-windows-checklist.md` 에 있다.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path("src/autotrading7s")
PURE_UI = ("view_model.py", "presenter.py", "text_render.py")


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names.add(node.module or "")
    return names


@pytest.mark.parametrize("name", PURE_UI)
def test_pure_ui_modules_do_not_import_tkinter(name):
    """EC2 에 tkinter 가 아예 없다 — import 하는 순간 이 모듈이 테스트 밖으로 나간다."""
    imported = _imports(ROOT / "ui" / name)
    assert not any(m == "tkinter" or m.startswith("tkinter.")
                   for m in imported), f"{name} 이 tkinter 를 import 한다"


@pytest.mark.parametrize("name", PURE_UI)
def test_pure_ui_modules_do_not_touch_the_database(name):
    """설계서 14.4절 — ui/ 는 DB 를 건드리지 않는다.

    그 규칙이 리포지토리의 단일 작성자 전제를 성립시킨다 (2A 핸드오버 3).
    """
    imported = _imports(ROOT / "ui" / name)
    assert "sqlite3" not in imported
    assert not any("adapters" in m for m in imported)


def test_widget_modules_do_not_import_domain_or_engine():
    """위젯에 계산이 들어가면 그 계산은 영원히 사각지대다.

    `app` 과 `ui.view_model`·`ui.presenter` 만 쓴다 — 계산이 필요하면 뷰모델에
    함수를 추가해야 하고, 그러면 그 함수가 EC2 에서 테스트된다.
    """
    forbidden = ("autotrading7s.domain", "autotrading7s.engine",
                 "autotrading7s.ports", "autotrading7s.adapters")
    offenders: list[str] = []
    for path in (ROOT / "ui" / "widgets").rglob("*.py"):
        for module in _imports(path):
            if any(module.startswith(f) for f in forbidden):
                offenders.append(f"{path.name}: {module}")
    assert offenders == []


def test_widget_modules_exist():
    """설계서 7.2절이 나열한 여섯 화면이 모두 있어야 한다."""
    expected = {"main_window.py", "holdings_table.py", "stage_detail.py",
                "config_dialog.py", "emergency_dialog.py", "log_view.py"}
    present = {p.name for p in (ROOT / "ui" / "widgets").glob("*.py")}
    assert expected <= present


def test_engine_and_app_still_do_not_import_ui():
    """의존 방향은 안쪽을 향한다 — 엔진이 화면을 알면 안 된다."""
    offenders: list[str] = []
    for sub in ("engine", "app", "domain", "ports"):
        for path in (ROOT / sub).rglob("*.py"):
            for module in _imports(path):
                if module.startswith("autotrading7s.ui"):
                    offenders.append(f"{path}: {module}")
    assert offenders == []


def test_the_pure_layer_is_importable_without_tkinter():
    """이 테스트가 통과하는 것 자체가 증거다 — EC2 에 tkinter 가 없으므로,
    순수 층이 그것을 끌어들이면 이 import 가 실패한다."""
    import importlib

    for name in ("view_model", "presenter", "text_render"):
        importlib.import_module(f"autotrading7s.ui.{name}")


def test_tkinter_really_is_absent_here():
    """이 게이트의 전제를 게이트가 확인한다.

    만약 어느 환경에 `tkinter` 가 생기면 위젯 층도 테스트할 수 있게 되고,
    그때는 이 게이트의 "검증 불가" 전제와 Windows 체크리스트의 필요성을 다시
    판단해야 한다. 그 신호를 여기서 받는다.
    """
    import importlib.util

    if importlib.util.find_spec("tkinter") is not None:
        pytest.skip("tkinter 가 있다 — 위젯 층 테스트를 추가할 수 있다")
