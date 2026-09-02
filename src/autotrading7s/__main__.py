"""GUI 기동 — 설계서 16절 `python -m autotrading7s`.

headless 는 `python -m autotrading7s.cli` 다.

**이 파일은 EC2 에서 import 되지 않는다** — `MainWindow` 가 `tkinter` 를
끌어오기 때문이다. 조립만 하고 로직을 두지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.app.engine_thread import EngineThread
from autotrading7s.app.settings import load_settings
from autotrading7s.cli import db_path_for
from autotrading7s.engine.orchestrator import Orchestrator
from autotrading7s.engine.recovery import Recovery
from autotrading7s.ui.presenter import Presenter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autotrading7s")
    parser.add_argument("--env", choices=("mock", "real"), required=True)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--simulate", default=None,
                        help="쉼표로 구분한 가격 스크립트 (키움 어댑터 부재 시)")
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    db = db_path_for(args.env)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    apply_schema(conn)
    repo = SqliteRepository(conn)

    if args.simulate is None:
        print("키움 어댑터가 아직 구현되지 않았습니다 (Plan 3). 지금은 "
              "--simulate 로 시뮬레이션 브로커만 기동할 수 있습니다.",
              file=sys.stderr)
        return 2

    # 구체 어댑터는 조립 지점인 여기에서만 import 한다.
    from autotrading7s.adapters.fake.broker import FakeBroker
    from autotrading7s.adapters.fake.clock import FakeClock
    from autotrading7s.ui.widgets.main_window import MainWindow

    broker = FakeBroker([int(p) for p in args.simulate.split(",")],
                        validate_account=True)
    # **주의: FakeClock 은 흐르지 않는다.** 재매수 쿨다운과 미체결 타임아웃이
    # 실제로 동작하지 않으므로, 이 기동 경로는 화면 확인용이다. KiwoomClock
    # (설계서 18.2절, 구현 2단계)이 그것을 대체한다.
    clock = FakeClock(current=datetime.now(UTC))
    thread = EngineThread(
        orchestrator_factory=lambda **qs: Orchestrator(
            repo=repo, broker=broker, clock=clock, settings=settings,
            max_fallback_rounds=3, **qs),
        # 복구도 이벤트를 낸다 — 기동 직후가 그 이벤트가 가장 나올 만한 시점이다.
        recovery_factory=lambda **qs: Recovery(
            repo=repo, broker=broker, clock=clock, emit=qs["event_q"].put),
    )
    thread.start()
    MainWindow(thread=thread, presenter=Presenter(args.env)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
