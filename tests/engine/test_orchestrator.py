from __future__ import annotations

import inspect
import queue
from datetime import UTC, datetime, timedelta

import pytest

from autotrading7s.adapters.fake.broker import FailMode, FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.commands import (
    EmergencyLiquidate,
    PauseCycle,
    ResetReconcileBaseline,
    ResumeCycle,
    Shutdown,
    StartCycle,
)
from autotrading7s.app.events import (
    CycleClosed,
    CycleLoadFailed,
    EmergencyResult,
    GuardBlocked,
    QuoteFallback,
    StageFilled,
)
from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource
from autotrading7s.engine.orchestrator import Orchestrator

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _build(repo, broker, *, total_limit=100_000_000, max_orders=60):
    clock = FakeClock(current=AT)
    qs = (queue.Queue(), queue.Queue(), queue.Queue())

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    orch = Orchestrator(
        repo=repo, broker=broker, clock=clock,
        settings=EngineSettings(total_limit=total_limit,
                                max_orders_per_minute=max_orders),
        command_q=qs[0], priority_q=qs[1], event_q=qs[2], sleep=sleep,
        # 재구독이 즉시 다시 끊기는 시뮬레이션에서 run() 이 무한 루프가 되지
        # 않도록 유한한 값을 준다. 기본값 None(무한)은 실전용이다.
        max_fallback_rounds=1,
    )
    return orch, clock, qs


def _drain(event_q):
    out = []
    while not event_q.empty():
        out.append(event_q.get_nowait())
    return out


def _tick(price: int, at=AT, source=TickSource.WS) -> Tick:
    return Tick(code="005930", price=price, at=at, source=source)


@pytest.mark.asyncio
async def test_priority_queue_is_consumed_first(repo_two_stocks):
    """설계서 7.1절 — priority_q 가 긴급 기능의 즉시성을 구조적으로 보장한다.

    일반 명령이 100건 쌓여 있어도 긴급청산이 먼저 처리돼야 한다. 순서가
    뒤바뀌면 급락 중에 청산이 100건 뒤로 밀린다.
    """
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    orch, clock, (command_q, priority_q, event_q) = _build(repo_two_stocks,
                                                           broker)
    # **소비 순서를 직접 관측한다.** 이벤트 순서로는 구별되지 않는다 —
    # PauseCycle 은 이벤트를 내지 않으므로, 우선순위가 뒤바뀌어도 첫 이벤트는
    # 여전히 EmergencyResult 다. 그 테스트는 통과하지만 아무것도 지키지 않는다.
    seen: list[str] = []
    original = orch._handle

    async def spy(command):
        seen.append(type(command).__name__)
        await original(command)

    orch._handle = spy                      # type: ignore[method-assign]
    for _ in range(100):
        command_q.put(PauseCycle(config_id=2))
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="긴급", confirmed_text=None))

    await orch.drain_commands()

    assert seen[0] == "EmergencyLiquidate", (
        "priority_q 를 먼저 비우지 않으면 급락 중에 청산이 100건 뒤로 밀린다")
    assert seen.count("PauseCycle") == 100
    events = _drain(event_q)
    assert isinstance(events[0], EmergencyResult)
    assert events[0].result == "SUCCESS"


@pytest.mark.asyncio
async def test_start_cycle_confirms_the_anchor_on_the_first_tick(repo_fresh):
    """앵커는 GUI 가 정하지 않는다 — 엔진이 첫 틱의 가격으로 확정한다.

    STARTING 은 트리거를 받지 않으므로(도메인 accepts_triggers), 앵커 확정
    전에는 어떤 주문도 나가지 않는다.
    """
    # 픽스처의 사이클을 닫고 새로 시작시킨다 — StartCycle 경로를 보려면
    # STARTING 사이클이 필요하다.
    repo_fresh._conn.execute("UPDATE cycle SET status = 'CLOSED', "
                             "close_reason = 'NORMAL', closed_at = ?",
                             (AT.isoformat(),))
    repo_fresh._conn.commit()
    broker = FakeBroker([9_800], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _build(repo_fresh, broker)

    command_q.put(StartCycle(config_id=1))
    await orch.drain_commands()
    starting = [c for c in repo_fresh.load_active_cycles()
                if c.status is CycleStatus.STARTING]
    assert starting, "StartCycle 이 STARTING 사이클을 만들어야 한다"
    assert repo_fresh.load_config(1).status == "ACTIVE"
    assert await broker.list_orders_today("005930") == []

    await orch.on_tick(_tick(9_800))

    running = [c for c in repo_fresh.load_active_cycles()
               if c.status is CycleStatus.RUNNING]
    assert running[0].anchor_price == 9_800
    assert running[0].ladder is not None


@pytest.mark.asyncio
async def test_buy_trigger_places_an_order_and_fills(repo_fresh):
    """틱 → decide() → 가드 → 발주 → 체결 반영의 한 바퀴."""
    broker = FakeBroker([10_000, 9_500], validate_account=True,
                        cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.on_tick(_tick(9_500))
    await orch.poll_pending()

    cyc = repo_fresh.load_active_cycles()[0]
    holding = [s.stage_no for s in repo_fresh.load_stages(cyc.cycle_id)
               if s.status is StageStatus.HOLDING]
    assert holding == [1]          # 규칙 2 — 가장 낮은 대기 단계부터
    assert any(isinstance(e, StageFilled) for e in _drain(event_q))


@pytest.mark.asyncio
async def test_guard_block_emits_an_event_and_places_nothing(repo_fresh):
    """② 가드 실패 시 로그만 남기고 종료한다 (설계서 9절)."""
    broker = FakeBroker([9_500], validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker, total_limit=1)

    await orch.on_tick(_tick(9_500))

    blocked = [e for e in _drain(event_q) if isinstance(e, GuardBlocked)]
    assert len(blocked) == 1
    assert "총한도" in blocked[0].reason
    assert await broker.list_orders_today("005930") == []


@pytest.mark.asyncio
async def test_cycle_closes_when_the_last_share_is_sold(repo_fresh):
    """D5 — 사이클 종료는 보유 0 도달로만 일어난다.

    종료 시 realized_pnl 을 기록하고 설정을 IDLE 로 돌리는 것이 엔진의 몫이다
    (2A 핸드오버 2·6).
    """
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.on_tick(_tick(10_000))
    await orch.poll_pending()
    cyc = repo_fresh.load_active_cycles()[0]
    held = repo_fresh.load_stages(cyc.cycle_id)[0]
    assert held.status is StageStatus.HOLDING

    target = 10_500                       # target_price(10_000, 5%)
    clock.advance(120)                    # 쿨다운 경과
    await orch.on_tick(_tick(target, at=AT + timedelta(seconds=120)))
    await orch.poll_pending()

    closed = [e for e in _drain(event_q) if isinstance(e, CycleClosed)]
    assert len(closed) == 1
    assert repo_fresh.load_config(1).status == "IDLE"
    row = repo_fresh._conn.execute(
        "SELECT realized_pnl FROM cycle WHERE id = ?", (closed[0].cycle_id,)
    ).fetchone()
    assert dict(row)["realized_pnl"] == closed[0].realized_pnl
    assert closed[0].realized_pnl == (target - 10_000) * 100


@pytest.mark.asyncio
async def test_a_fresh_cycle_is_not_closed_immediately(repo_fresh):
    """갓 시작한 사이클은 전 단계가 WAITING 이므로 is_cycle_complete 가 True 다.

    그것으로 닫으면 아무것도 사지 않은 사이클이 즉시 종료된다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.poll_pending()

    assert repo_fresh.load_active_cycles()          # 여전히 활성
    assert [e for e in _drain(event_q) if isinstance(e, CycleClosed)] == []


@pytest.mark.asyncio
async def test_websocket_drop_falls_back_to_rest_and_keeps_deciding(repo_fresh):
    """설계서 8.4절 — 끊겨도 트리거 판정은 계속 수행한다.

    폴백 중에 판정을 멈추면 급락 구간에서 매수 기회를 통째로 놓치고, 더
    나쁘게는 목표가 도달한 매도를 놓친다.

    시나리오 구성: 첫 WS 틱 9,500 이 1단계를 사고(규칙 2 — 한 틱에 한 단계),
    그 직후 스트림이 끊긴다. 폴백이 같은 가격 9,500 을 폴링하면 **2단계**가
    발동가에 걸려 있으므로 REST 경로로 주문이 나가야 한다. 판정이 멈추면 그
    주문이 없다.
    """
    broker = FakeBroker([9_500, 9_400], validate_account=True,
                        cash=100_000_000, fail_mode=FailMode.DISCONNECT,
                        fail_after=1)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.run()

    fallbacks = [e for e in _drain(event_q) if isinstance(e, QuoteFallback)]
    assert fallbacks and fallbacks[0].active is True
    # 폴백 구간에서도 주문이 나갔다 — 판정이 멈추지 않았다는 증거
    rows = [dict(r) for r in repo_fresh._conn.execute(
        "SELECT tick_source, req_price FROM order_log ORDER BY id").fetchall()]
    assert [r["tick_source"] for r in rows] == ["WS", "REST_POLL"]
    cyc = repo_fresh.load_active_cycles()[0]
    holding = [s.stage_no for s in repo_fresh.load_stages(cyc.cycle_id)
               if s.status is StageStatus.HOLDING]
    assert holding == [1, 2]


@pytest.mark.asyncio
async def test_shutdown_stops_the_run_loop(repo_fresh):
    broker = FakeBroker([10_000] * 50, fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, _) = _build(repo_fresh, broker)
    command_q.put(Shutdown())

    await orch.run()

    assert orch.stopped is True


@pytest.mark.asyncio
async def test_pause_and_resume_touch_only_the_cycle(repo_two_stocks):
    """일시정지는 사이클의 상태다 — 설정은 ACTIVE 로 남는다 (원장 Ruling 1)."""
    broker = FakeBroker([10_000])
    orch, clock, (command_q, _, _) = _build(repo_two_stocks, broker)

    command_q.put(PauseCycle(config_id=1))
    await orch.drain_commands()
    assert (next(c for c in repo_two_stocks.load_active_cycles()
                 if c.config_id == 1).status is CycleStatus.PAUSED)
    assert repo_two_stocks.load_config(1).status == "ACTIVE"

    command_q.put(ResumeCycle(config_id=1))
    await orch.drain_commands()
    assert (next(c for c in repo_two_stocks.load_active_cycles()
                 if c.config_id == 1).status is CycleStatus.RUNNING)


@pytest.mark.asyncio
async def test_paused_cycle_places_no_orders(repo_fresh):
    """규칙 — PAUSED 는 트리거를 받지 않는다 (도메인 accepts_triggers)."""
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, _) = _build(repo_fresh, broker)
    command_q.put(PauseCycle(config_id=1))
    await orch.drain_commands()

    await orch.on_tick(_tick(10_000))

    assert await broker.list_orders_today("005930") == []


@pytest.mark.asyncio
async def test_reset_reconcile_baseline_command_is_handled(repo_two_stocks):
    broker = FakeBroker([10_000])
    orch, clock, (command_q, _, _) = _build(repo_two_stocks, broker)
    command_q.put(ResetReconcileBaseline(stock_code="005930"))

    await orch.drain_commands()

    row = repo_two_stocks._conn.execute(
        "SELECT action_taken FROM reconcile_log"
    ).fetchone()
    assert dict(row)["action_taken"] == "BASELINE_RESET"


@pytest.mark.asyncio
async def test_empty_stage_set_pauses_instead_of_crashing(repo_fresh):
    """Plan 1 핸드오버 5 — `is_cycle_complete([])` 가 DomainInvariantError 다.

    엔진이 그것을 흡수하지 않으면 단계 행이 사라진 사이클 하나가 틱 루프를
    죽인다. 그러면 다른 종목의 매도도 함께 멈춘다.
    """
    cyc = repo_fresh.load_active_cycles()[0]
    repo_fresh._conn.execute("DELETE FROM stage_state WHERE cycle_id = ?",
                             (cyc.cycle_id,))
    repo_fresh._conn.commit()
    broker = FakeBroker([9_500], validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.on_tick(_tick(9_500))

    events = _drain(event_q)
    assert any(isinstance(e, CycleLoadFailed) for e in events)
    # 멈추는 것은 사이클이다 — 설정은 ACTIVE 로 남는다 (원장 Ruling 1)
    assert repo_fresh.load_active_cycles()[0].status is CycleStatus.PAUSED
    assert repo_fresh.load_config(1).status == "ACTIVE"


def test_orchestrator_never_sleeps_on_the_real_clock():
    """주입된 sleep 만 쓴다.

    asyncio.sleep 을 직접 부르면 G2 시나리오가 실제 시간을 소모하고,
    3초 타임아웃 테스트마다 3초가 든다.
    """
    from autotrading7s.engine import orchestrator as mod

    source = inspect.getsource(mod)
    assert "await asyncio.sleep" not in source
