"""G2 게이트 — 설계서 15.2절.

G1 이 도메인 계약의 조합을, G2a 가 영속성 계약의 조합을 검증했듯, 이 게이트는
**엔진의 조합**을 검증한다. 시나리오 대부분은 모의투자 계좌로 재현할 수 없다 —
갭하락을 주문해서 만들 수 없고, 응답 타임아웃을 유발할 수 없고, WebSocket 을
끊었다 붙일 수도 없다. **그래서 G2 가 G3 보다 넓다.**

기대 실현손익을 손으로 적지 않고 사다리에서 계산한다. Plan 2A 에서 절대 숫자를
적어 여섯 번 틀렸고, 근본 원인은 "계산 결과를 문서에 박아두면 정확성을 유지할 수
없다" 는 것이었다. 엔진은 지정가를 틱에서·수량을 단계 기록에서 가져오고 테스트는
사다리에서 가져오므로 일치는 여전히 실질적 검증이다.
"""

from __future__ import annotations

import queue
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autotrading7s.adapters.fake.broker import FailMode, FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.app.commands import (
    EmergencyLiquidate,
    ForceClose,
    StartCycle,
)
from autotrading7s.app.events import (
    CycleClosed,
    GuardBlocked,
    QuoteFallback,
    ReconcileMismatch,
)
from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.ladder import target_price
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    StageStatus,
    Tick,
    TickSource,
)
from autotrading7s.engine.orchestrator import Orchestrator
from autotrading7s.engine.recovery import Recovery
from autotrading7s.ports.repository import SplitConfig

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
ANCHOR = 10_000
PCT = Decimal("0.05")


def _repo(tmp_path, *, allow_rebuy=False, amount=1_000_000, stages=7,
          limit=99_999_999):
    conn = connect(tmp_path / "g2.db")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    repo.save_config(SplitConfig(
        config_id=None, stock_code="005930", stock_name="삼성전자", label=None,
        max_stages=stages, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=amount, allow_rebuy=allow_rebuy,
        rebuy_cooldown_sec=60, total_limit=limit, status="IDLE",
        created_at=AT, updated_at=AT,
    ))
    return repo


def _engine(repo, broker, *, total_limit=99_999_999, max_orders=60,
            clock=None):
    clock = clock or FakeClock(current=AT)
    qs = (queue.Queue(), queue.Queue(), queue.Queue())

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    orch = Orchestrator(
        repo=repo, broker=broker, clock=clock,
        settings=EngineSettings(total_limit=total_limit,
                                max_orders_per_minute=max_orders),
        command_q=qs[0], priority_q=qs[1], event_q=qs[2], sleep=sleep,
        max_fallback_rounds=1,
    )
    return orch, clock, qs


def _start(orch, command_q):
    """설정을 시작시켜 STARTING 사이클을 만든다."""
    command_q.put(StartCycle(config_id=1))


def _events(event_q):
    out = []
    while not event_q.empty():
        out.append(event_q.get_nowait())
    return out


def _ladder(repo):
    return repo.load_config(1).to_ladder(anchor_price=ANCHOR)


def _tick(price, at=AT, source=TickSource.WS):
    return Tick(code="005930", price=price, at=at, source=source)


def _cycle_id(repo):
    return dict(repo._conn.execute("SELECT id FROM cycle").fetchone())["id"]


async def _boot(orch, command_q, price=ANCHOR):
    """StartCycle → 첫 틱으로 앵커 확정까지."""
    _start(orch, command_q)
    await orch.drain_commands()
    await orch.on_tick(_tick(price))


# ══ 1. 7단계 전 사이클 ═══════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_full_seven_stage_cycle(tmp_path):
    """하락 → 단계별 매수 → 반등 → 단계별 매도 → 보유 0 → IDLE."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    downs = [ladder.trigger_price(n) for n in range(1, 8)]
    ups = [target_price(ladder.trigger_price(n), PCT) for n in range(7, 0, -1)]
    broker = FakeBroker([ANCHOR, *downs, *ups], validate_account=True,
                        cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker)
    _start(orch, command_q)

    await orch.run()

    row = dict(repo._conn.execute(
        "SELECT id, status, close_reason, realized_pnl FROM cycle"
    ).fetchone())
    assert row["status"] == "CLOSED"
    assert row["close_reason"] == "NORMAL"

    expected = sum(
        (target_price(ladder.trigger_price(n), PCT) - ladder.trigger_price(n))
        * ladder.planned_qty(n)
        for n in range(1, 8)
    )
    assert row["realized_pnl"] == expected
    assert repo.load_config(1).status == "IDLE"
    assert repo.holdings() == []

    orders = [dict(o) for o in repo._conn.execute(
        "SELECT side, req_qty FROM order_log ORDER BY id").fetchall()]
    assert [o["side"] for o in orders] == ["BUY"] * 7 + ["SELL"] * 7
    assert [o["req_qty"] for o in orders][:7] == [
        ladder.planned_qty(n) for n in range(1, 8)]
    closed = [e for e in _events(event_q) if isinstance(e, CycleClosed)]
    assert len(closed) == 1 and closed[0].realized_pnl == expected


# ══ 2. 갭하락 3단계 동시 통과 → 틱별 순차 매수 ═══════════════════════════
@pytest.mark.asyncio
async def test_g2_gap_down_buys_one_stage_per_tick(tmp_path):
    """규칙 2 — 갭하락으로 여러 단계가 한꺼번에 통과해도 한 틱에 하나만 산다.

    모의투자로는 갭하락을 주문해서 만들 수 없다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    gap = ladder.trigger_price(4)          # 4단계까지 한 번에 통과하는 가격
    broker = FakeBroker([ANCHOR, gap, gap, gap, gap], validate_account=True,
                        cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker)
    _start(orch, command_q)

    await orch.run()

    cid = _cycle_id(repo)
    holding = [s.stage_no for s in repo.load_stages(cid)
               if s.status is StageStatus.HOLDING]
    assert holding == [1, 2, 3, 4]
    buys = dict(repo._conn.execute(
        "SELECT count(*) AS c FROM order_log WHERE side = 'BUY'"
    ).fetchone())["c"]
    assert buys == 4                       # 틱 4개에 매수 4건


# ══ 3. 매도·매수 동시 충족 → 매도 우선 ═══════════════════════════════════
@pytest.mark.asyncio
async def test_g2_sell_wins_when_both_trigger(tmp_path):
    """규칙 1 — 매도가 하나라도 있으면 그 틱은 매도만 집행한다.

    3단계를 9,000원에 보유하고 1·2단계가 대기 중이면, 3단계의 목표가는
    1·2단계 발동가보다 낮다 — 그 가격 한 틱에서 매도 조건과 매수 조건이 함께
    성립한다. 그 상태는 틱으로 만들 수 없으므로(규칙 2 가 낮은 번호부터
    사므로) 단계 상태를 직접 시드한다.

    규칙 1 이 없으면 이 틱에서 1단계 매수도 함께 나가고, 그것이
    세븐스플릿에서 가장 나쁜 순서다 — 반등 중에 물타기가 일어난다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    cyc = repo.create_cycle(1, AT)
    cyc = cycle_mod.confirm_anchor(cyc, anchor_price=ANCHOR, ladder=ladder,
                                   at=AT)
    repo.save_cycle(cyc)
    repo.set_config_status(1, "ACTIVE", at=AT)
    for n in range(1, 8):
        st = stage_mod.StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n),
            planned_qty=ladder.planned_qty(n),
        )
        if n == 3:
            st = stage_mod.to_holding(stage_mod.to_buy_pending(st),
                                      fill_price=ladder.trigger_price(3),
                                      fill_qty=ladder.planned_qty(3), at=AT)
        repo.save_stage(cyc.cycle_id, st)

    target3 = target_price(ladder.trigger_price(3), PCT)
    assert target3 < ladder.trigger_price(2), "동시 충족 상황이 아니다"

    broker = FakeBroker([target3], validate_account=True, cash=100_000_000,
                        holdings={"005930": (ladder.planned_qty(3),
                                             ladder.trigger_price(3)
                                             * ladder.planned_qty(3))})
    orch, clock, (_, _, event_q) = _engine(repo, broker)

    await orch.on_tick(_tick(target3, at=AT + timedelta(seconds=1)))

    sides = [dict(o)["side"] for o in repo._conn.execute(
        "SELECT side FROM order_log ORDER BY id").fetchall()]
    assert sides == ["SELL"]


# ══ 4. 재매수 쿨다운 ════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_rebuy_cooldown(tmp_path):
    """규칙 3 — 60초 안의 재매수는 막히고, 지나면 다시 산다.

    쿨다운이 없으면 같은 단계가 수수료를 태우며 분당 수십 번 회전한다.

    시나리오 구성이 까다롭다: `allow_rebuy=True` 여도 **보유가 0 이 되면
    사이클이 종료된다**(설계서 4.2절 — 보유수량 0 도달 → CLOSED(NORMAL) →
    설정 IDLE, 다음 사이클은 사용자가 시작). `allow_rebuy` 는 사이클 안에서
    같은 단계를 다시 사는 옵션이고 사이클을 무한히 유지하는 옵션이 아니다.
    그래서 1·2단계를 사고 **2단계만** 팔아, 1단계가 보유를 유지한 채로
    2단계의 재매수 쿨다운을 관측한다.
    """
    repo = _repo(tmp_path, allow_rebuy=True)
    ladder = _ladder(repo)
    t1, t2 = ladder.trigger_price(1), ladder.trigger_price(2)
    target2 = target_price(t2, PCT)
    assert target2 < target_price(t1, PCT), "1단계는 계속 보유해야 한다"
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker, clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)

    def stage(n):
        return next(s for s in repo.load_stages(cid) if s.stage_no == n)

    await orch.on_tick(_tick(t1, at=AT + timedelta(seconds=1)))
    await orch.poll_pending()
    await orch.on_tick(_tick(t2, at=AT + timedelta(seconds=2)))
    await orch.poll_pending()
    assert stage(1).status is StageStatus.HOLDING
    assert stage(2).status is StageStatus.HOLDING

    # 2단계만 목표가 도달 → 매도. 1단계는 보유를 유지하므로 사이클은 열려 있다.
    await orch.on_tick(_tick(target2, at=AT + timedelta(seconds=3)))
    await orch.poll_pending()
    assert stage(2).status is StageStatus.WAITING
    assert stage(1).status is StageStatus.HOLDING
    assert repo.load_cycle(cid).status is CycleStatus.RUNNING

    # 쿨다운 안 — 사지 않는다
    clock.advance(30)
    await orch.on_tick(_tick(t2, at=AT + timedelta(seconds=33)))
    assert stage(2).status is StageStatus.WAITING

    # 쿨다운 경과 — 다시 산다
    clock.advance(31)
    await orch.on_tick(_tick(t2, at=AT + timedelta(seconds=64)))
    await orch.poll_pending()
    assert stage(2).status is StageStatus.HOLDING
    assert stage(2).rebuy_count == 1


# ══ 5. 미체결 3초 타임아웃 → 취소 → 재시도 ══════════════════════════════
@pytest.mark.asyncio
async def test_g2_pending_timeout_cancels_and_retries(tmp_path):
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, _, _) = _engine(repo, broker, clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)

    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))
    assert repo.load_stages(cid)[0].status is StageStatus.BUY_PENDING

    clock.advance(3.0)
    await orch.poll_pending()
    assert repo.load_stages(cid)[0].status is StageStatus.WAITING

    broker._fill_mode = FillMode.INSTANT
    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=5)))
    await orch.poll_pending()
    assert repo.load_stages(cid)[0].status is StageStatus.HOLDING


# ══ 6. 부분체결 매수·매도 비대칭 ════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_partial_fill_asymmetry(tmp_path):
    """매수 부분체결은 보유를 만들고, 매도 부분체결은 보유를 줄인다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    planned = ladder.planned_qty(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"), validate_account=True,
                        cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, _, _) = _engine(repo, broker, clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)

    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))
    clock.advance(3.0)
    await orch.poll_pending()

    bought = repo.load_stages(cid)[0]
    assert bought.status is StageStatus.HOLDING
    assert bought.fill_qty == int(planned * Decimal("0.4"))

    target = target_price(bought.fill_price, PCT)
    await orch.on_tick(_tick(target, at=AT + timedelta(seconds=5)))
    clock.advance(3.0)
    await orch.poll_pending()

    sold_partly = repo.load_stages(cid)[0]
    assert sold_partly.status is StageStatus.HOLDING       # 잔량이 보유로 복귀
    assert sold_partly.fill_qty < bought.fill_qty
    assert sold_partly.fill_price == bought.fill_price     # 취득원가 불변


# ══ 7. 응답 타임아웃 → 조회 확인 → 중복 발주 없음 ═══════════════════════
@pytest.mark.asyncio
async def test_g2_response_timeout_does_not_duplicate(tmp_path):
    """D12 — **이 시스템에서 가장 중요한 분기다.**

    모의투자로는 응답 타임아웃을 유발할 수 없다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker, clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)

    broker._fail_mode = FailMode.TIMEOUT   # 앵커 확정 뒤부터 타임아웃
    broker._calls = 0
    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))

    assert len(await broker.list_orders_today("005930")) == 1
    assert repo.load_stages(cid)[0].status is StageStatus.BUY_PENDING
    assert [r.status for r in repo.load_pending_orders()] == ["ACCEPTED"]


# ══ 8. 명시적 거부 → 상태 복구 ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_rejection_restores_state(tmp_path):
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker, clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)

    broker._fail_mode = FailMode.REJECT
    broker._calls = 0
    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))

    assert repo.load_stages(cid)[0].status is StageStatus.WAITING
    assert repo.load_pending_orders() == []


# ══ 9. WS 끊김 → REST 폴백 → 판정 계속 ══════════════════════════════════
@pytest.mark.asyncio
async def test_g2_websocket_drop_falls_back_and_keeps_deciding(tmp_path):
    """설계서 8.4절 — 끊겨도 트리거 판정은 계속 수행한다.

    모의투자로는 WebSocket 을 끊었다 붙일 수 없다.

    첫 WS 틱이 1단계를 사고(규칙 2) 곧 끊긴다. 폴백이 같은 가격을 폴링하면
    2단계가 발동가에 걸려 있으므로 REST 경로로 주문이 나가야 한다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    cyc = repo.create_cycle(1, AT)
    repo.save_cycle(cycle_mod.confirm_anchor(cyc, anchor_price=ANCHOR,
                                             ladder=ladder, at=AT))
    repo.set_config_status(1, "ACTIVE", at=AT)
    for n in range(1, 8):
        repo.save_stage(cyc.cycle_id, stage_mod.StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n),
            planned_qty=ladder.planned_qty(n)))

    broker = FakeBroker([ladder.trigger_price(2), ladder.trigger_price(3)],
                        validate_account=True, cash=100_000_000,
                        fail_mode=FailMode.DISCONNECT, fail_after=1)
    orch, clock, (_, _, event_q) = _engine(repo, broker)

    await orch.run()

    fallbacks = [e for e in _events(event_q) if isinstance(e, QuoteFallback)]
    assert fallbacks and fallbacks[0].active is True
    rows = [dict(r) for r in repo._conn.execute(
        "SELECT tick_source FROM order_log ORDER BY id").fetchall()]
    assert [r["tick_source"] for r in rows] == ["WS", "REST_POLL"]
    holding = [s.stage_no for s in repo.load_stages(cyc.cycle_id)
               if s.status is StageStatus.HOLDING]
    assert holding == [1, 2]


# ══ 10. 대사 불일치 → 자동 PAUSED ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_reconcile_mismatch_pauses(tmp_path):
    """D13 — 자동 보정하지 않고 멈춘다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker, clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)
    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))
    await orch.poll_pending()
    _events(event_q)

    # 사용자가 증권사 앱에서 직접 절반을 팔았다
    held, cost = broker._positions["005930"]
    broker._positions["005930"] = (held // 2, cost // 2)

    await orch.reconcile()

    mismatches = [e for e in _events(event_q)
                  if isinstance(e, ReconcileMismatch)]
    assert [e.verdict for e in mismatches] == ["INTERNAL_MORE"]
    assert mismatches[0].action_taken == "PAUSED"
    assert repo.load_cycle(cid).status is CycleStatus.PAUSED
    # 설정은 ACTIVE 로 남는다 — 일시정지는 사이클의 상태다
    assert repo.load_config(1).status == "ACTIVE"


# ══ 11. 프로세스 강제 종료 후 재시작 복구 ═══════════════════════════════
@pytest.mark.asyncio
async def test_g2_restart_recovery(tmp_path):
    """설계서 10.1절 — 발주 직후 죽었고 그 사이에 체결됐다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, _, _) = _engine(repo, broker, clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)
    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))
    order_id = repo.load_pending_orders()[0].broker_order_id
    broker._fill(broker._orders[order_id], ladder.planned_qty(1))

    # 재시작
    events: list[object] = []
    report = await Recovery(repo=repo, broker=broker,
                            clock=FakeClock(current=AT),
                            emit=events.append).run()

    assert report.resolved_orders == 1
    stage = repo.load_stages(cid)[0]
    assert stage.status is StageStatus.HOLDING
    assert stage.fill_qty == ladder.planned_qty(1)
    assert report.subscribe_codes == ("005930",)


# ══ 12. 긴급청산 ════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_emergency_liquidation(tmp_path):
    """미체결 취소·실계좌 수량 사용·장외 거부를 한 시나리오에서 확인한다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, priority_q, event_q) = _engine(repo, broker,
                                                            clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)
    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))
    await orch.poll_pending()
    # 하위 단계 매수 주문을 미체결로 남긴다
    broker._fill_mode = FillMode.NEVER
    await orch.on_tick(_tick(ladder.trigger_price(2),
                             at=AT + timedelta(seconds=2)))
    assert len(repo.load_pending_orders()) == 1

    # 장외 요청은 거부된다 (11.3절)
    clock.set_market_open(False)
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="오작동 의심",
                                      confirmed_text=None))
    await orch.drain_commands()
    last = dict(repo._conn.execute(
        "SELECT result FROM emergency_liquidation_log ORDER BY id"
    ).fetchall()[-1])
    assert last["result"] == "REJECTED_CLOSED_MARKET"

    # 장중 요청은 실행된다
    clock.set_market_open(True)
    broker._fill_mode = FillMode.INSTANT
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="오작동 의심",
                                      confirmed_text=None))
    await orch.drain_commands()

    log = dict(repo._conn.execute(
        "SELECT result, qty_before, qty_after, canceled_orders "
        "FROM emergency_liquidation_log ORDER BY id"
    ).fetchall()[-1])
    assert log["result"] == "SUCCESS"
    assert log["canceled_orders"] == 1
    assert log["qty_before"] == ladder.planned_qty(1)
    assert log["qty_after"] == 0
    assert repo.load_cycle(cid).close_reason is CloseReason.EMERGENCY
    assert repo.holdings() == []
    assert repo.load_config(1).status == "IDLE"


# ══ 13. 총한도 도달 시 매수 중단 ════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_total_limit_stops_buying(tmp_path):
    """설계서 6절 — **손절매가 없으므로 이것이 유일한 구조적 보호장치다.**

    브로커 검증을 켠 채로 돌린다. 끄면 한도를 넘긴 매수도 조용히 체결되어
    이 테스트가 아무것도 검증하지 않는다 (2A 핸드오버 4).
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    limit = (ladder.trigger_price(1) * ladder.planned_qty(1)
             + ladder.trigger_price(2) * ladder.planned_qty(2))
    script = [ANCHOR] + [ladder.trigger_price(n) for n in range(1, 8)]
    broker = FakeBroker(script, validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker,
                                                   total_limit=limit)
    _start(orch, command_q)

    await orch.run()

    cid = _cycle_id(repo)
    holding = [s.stage_no for s in repo.load_stages(cid)
               if s.status is StageStatus.HOLDING]
    assert holding == [1, 2]
    blocked = [e for e in _events(event_q) if isinstance(e, GuardBlocked)]
    assert blocked and all("총한도" in e.reason for e in blocked)


# ══ D20. 강제 종료 ══════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_forced_close_when_liquidation_cannot_finish(tmp_path):
    """설계서 11.4절 — 거래정지로 청산이 끝까지 가지 못하는 경우."""
    from autotrading7s.ports.broker import BrokerRejected

    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (command_q, priority_q, event_q) = _engine(repo, broker,
                                                            clock=clock)
    await _boot(orch, command_q)
    cid = _cycle_id(repo)
    await orch.on_tick(_tick(trigger, at=AT + timedelta(seconds=1)))
    await orch.poll_pending()
    qty = ladder.planned_qty(1)

    async def halted(req):
        raise BrokerRejected("40510", "거래정지")

    broker.place_market_sell = halted        # type: ignore[method-assign]
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="오작동 의심",
                                      confirmed_text=None))
    await orch.drain_commands()
    assert repo.load_cycle(cid).status is CycleStatus.LIQUIDATING

    # 사용자가 증언과 함께 강제 종료한다
    priority_q.put(ForceClose(
        config_id=1,
        reason=f"거래정지로 청산 불가, 잔량 {qty}주는 직접 처리 예정",
        confirmed_text="강제종료"))
    await orch.drain_commands()

    closed = repo.load_cycle(cid)
    assert closed.status is CycleStatus.CLOSED
    assert closed.close_reason is CloseReason.FORCED
    assert closed.forced_close_qty == qty
    assert "거래정지" in closed.forced_close_reason
    assert repo.holdings() == []
    assert repo.load_config(1).status == "IDLE"
    # 대사 기준선에 그 수량이 반영되어 이후 대사가 조용하다
    assert repo.forced_close_baseline("005930") == qty
    _events(event_q)
    await orch.reconcile()
    assert not [e for e in _events(event_q)
                if isinstance(e, ReconcileMismatch)]


# ══ 의존 방향과 게이트 자신의 전제 ══════════════════════════════════════
def test_engine_and_app_do_not_import_adapters():
    """설계서 7.2절 — 화살표는 항상 안쪽을 향한다.

    `cli.py` 는 예외다 — 조립 지점이므로 구체 어댑터를 알아야 한다.
    """
    import ast
    import pathlib

    root = pathlib.Path("src/autotrading7s")
    offenders: list[str] = []
    for path in (list((root / "engine").rglob("*.py"))
                 + list((root / "app").rglob("*.py"))):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue          # 상대 import 는 같은 패키지 안이다
                names = [node.module or ""]
            for name in names:
                if "autotrading7s.adapters" in name:
                    offenders.append(f"{path}: {name}")
    assert offenders == []


def test_gate_runs_with_broker_validation_enabled():
    """게이트 자신의 전제를 게이트가 단정한다 (2A 핸드오버 4).

    `validate_account` 를 끄면 한도를 넘긴 매수와 없는 포지션의 매도가 조용히
    통과하고, 시나리오 12·13 이 아무것도 검증하지 않게 된다. 그 사실이 이
    파일에서 조용히 사라지지 않도록 소스에서 직접 확인한다.

    이 테스트 **자신의 본문 앞까지만** 센다. 자기 리터럴을 포함시키면 두 이름의
    등장 횟수가 비대칭이라(이 함수에 `FakeBroker(` 는 두 번, `validate_account=
    True` 는 한 번 나온다) 검사가 구조적으로 1 만큼 어긋난다 — 통과시키려고
    상수를 더하는 순간 그 검사는 아무것도 지키지 않게 된다.
    """
    import pathlib

    source = pathlib.Path("tests/test_g2_gate.py").read_text(encoding="utf-8")
    body = source.split("def test_gate_runs_with_broker_validation_enabled")[0]
    assert body.count("FakeBroker(") > 10, "FakeBroker 생성이 거의 없다"
    assert body.count("FakeBroker(") == body.count("validate_account=True")
