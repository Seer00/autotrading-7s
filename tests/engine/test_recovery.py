from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import CycleLoadFailed, Event, ReconcileMismatch
from autotrading7s.domain.rules import BuyStage
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource
from autotrading7s.engine.executor import Executor
from autotrading7s.engine.recovery import Recovery

AT = datetime(2026, 9, 2, 9, 5, tzinfo=UTC)


def _recovery(repo, broker):
    events: list[Event] = []
    return (Recovery(repo=repo, broker=broker, clock=FakeClock(current=AT),
                     emit=events.append), events)


async def _leave_a_pending_buy(repo, broker, *, stage_no=1, qty=100,
                               price=10_000):
    """엔진이 발주 직후 죽은 상태를 만든다 — BUY_PENDING + ACCEPTED 주문."""
    cyc = repo.load_active_cycles()[0]
    config = repo.load_config(cyc.config_id)
    ex = Executor(repo=repo, broker=broker, clock=FakeClock(current=AT),
                  emit=lambda e: None)
    waiting = next(s for s in repo.load_stages(cyc.cycle_id)
                   if s.stage_no == stage_no)
    sent = await ex.send(
        cycle=cyc, config=config, stage=waiting,
        decision=BuyStage(stage_no=stage_no, limit_price=price, qty=qty,
                          reason="r"),
        tick=Tick(code="005930", price=price, at=AT, source=TickSource.WS),
    )
    return cyc, sent


@pytest.mark.asyncio
async def test_a_filled_order_is_reconciled_into_holding(repo_fresh):
    """2단계 '체결됨 → HOLDING 으로 정정'.

    죽어 있는 동안 체결된 주문을 놓치면 그 단계는 영원히 BUY_PENDING 이고,
    규칙 5 가 판정에서 제외하므로 그 자본이 조용히 잠긴다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    cyc, sent = await _leave_a_pending_buy(repo_fresh, broker)
    broker._fill(broker._orders[sent.broker_order_id], 100)   # 죽은 동안 체결

    rec, events = _recovery(repo_fresh, broker)
    report = await rec.run()

    assert report.resolved_orders == 1
    stage = repo_fresh.load_stages(cyc.cycle_id)[0]
    assert stage.status is StageStatus.HOLDING
    assert (stage.fill_price, stage.fill_qty) == (10_000, 100)


@pytest.mark.asyncio
async def test_an_order_with_no_trace_restores_the_stage(repo_fresh):
    """'기록 없음 → 원래 상태 복구'.

    전일 미체결은 장 마감에 자동 소멸한다 — 한국 주식 주문은 당일에만
    유효하다. 그 단계를 WAITING 으로 돌려야 오늘 다시 시도된다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    cyc, sent = await _leave_a_pending_buy(repo_fresh, broker)

    async def empty(code):
        return []

    broker.list_orders_today = empty        # type: ignore[method-assign]
    rec, events = _recovery(repo_fresh, broker)
    report = await rec.run()

    assert report.restored_stages == 1
    assert repo_fresh.load_stages(cyc.cycle_id)[0].status is StageStatus.WAITING
    assert repo_fresh.load_pending_orders() == []


@pytest.mark.asyncio
async def test_a_partially_filled_order_confirms_the_filled_portion(repo_fresh):
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    cyc, sent = await _leave_a_pending_buy(repo_fresh, broker)
    broker._fill(broker._orders[sent.broker_order_id], 40)

    rec, _ = _recovery(repo_fresh, broker)
    await rec.run()

    stage = repo_fresh.load_stages(cyc.cycle_id)[0]
    assert stage.status is StageStatus.HOLDING
    assert stage.fill_qty == 40


@pytest.mark.asyncio
async def test_startup_reconcile_warns_but_never_pauses(repo_two_stocks):
    """3단계 — '불일치 시 경고, 정지하지는 않음'.

    재시작 직후의 불일치는 아직 정정되지 않은 주문 때문일 수 있다. 정지는
    첫 정기 대사(10.2절)가 한다.
    """
    broker = FakeBroker([10_000], holdings={"005930": (40, 400_000),
                                            "000660": (100, 600_000)})
    rec, events = _recovery(repo_two_stocks, broker)

    await rec.run()

    mismatches = [e for e in events if isinstance(e, ReconcileMismatch)]
    assert [e.verdict for e in mismatches] == ["INTERNAL_MORE"]
    assert all(e.action_taken is None for e in mismatches)
    cyc = next(c for c in repo_two_stocks.load_active_cycles()
               if c.config_id == 1)
    assert cyc.status is CycleStatus.RUNNING
    assert repo_two_stocks.load_config(1).status == "ACTIVE"


@pytest.mark.asyncio
async def test_subscription_is_restored_for_active_cycles(repo_two_stocks):
    """4단계 — RUNNING 사이클의 구독 복원."""
    broker = FakeBroker([10_000], holdings={"005930": (100, 1_000_000),
                                            "000660": (100, 600_000)})
    rec, _ = _recovery(repo_two_stocks, broker)

    report = await rec.run()

    assert set(report.subscribe_codes) == {"005930", "000660"}


@pytest.mark.asyncio
async def test_a_corrupt_cycle_is_isolated_not_fatal(repo_two_stocks):
    """2A 핸드오버 7 — 손상된 행 하나가 기동을 막으면 사용자에게 나갈 길이 없다.

    자동 손절매가 없는 프로그램에서 크래시 루프는 포지션을 방치하는 것과
    같다. 그 사이클만 격리하고 나머지는 계속 복구한다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    repo_two_stocks._conn.execute(
        "UPDATE stage_state SET trigger_price = trigger_price + 7 "
        "WHERE cycle_id = ? AND stage_no = 1", (cyc.cycle_id,)
    )
    repo_two_stocks._conn.commit()
    broker = FakeBroker([10_000], holdings={"000660": (100, 600_000)})
    rec, events = _recovery(repo_two_stocks, broker)

    report = await rec.run()

    assert report.failed_cycles == (cyc.cycle_id,)
    failures = [e for e in events if isinstance(e, CycleLoadFailed)]
    assert len(failures) == 1
    assert "stage_state" in failures[0].detail
    assert failures[0].action_taken == "PAUSED"
    # 멈추는 것은 사이클이다 — 설정은 ACTIVE 로 남는다 (원장 Ruling 1)
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.PAUSED)
    assert repo_two_stocks.load_config(cyc.config_id).status == "ACTIVE"
    # 손상되지 않은 종목은 계속 복구된다
    assert report.subscribe_codes == ("000660",)


@pytest.mark.asyncio
async def test_an_emergency_order_without_a_stage_is_skipped(repo_two_stocks):
    """긴급청산 주문은 `stage_state_id` 가 없다 — LEFT JOIN 이어야 보인다.

    내부 조인이면 그 행이 목록에서 사라져 복구가 미체결 시장가 주문의 존재를
    아예 모르게 된다. 여기서는 그 행이 **보이되** 단계 정정 대상이 아니라는
    것을 확인한다.
    """
    from autotrading7s.domain.types import OrderPath, Side

    cyc = repo_two_stocks.load_active_cycles()[0]
    repo_two_stocks.append_order_log(
        client_ref="emergency-1", cycle_id=cyc.cycle_id, stage_state_id=None,
        side=Side.SELL, order_type="MARKET", path=OrderPath.EMERGENCY,
        req_price=None, req_qty=100, trigger_reason="긴급청산",
        tick_price=None, tick_source=None, sent_at=AT,
    )
    rows = repo_two_stocks.load_pending_orders()
    assert [r.client_ref for r in rows] == ["emergency-1"]
    assert rows[0].stage_no is None

    broker = FakeBroker([10_000], holdings={"005930": (100, 1_000_000),
                                            "000660": (100, 600_000)})
    rec, _ = _recovery(repo_two_stocks, broker)
    report = await rec.run()

    assert (report.resolved_orders, report.restored_stages) == (0, 0)
    # 그 행은 그대로 남는다 — 결말은 사용자가 긴급청산을 다시 시도할 때 정해진다
    assert [r.client_ref for r in repo_two_stocks.load_pending_orders()] == [
        "emergency-1"]


def test_recovery_does_not_swallow_corruption_with_a_broad_except():
    """`CorruptRowError` 는 `ValueError` 의 하위다.

    엔진에 넓은 `except ValueError` 를 두면 DB 손상을 삼킨다 — 잘못된 가격이
    올라와도 조용히 넘어가고, 그 가격으로 주문이 나간다.
    """
    from autotrading7s.engine import recovery as mod

    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            names = [n.id for n in ast.walk(node.type)
                     if isinstance(n, ast.Name)]
            assert "ValueError" not in names, "넓은 except ValueError 금지"
            assert "Exception" not in names, "넓은 except Exception 금지"
