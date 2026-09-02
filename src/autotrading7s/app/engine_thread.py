"""스레드 브리지 — 설계서 7.1절.

큐를 소유하고 엔진 스레드를 띄운다. GUI(Tkinter 메인 스레드)는 이 객체의
`send`·`send_priority`·`drain_events` 만 쓰며 DB 를 직접 건드리지 않는다 —
그 규칙이 리포지토리의 단일 작성자 전제를 성립시킨다 (2A 핸드오버 3).

복구가 오케스트레이터보다 먼저 돈다. 미체결 주문이 아직 정정되지 않은 상태에서
판정하면 그 단계가 PENDING 이 아니라 WAITING 으로 보여 중복 발주가 된다.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from typing import Any

from autotrading7s.app.commands import Command, PriorityCommand
from autotrading7s.app.events import Event


class EngineThread:
    def __init__(
        self, *, orchestrator_factory: Callable[..., Any],
        recovery_factory: Callable[..., Any],
    ) -> None:
        self.command_q: queue.Queue = queue.Queue()
        self.priority_q: queue.Queue = queue.Queue()
        self.event_q: queue.Queue = queue.Queue()
        self._orchestrator_factory = orchestrator_factory
        self._recovery_factory = recovery_factory
        self._thread: threading.Thread | None = None
        self._orchestrator: Any = None
        self._error: BaseException | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException as exc:        # noqa: BLE001 — 스레드 경계다
            # 스레드에서 던진 예외는 아무도 보지 못한다. 붙잡아 두고
            # `raise_if_failed` 로 메인 스레드가 확인할 수 있게 한다 —
            # 조용히 죽은 엔진은 "프로그램이 켜져 있는데 트리거를 놓치는"
            # 최악의 상태다 (설계서 18.1 리스크 6).
            self._error = exc

    async def _main(self) -> None:
        # 복구도 이벤트를 낸다 — **기동 직후가 CycleLoadFailed·ReconcileMismatch
        # 가 가장 나올 만한 시점이므로** event_q 를 넘겨야 한다. 넘기지 않으면
        # 정확히 필요한 순간에 화면이 조용하다.
        await self._recovery_factory(event_q=self.event_q).run()
        self._orchestrator = self._orchestrator_factory(
            command_q=self.command_q, priority_q=self.priority_q,
            event_q=self.event_q,
        )
        await self._orchestrator.run()

    def send(self, command: Command) -> None:
        self.command_q.put(command)

    def send_priority(self, command: PriorityCommand) -> None:
        """긴급 명령만 받는다.

        일반 명령이 들어가면 우선순위 보장이 무의미해진다 — 명령 계약이 자격을
        타입으로 표현했으므로 여기서 단정할 수 있다.
        """
        if not isinstance(command, PriorityCommand):
            raise TypeError(
                f"priority_q accepts PriorityCommand only, got "
                f"{type(command).__name__}"
            )
        self.priority_q.put(command)

    def drain_events(self) -> list[Event]:
        """GUI 가 `root.after(200ms)` 마다 부른다."""
        out: list[Event] = []
        while True:
            try:
                out.append(self.event_q.get_nowait())
            except queue.Empty:
                return out

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def raise_if_failed(self) -> None:
        """엔진 스레드가 예외로 죽었으면 여기서 다시 던진다."""
        if self._error is not None:
            raise self._error

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
