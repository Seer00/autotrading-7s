"""headless 기동 — 설계서 14.4절, 16절.

GUI 없이 엔진만 돌린다. EC2 에서 자동 테스트할 수 있는 경로가 이것뿐이다
(설계서 18.1 리스크 7).

**키움 어댑터가 없다는 사실을 숨기지 않는다.** 조용히 시뮬레이션으로 대체하면
사용자가 실전이라고 믿는 채로 가짜 브로커에 주문을 낸다.

DB 경로 분리(D15)는 여기서 확정한다 — `--env` 가 `data/mock/` 과 `data/real/`
을 가른다. 한 파일을 공유하면 모의투자의 체결 기록이 실전 사이클의 목표가
계산에 섞여 들어갈 수 있다.
"""

from __future__ import annotations

import argparse
import asyncio
import queue
import sys
from datetime import UTC, datetime
from pathlib import Path

from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.app.settings import load_settings
from autotrading7s.app.snapshot import Snapshot
from autotrading7s.engine.orchestrator import Orchestrator
from autotrading7s.engine.recovery import Recovery
from autotrading7s.ui.presenter import Presenter
from autotrading7s.ui.text_render import render_holdings, render_status_bar

_DB_PATHS = {
    "mock": Path("data/mock/autotrading7s.db"),
    "real": Path("data/real/autotrading7s.db"),
}


def db_path_for(env: str) -> Path:
    """D15 — 모의투자와 실전의 DB 파일이 절대 섞이지 않는다."""
    try:
        return _DB_PATHS[env]
    except KeyError:
        raise ValueError(
            f"env must be one of {sorted(_DB_PATHS)}: {env!r}"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autotrading7s.cli")
    parser.add_argument("--env", choices=sorted(_DB_PATHS), required=True)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=None,
                        help="DB 경로 재지정 (기본값은 --env 가 정한다)")
    parser.add_argument("--simulate", default=None,
                        help="쉼표로 구분한 가격 스크립트. 시뮬레이션 "
                             "브로커로 기동한다.")
    parser.add_argument("--status", action="store_true",
                        help="스냅샷마다 보유현황 표를 표준출력에 찍는다 "
                             "(설계서 14.1절의 텍스트 재현).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.settings)
    db = args.db if args.db is not None else db_path_for(args.env)
    db.parent.mkdir(parents=True, exist_ok=True)

    if args.simulate is None:
        print(
            "키움 어댑터가 아직 구현되지 않았습니다 (Plan 3). 지금은 "
            "--simulate 로 시뮬레이션 브로커만 기동할 수 있습니다.",
            file=sys.stderr,
        )
        return 2

    # 구체 어댑터는 조립 지점인 여기(app 층)에서만 import 한다 — `engine/` 은
    # `adapters/` 를 알지 못한다 (설계서 7.2절).
    from autotrading7s.adapters.fake.broker import FakeBroker
    from autotrading7s.adapters.fake.clock import FakeClock

    script = [int(p) for p in args.simulate.split(",")]
    conn = connect(db)
    apply_schema(conn)
    repo = SqliteRepository(conn)
    broker = FakeBroker(script, validate_account=True)
    clock = FakeClock(current=datetime.now(UTC))
    event_q: queue.Queue = queue.Queue()

    # 프레젠터 사슬 전체를 여기서 돌린다 — 스냅샷 발행 → 프레젠터 소비 →
    # 뷰모델 → 렌더러. 그 사슬이 Windows 에서 처음 돌면 어디가 틀렸는지 알기
    # 어렵다.
    presenter = Presenter(args.env)

    async def run() -> None:
        stop = asyncio.Event()

        async def drain() -> None:
            """이벤트를 프레젠터에 먹이고 스냅샷마다 화면을 찍는다.

            `asyncio.sleep(0)` 은 양보만 하므로 실제로 잠들지 않는다 — 이
            파일은 조립 층이므로 오케스트레이터의 "주입된 sleep" 규칙이
            적용되지 않는다.
            """
            while True:
                drew = False
                while True:
                    try:
                        event = event_q.get_nowait()
                    except queue.Empty:
                        break
                    presenter.consume(event)
                    drew = drew or isinstance(event, Snapshot)
                if drew and args.status:
                    print(render_holdings(presenter.holdings()))
                    print(render_status_bar(presenter.status_bar()))
                if stop.is_set():
                    return
                await asyncio.sleep(0)

        await Recovery(repo=repo, broker=broker, clock=clock,
                       emit=event_q.put).run()
        drainer = asyncio.create_task(drain())
        try:
            await Orchestrator(
                repo=repo, broker=broker, clock=clock, settings=settings,
                command_q=queue.Queue(), priority_q=queue.Queue(),
                event_q=event_q, max_fallback_rounds=3,
            ).run()
        finally:
            stop.set()
            await drainer

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    # `python -m autotrading7s.cli` 가 실제로 `main()` 을 부르게 만든다.
    # 이 블록이 없으면 모듈이 import 되고 `main` 이 정의된 채 종료 코드 0 으로
    # 끝난다 — 아무것도 하지 않았는데 성공으로 보이는 침묵이며, 설계서 14.4
    # 절의 headless 경로와 Plan 4 체크리스트 21번(두 렌더러의 대조)이 전부
    # 이 명령에 의존한다.
    raise SystemExit(main())
