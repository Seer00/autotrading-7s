from __future__ import annotations

import dataclasses
import queue
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.commands import Shutdown, StartCycle
from autotrading7s.app.settings import EngineSettings
from autotrading7s.app.snapshot import Snapshot
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource
from autotrading7s.engine.orchestrator import Orchestrator

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _build(repo, broker, *, total_limit=100_000_000):
    clock = FakeClock(current=AT)
    qs = (queue.Queue(), queue.Queue(), queue.Queue())

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    orch = Orchestrator(
        repo=repo, broker=broker, clock=clock,
        settings=EngineSettings(total_limit=total_limit,
                                max_orders_per_minute=60),
        command_q=qs[0], priority_q=qs[1], event_q=qs[2], sleep=sleep,
        max_fallback_rounds=1,
    )
    return orch, clock, qs


def _snapshots(event_q):
    out = []
    while not event_q.empty():
        e = event_q.get_nowait()
        if isinstance(e, Snapshot):
            out.append(e)
    return out


def _tick(price, at=AT):
    return Tick(code="005930", price=price, at=at, source=TickSource.WS)


def test_snapshot_lists_every_config_including_idle_ones(repo_two_stocks):
    """설계서 14.1절 목업의 `NAVER 0/5 IDLE` 행을 그릴 수 있어야 한다."""
    base = repo_two_stocks.load_config(1)
    repo_two_stocks.save_config(dataclasses.replace(
        base, config_id=None, stock_code="035420", stock_name="NAVER",
        label="기본", max_stages=5, amount_per_stage=1_000_000,
        allow_rebuy=False, total_limit=5_000_000, status="IDLE"))
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker)

    snap = orch.build_snapshot()

    codes = [c.stock_code for c in snap.configs]
    assert codes == ["005930", "000660", "035420"]
    naver = snap.configs[-1]
    assert naver.config_status == "IDLE"
    assert naver.cycle_id is None
    assert naver.stages == ()
    assert naver.max_stages == 5


def test_snapshot_carries_the_total_limit_from_settings(repo_two_stocks):
    """상태바의 `총한도 9,971,350 / 21,000,000` 오른쪽 숫자다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker, total_limit=21_000_000)
    assert orch.build_snapshot().total_limit == 21_000_000


def test_snapshot_carries_stages_and_pending_order_counts(repo_two_stocks):
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker)

    snap = orch.build_snapshot()
    samsung = snap.configs[0]

    assert len(samsung.stages) == 7
    assert samsung.stages[0].status is StageStatus.HOLDING
    assert samsung.cycle_status is CycleStatus.RUNNING
    assert samsung.anchor_price == 10_000
    assert samsung.ladder is not None
    assert samsung.pending_orders == 0


@pytest.mark.asyncio
async def test_pending_order_count_is_per_config(repo_two_stocks):
    """긴급청산 다이얼로그의 '미체결 매수주문 2건이 함께 취소됩니다' 안내."""
    from autotrading7s.domain.rules import BuyStage
    from autotrading7s.engine.executor import Executor

    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex = Executor(repo=repo_two_stocks, broker=broker,
                  clock=FakeClock(current=AT), emit=lambda e: None)
    waiting = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.stage_no == 2)
    await ex.send(cycle=cyc, config=config, stage=waiting,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=_tick(9_500))

    orch, _, _ = _build(repo_two_stocks, broker)
    snap = orch.build_snapshot()

    assert snap.configs[0].pending_orders == 1
    assert snap.configs[1].pending_orders == 0


def test_a_corrupt_cycle_still_appears_with_no_stages(repo_two_stocks):
    """2A 핸드오버 7 — 사용자에게 나갈 길의 최소 조건은 그 설정이 보이는 것이다.

    표에서 사라지면 사용자는 그것이 존재하는지조차 모른다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    repo_two_stocks._conn.execute(
        "UPDATE stage_state SET trigger_price = trigger_price + 7 "
        "WHERE cycle_id = ? AND stage_no = 1", (cyc.cycle_id,))
    repo_two_stocks._conn.commit()
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker)

    snap = orch.build_snapshot()

    samsung = snap.configs[0]
    assert samsung.stages == ()
    assert samsung.cycle_id == cyc.cycle_id          # 사이클은 여전히 보인다
    assert samsung.cycle_status is CycleStatus.RUNNING
    assert len(snap.configs) == 2                    # 다른 종목도 그대로


def test_emit_is_skipped_when_nothing_changed(repo_two_stocks):
    """유휴 틱마다 스냅샷이 나가면 큐가 자란다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, (_, _, event_q) = _build(repo_two_stocks, broker)

    assert orch.emit_snapshot_if_changed() is True
    assert orch.emit_snapshot_if_changed() is False
    assert orch.emit_snapshot_if_changed() is False
    assert len(_snapshots(event_q)) == 1


@pytest.mark.asyncio
async def test_emit_fires_when_a_stage_changes(repo_fresh):
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _build(repo_fresh, broker)
    orch.emit_snapshot_if_changed()
    _snapshots(event_q)

    await orch.on_tick(_tick(10_000))
    await orch.poll_pending()

    assert orch.emit_snapshot_if_changed() is True
    snaps = _snapshots(event_q)
    assert snaps and snaps[-1].configs[0].stages[0].status is StageStatus.HOLDING


@pytest.mark.asyncio
async def test_run_emits_a_snapshot_before_the_first_tick(repo_two_stocks):
    """GUI 는 기동 직후 화면을 그려야 한다 — 첫 틱을 기다릴 수 없다.

    장이 열리기 전이나 IDLE 설정만 있는 상태에서도 표가 보여야 한다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(Shutdown())

    await orch.run()

    snaps = _snapshots(event_q)
    assert snaps, "run() 이 시작 직후 스냅샷을 내지 않았다"
    assert len(snaps[0].configs) == 2


@pytest.mark.asyncio
async def test_run_emits_a_snapshot_even_with_no_active_cycles(repo_fresh):
    """활성 사이클이 없으면 run() 이 바로 반환한다 — 그래도 스냅샷은 나가야 한다."""
    repo_fresh._conn.execute(
        "UPDATE cycle SET status = 'CLOSED', close_reason = 'NORMAL', "
        "closed_at = ?", (AT.isoformat(),))
    repo_fresh._conn.commit()
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.run()

    snaps = _snapshots(event_q)
    assert snaps and snaps[0].configs[0].cycle_id is None


@pytest.mark.asyncio
async def test_command_handling_emits_a_snapshot(repo_fresh):
    """[시작]을 눌렀는데 화면이 그대로면 사용자는 눌렸는지 알 수 없다."""
    repo_fresh._conn.execute(
        "UPDATE cycle SET status = 'CLOSED', close_reason = 'NORMAL', "
        "closed_at = ?", (AT.isoformat(),))
    repo_fresh._conn.commit()
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, (command_q, _, event_q) = _build(repo_fresh, broker)
    orch.emit_snapshot_if_changed()
    _snapshots(event_q)

    command_q.put(StartCycle(config_id=1))
    await orch.drain_commands()

    snaps = _snapshots(event_q)
    assert snaps, "명령 처리 후 스냅샷이 나가지 않았다"
    assert snaps[-1].configs[0].cycle_status is CycleStatus.STARTING
    assert snaps[-1].configs[0].config_status == "ACTIVE"
