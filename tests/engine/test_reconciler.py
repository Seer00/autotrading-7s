from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import Event, ReconcileMismatch
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import CycleStatus
from autotrading7s.engine.reconciler import Reconciler

AT = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)


def _rec(repo, broker):
    events: list[Event] = []
    return (Reconciler(repo=repo, broker=broker, clock=FakeClock(current=AT),
                       emit=events.append), events)


def _force_close_first_cycle(repo, *, qty=100):
    """005930 사이클을 강제 종료해 기준선을 만든다."""
    cyc = repo.load_active_cycles()[0]
    liq = cycle_mod.begin_liquidation(cyc)
    repo.save_cycle(liq)
    stages = [stage_mod.force_sold(s, at=AT)
              for s in repo.load_stages(cyc.cycle_id)]
    repo.emergency_close_cycle(
        cycle=cycle_mod.force_close(liq, reason="거래정지", qty=qty, at=AT),
        stages=stages,
    )
    return cyc


@pytest.mark.asyncio
async def test_match_writes_no_event(repo_two_stocks):
    """일치하면 로그도 이벤트도 없다 (설계서 10.2절 표)."""
    broker = FakeBroker([10_000], holdings={"005930": (100, 1_000_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()

    assert {r.verdict for r in reports} == {"MATCH"}
    assert events == []
    assert dict(repo_two_stocks._conn.execute(
        "SELECT count(*) AS c FROM reconcile_log").fetchone())["c"] == 0


@pytest.mark.asyncio
async def test_internal_less_warns_but_keeps_trading(repo_two_stocks):
    """내부 < 실계좌 — 외부에서 수동 매수한 듯. 경고만 하고 계속 돈다."""
    broker = FakeBroker([10_000], holdings={"005930": (150, 1_500_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()
    samsung = next(r for r in reports if r.stock_code == "005930")

    assert samsung.verdict == "INTERNAL_LESS"
    assert samsung.action_taken is None
    assert repo_two_stocks.load_active_cycles()[0].status is CycleStatus.RUNNING
    assert [type(e) for e in events] == [ReconcileMismatch]


@pytest.mark.asyncio
async def test_internal_more_pauses_that_cycle(repo_two_stocks):
    """내부 > 실계좌 — 해당 사이클 즉시 PAUSED.

    자동 보정하지 않는 이유(D13): 내부가 많으면 매도가 계속 거부되어
    SELL_PENDING 무한 재시도에 빠진다. 반대로 내부를 실계좌에 맞춰 고치면
    단계별 체결가가 조용히 조작되고 이후 모든 목표가 계산이 근거를 잃는다.
    """
    broker = FakeBroker([10_000], holdings={"005930": (40, 400_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()
    samsung = next(r for r in reports if r.stock_code == "005930")

    assert samsung.verdict == "INTERNAL_MORE"
    assert samsung.action_taken == "PAUSED"
    cyc = next(c for c in repo_two_stocks.load_active_cycles()
               if c.config_id == 1)
    assert cyc.status is CycleStatus.PAUSED
    # 설정은 ACTIVE 로 남는다 — 일시정지는 사이클의 상태다 (원장 Ruling 1)
    assert repo_two_stocks.load_config(1).status == "ACTIVE"
    # 다른 종목은 영향받지 않는다 — 종목별 대응이다
    other = next(c for c in repo_two_stocks.load_active_cycles()
                 if c.config_id == 2)
    assert other.status is CycleStatus.RUNNING


@pytest.mark.asyncio
async def test_a_paused_cycle_is_not_transitioned_again(repo_two_stocks):
    """이미 PAUSED 면 상태를 바꾸지 않고 이벤트만 낸다 (원장 Ruling 5).

    도메인 전이표가 PAUSED → PAUSED 를 금지하므로, 조건 없이 pause() 를
    부르면 두 번째 대사에서 IllegalCycleTransition 이 나고 대사 태스크가
    죽는다 — 그러면 다른 종목의 불일치도 함께 놓친다.
    """
    broker = FakeBroker([10_000], holdings={"005930": (40, 400_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)
    await rec.run_once()

    reports = await rec.run_once()
    samsung = next(r for r in reports if r.stock_code == "005930")

    assert samsung.verdict == "INTERNAL_MORE"
    assert samsung.action_taken is None
    assert (next(c for c in repo_two_stocks.load_active_cycles()
                 if c.config_id == 1).status is CycleStatus.PAUSED)


def test_reconciler_never_writes_stage_state():
    """자동 보정 금지를 코드에서 확인한다 (D13).

    호출 부재가 아니라 참조 부재로 고정한다 — 대사가 단계를 쓸 수 있게 되면
    그것이 D13 이 금지한 바로 그 조작이다.
    """
    import ast

    from autotrading7s.engine import reconciler as mod

    # 문자열 검색이 아니라 호출 그래프를 본다 — 독스트링이 "save_stage 를
    # 부르지 않는다" 고 적으면 문자열 검색은 그것을 위반으로 오판한다.
    tree = ast.parse(inspect.getsource(mod))
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "save_stage" not in called
    assert "emergency_close_cycle" not in called
    # 단계를 쓰는 다른 경로도 없어야 한다
    assert "force_sold" not in called


# ── 강제 종료 기준선 (설계서 11.4절) ────────────────────────────────────
@pytest.mark.asyncio
async def test_forced_quantity_is_excluded_from_reconciliation(repo_two_stocks):
    """강제 종료된 수량은 대사 기준에서 빠진다.

    빼지 않으면 강제 종료 직후 매 5분마다 영구적으로 INTERNAL_LESS 경고가
    나고, 사용자는 그 경고를 무시하는 습관을 들인다 — 그러면 진짜 불일치도
    무시된다.
    """
    _force_close_first_cycle(repo_two_stocks, qty=100)
    # 실계좌에는 강제 종료된 100주가 그대로 남아 있다
    broker = FakeBroker([10_000], holdings={"005930": (100, 1_000_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()

    assert {r.verdict for r in reports} == {"MATCH"}
    assert events == []


@pytest.mark.asyncio
async def test_baseline_reset_makes_the_difference_visible_again(repo_two_stocks):
    """설계서 11.4절 — 대사 제외는 영구적이지 않다.

    사용자가 그 주식을 처리한 뒤 기준선을 초기화하면 이후의 차이는 다시
    불일치로 보고돼야 한다.
    """
    _force_close_first_cycle(repo_two_stocks, qty=100)
    assert repo_two_stocks.forced_close_baseline("005930") == 100

    repo_two_stocks.reset_forced_close_baseline("005930", at=AT)

    assert repo_two_stocks.forced_close_baseline("005930") == 0
    row = repo_two_stocks._conn.execute(
        "SELECT action_taken, verdict FROM reconcile_log"
    ).fetchone()
    assert dict(row)["action_taken"] == "BASELINE_RESET"
    assert dict(row)["verdict"] == "MATCH"


@pytest.mark.asyncio
async def test_reconciler_reset_baseline_uses_the_clock(repo_two_stocks):
    broker = FakeBroker([10_000])
    rec, _ = _rec(repo_two_stocks, broker)
    rec.reset_baseline("005930")
    row = repo_two_stocks._conn.execute(
        "SELECT checked_at FROM reconcile_log"
    ).fetchone()
    assert dict(row)["checked_at"].startswith("2026-09-02T11:00")


def test_baseline_is_zero_for_a_stock_never_force_closed(repo_two_stocks):
    assert repo_two_stocks.forced_close_baseline("000660") == 0
    assert repo_two_stocks.forced_close_baseline("035720") == 0
