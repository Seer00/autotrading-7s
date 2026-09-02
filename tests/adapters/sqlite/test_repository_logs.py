from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.ports.repository import SplitConfig
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import confirm_anchor
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus
from autotrading7s.ports.repository import RepositoryPort

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


@pytest.fixture()
def repo():
    conn = connect(":memory:")
    apply_schema(conn)
    yield SqliteRepository(conn)
    conn.close()


def seed(repo, *, stock_code="005930", label="기본", holdings=()) -> int:
    """설정과 RUNNING 사이클을 만들고, holdings 에 (stage_no, fill, qty) 를 채운다."""
    config_id = repo.save_config(SplitConfig(
        config_id=None, stock_code=stock_code, stock_name="삼성전자", label=label,
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=True, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="ACTIVE", created_at=T0, updated_at=T0))
    lad = a_ladder()
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=lad, at=T0)
    repo.save_cycle(cycle)
    held = {n: (fill, qty) for n, fill, qty in holdings}
    for n in range(1, 8):
        if n in held:
            fill, qty = held[n]
            stage = StageState(stage_no=n, status=StageStatus.HOLDING,
                               trigger_price=lad.trigger_price(n),
                               planned_qty=lad.planned_qty(n),
                               fill_price=fill, fill_qty=qty, bought_at=T0)
        else:
            stage = StageState(stage_no=n, status=StageStatus.WAITING,
                               trigger_price=lad.trigger_price(n),
                               planned_qty=lad.planned_qty(n))
        repo.save_stage(cycle.cycle_id, stage)
    return cycle.cycle_id


def test_repository_satisfies_the_port(repo):
    """Task 3 이 고정한 목록을 이제 전부 채웠다."""
    assert isinstance(repo, RepositoryPort)


def test_emergency_log_round_trip(repo):
    cycle_id = seed(repo, holdings=[(1, 10_000, 100)])
    log_id = repo.append_emergency_log(
        scope="SINGLE", stock_code="005930", cycle_id=cycle_id, requested_at=T0,
        reason="실적 쇼크", qty_before=100, qty_after=0, canceled_orders=2,
        result="SUCCESS", detail_json=None, completed_at=T0)
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT * FROM emergency_liquidation_log WHERE id = ?", (log_id,)
    ).fetchone()
    assert row["reason"] == "실적 쇼크"
    assert row["result"] == "SUCCESS"
    assert row["qty_before"] == 100


def test_emergency_log_accepts_forced_close_result(repo):
    """D20 — 강제 종료가 이 이력에 result=FORCED_CLOSE 로 기록된다."""
    cycle_id = seed(repo, holdings=[(1, 10_000, 100)])
    repo.append_emergency_log(
        scope="SINGLE", stock_code="005930", cycle_id=cycle_id, requested_at=T0,
        reason="거래정지로 청산 불가, 잔량 40주 직접 처리 예정", qty_before=100,
        qty_after=40, canceled_orders=1, result="FORCED_CLOSE",
        detail_json='{"attempts": 3}', completed_at=T0)
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT result, qty_after FROM emergency_liquidation_log"
    ).fetchone()
    assert (row["result"], row["qty_after"]) == ("FORCED_CLOSE", 40)


def test_emergency_log_refuses_an_unknown_result(repo):
    import sqlite3

    cycle_id = seed(repo)
    with pytest.raises(sqlite3.IntegrityError):
        repo.append_emergency_log(
            scope="SINGLE", stock_code="005930", cycle_id=cycle_id,
            requested_at=T0, reason=None, qty_before=None, qty_after=None,
            canceled_orders=None, result="BOGUS", detail_json=None,
            completed_at=None)


def test_reconcile_log_round_trip(repo):
    seed(repo)
    repo.append_reconcile_log(
        checked_at=T0, stock_code="005930", internal_qty=316, broker_qty=316,
        verdict="MATCH", action_taken=None)
    repo.append_reconcile_log(
        checked_at=T0, stock_code="005930", internal_qty=316, broker_qty=200,
        verdict="INTERNAL_MORE", action_taken="PAUSED")
    rows = repo._conn.execute(  # noqa: SLF001
        "SELECT verdict, action_taken FROM reconcile_log ORDER BY id").fetchall()
    assert [(r["verdict"], r["action_taken"]) for r in rows] == [
        ("MATCH", None), ("INTERNAL_MORE", "PAUSED")]


def test_holdings_is_empty_when_nothing_is_held(repo):
    seed(repo)
    assert repo.holdings() == []


def test_holdings_aggregates_one_stock():
    """설계서 14.1절 목업의 삼성전자: 3단계 보유, 316주, 평단 9,458원."""
    conn = connect(":memory:")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    seed(repo, holdings=[(1, 10_000, 100), (2, 9_480, 105), (3, 8_950, 111)])
    rows = repo.holdings()
    assert len(rows) == 1
    row = rows[0]
    assert row.stock_code == "005930"
    assert row.total_qty == 316
    # 9,458 — 이 목업은 소수부가 0.386 이라 절사와 반올림이 같다.
    # 절사를 실제로 가르는 것은 아래 test_holdings_avg_price_truncates 다.
    assert row.avg_price == 2_988_850 // 316
    assert row.holding_stages == 3
    assert row.max_stages == 7
    assert row.cycle_status is CycleStatus.RUNNING
    conn.close()


def test_holdings_avg_price_truncates(repo):
    """뷰의 평단은 SQL 정수 나눗셈이라 절사다 — 도메인의 half-up 반올림과 다르다."""
    seed(repo, holdings=[(1, 10_000, 100), (2, 9_400, 103)])
    invested, qty = 10_000 * 100 + 9_400 * 103, 203
    # 이 조합은 소수부가 0.5 를 넘으므로 절사와 반올림이 1원 갈린다.
    assert invested / qty > invested // qty + 0.5
    assert repo.holdings()[0].avg_price == 9_695   # 반올림이면 9,696 이다


def test_holdings_counts_sell_pending_as_held():
    """매도 주문이 나갔어도 체결 전까지는 보유다."""
    from autotrading7s.domain.stage import to_sell_pending

    conn = connect(":memory:")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    cycle_id = seed(repo, holdings=[(1, 10_000, 100)])
    lad = a_ladder()
    held = StageState(stage_no=1, status=StageStatus.HOLDING,
                      trigger_price=lad.trigger_price(1),
                      planned_qty=lad.planned_qty(1),
                      fill_price=10_000, fill_qty=100, bought_at=T0)
    repo.save_stage(cycle_id, to_sell_pending(held))
    rows = repo.holdings()
    assert len(rows) == 1 and rows[0].total_qty == 100
    conn.close()


def test_holdings_lists_multiple_stocks():
    conn = connect(":memory:")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    seed(repo, stock_code="005930", label="기본", holdings=[(1, 10_000, 100)])
    seed(repo, stock_code="035720", label="공격형", holdings=[(1, 10_000, 100)])
    assert {r.stock_code for r in repo.holdings()} == {"005930", "035720"}
    conn.close()
