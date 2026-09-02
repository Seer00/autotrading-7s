from __future__ import annotations

import asyncio
import queue
from datetime import UTC, datetime

import pytest

from autotrading7s.app.commands import PauseCycle, Shutdown, StartCycle
from autotrading7s.app.engine_thread import EngineThread
from autotrading7s.app.events import EngineStopped, Event

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


class _Orchestrator:
    """큐만 소비하는 최소 오케스트레이터 — 스레드 브리지 자체를 검증한다."""

    def __init__(self, command_q, priority_q, event_q):
        self.command_q = command_q
        self.priority_q = priority_q
        self.event_q = event_q
        self.seen: list[object] = []
        self.stopped = False

    async def run(self):
        # 유한한 상한을 둔다 — 무한 루프면 Shutdown 이 유실될 때 테스트가
        # 영원히 걸린다.
        for _ in range(100_000):
            if self.stopped:
                break
            for q in (self.priority_q, self.command_q):
                try:
                    command = q.get_nowait()
                except queue.Empty:
                    continue
                self.seen.append(command)
                if isinstance(command, Shutdown):
                    self.stopped = True
            await asyncio.sleep(0)
        self.event_q.put(EngineStopped(detail=None, at=AT))


class _Recovery:
    def __init__(self):
        self.ran = False

    async def run(self):
        self.ran = True


def test_send_priority_rejects_a_plain_command():
    """priority_q 에 일반 명령이 들어가면 우선순위 보장이 무의미해진다.

    타입이 자격을 표현하므로 브리지가 그것을 단정할 수 있다.
    """
    thread = EngineThread(
        orchestrator_factory=lambda **kw: _Orchestrator(**kw),
        recovery_factory=_Recovery,
    )
    with pytest.raises(TypeError, match="PriorityCommand"):
        thread.send_priority(PauseCycle(config_id=1))


def test_recovery_runs_before_the_orchestrator():
    """설계서 10.1절 — 복구가 끝나기 전에 트리거 판정을 시작하면 안 된다.

    미체결 주문이 아직 정정되지 않은 상태에서 판정하면 그 단계가 PENDING 이
    아니라 WAITING 으로 보여 중복 발주가 된다.
    """
    recovery = _Recovery()
    order: list[str] = []
    orch_box: list[_Orchestrator] = []

    def make_orch(**kw):
        order.append("orchestrator")
        orch = _Orchestrator(**kw)
        orch_box.append(orch)
        return orch

    def make_recovery():
        order.append("recovery")
        return recovery

    thread = EngineThread(orchestrator_factory=make_orch,
                          recovery_factory=make_recovery)
    thread.send(Shutdown())
    thread.start()
    thread.stop()
    thread.raise_if_failed()

    assert recovery.ran is True
    assert order == ["recovery", "orchestrator"]


def test_commands_reach_the_engine_and_events_come_back():
    orch_box: list[_Orchestrator] = []

    def make_orch(**kw):
        orch = _Orchestrator(**kw)
        orch_box.append(orch)
        return orch

    thread = EngineThread(orchestrator_factory=make_orch,
                          recovery_factory=_Recovery)
    thread.send(StartCycle(config_id=3))
    thread.send(Shutdown())
    thread.start()
    thread.stop()
    thread.raise_if_failed()

    assert any(isinstance(c, StartCycle) for c in orch_box[0].seen)
    events = thread.drain_events()
    assert events and all(isinstance(e, Event) for e in events)


def test_stop_joins_the_thread():
    thread = EngineThread(
        orchestrator_factory=lambda **kw: _Orchestrator(**kw),
        recovery_factory=_Recovery,
    )
    thread.send(Shutdown())
    thread.start()
    thread.stop()
    assert thread.is_alive() is False


def test_a_crashed_engine_is_not_silent():
    """조용히 죽은 엔진은 "프로그램이 켜져 있는데 트리거를 놓치는" 최악의
    상태다 (설계서 18.1 리스크 6).

    스레드에서 던진 예외는 아무도 보지 못하므로 붙잡아 두고 메인 스레드가
    확인할 수 있게 한다.
    """
    class _Boom:
        async def run(self):
            raise RuntimeError("복구 실패")

    thread = EngineThread(
        orchestrator_factory=lambda **kw: _Orchestrator(**kw),
        recovery_factory=_Boom,
    )
    thread.start()
    thread.stop()

    assert thread.is_alive() is False
    with pytest.raises(RuntimeError, match="복구 실패"):
        thread.raise_if_failed()
