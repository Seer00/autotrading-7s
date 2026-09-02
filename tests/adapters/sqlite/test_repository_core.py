from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import CorruptRowError
from autotrading7s.ports.repository import RowNotFound, SplitConfig
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import Cycle, confirm_anchor
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState, to_buy_pending, to_holding
from autotrading7s.domain.types import CycleStatus, StageStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


@pytest.fixture()
def repo():
    conn = connect(":memory:")
    apply_schema(conn)
    yield SqliteRepository(conn)
    conn.close()


def a_config(**over) -> SplitConfig:
    kwargs = dict(
        config_id=None, stock_code="005930", stock_name="삼성전자", label="기본",
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=True, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="IDLE", created_at=T0, updated_at=T0,
    )
    kwargs.update(over)
    return SplitConfig(**kwargs)  # type: ignore[arg-type]


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def a_running_cycle(repo, config_id: int) -> Cycle:
    lad = a_ladder()
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=lad, at=T0)
    repo.save_cycle(cycle)
    for n in range(1, 8):
        repo.save_stage(cycle.cycle_id, StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=lad.trigger_price(n), planned_qty=lad.planned_qty(n)))
    return cycle


def test_config_save_and_load(repo):
    config_id = repo.save_config(a_config())
    loaded = repo.load_config(config_id)
    assert loaded.config_id == config_id
    assert loaded.stock_code == "005930"
    assert loaded.drop_pct == FIVE


def test_duplicate_stock_code_and_label_is_refused(repo):
    """설계서 1.1절이 종목별 복수 설정을 허용하지만 label 로 구분한다."""
    import sqlite3

    repo.save_config(a_config())
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_config(a_config())


def test_same_stock_with_a_different_label_is_allowed(repo):
    repo.save_config(a_config(label="기본"))
    repo.save_config(a_config(label="공격형"))
    assert len(repo.list_configs()) == 2


def test_set_config_status(repo):
    config_id = repo.save_config(a_config())
    repo.set_config_status(config_id, "ACTIVE", at=T0)
    assert repo.load_config(config_id).status == "ACTIVE"


def test_set_config_status_raises_for_an_unknown_config_id(repo):
    """Fix Round 3 — 없는 행을 조용히 갱신하지 않는다(update_order_log 와
    같은 이유: 조용한 무동작은 호출자가 영속화됐다고 믿게 만든다)."""
    with pytest.raises(RowNotFound):
        repo.set_config_status(99_999, "ACTIVE", at=T0)


def test_set_config_status_does_not_take_the_wall_clock(repo):
    """`at` 을 명시적으로 넘겨야 한다 — datetime.now() 를 읽지 않는다."""
    config_id = repo.save_config(a_config())
    later = T0 + timedelta(days=1)
    repo.set_config_status(config_id, "ACTIVE", at=later)
    assert repo.load_config(config_id).updated_at == later


def test_create_cycle_starts_at_seq_one_and_status_starting(repo):
    config_id = repo.save_config(a_config())
    cycle = repo.create_cycle(config_id, started_at=T0)
    assert cycle.seq == 1
    assert cycle.status is CycleStatus.STARTING
    assert cycle.anchor_price is None and cycle.ladder is None


def test_create_cycle_increments_seq(repo):
    """사이클 이력이 보존되어야 종목별 누적 성과를 조회할 수 있다(설계서 D14)."""
    config_id = repo.save_config(a_config())
    first = repo.create_cycle(config_id, started_at=T0)
    second = repo.create_cycle(config_id, started_at=T0 + timedelta(days=1))
    assert (first.seq, second.seq) == (1, 2)
    assert first.cycle_id != second.cycle_id


def test_cycle_round_trip_through_the_database(repo):
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    loaded = repo.load_cycle(cycle.cycle_id)
    assert loaded.status is CycleStatus.RUNNING
    assert loaded.anchor_price == 10_000
    assert loaded.ladder is not None
    assert loaded.ladder.trigger_price(7) == a_ladder().trigger_price(7)


def test_save_cycle_raises_for_an_unknown_cycle_id(repo):
    """Fix Round 3 — save_cycle 도 update_order_log 와 같은 사전조건을 진다."""
    config_id = repo.save_config(a_config())
    ghost = Cycle(cycle_id=99_999, config_id=config_id, seq=1,
                 status=CycleStatus.IDLE, started_at=T0)
    with pytest.raises(RowNotFound):
        repo.save_cycle(ghost)


def test_save_cycle_still_succeeds_for_a_real_id(repo):
    config_id = repo.save_config(a_config())
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=a_ladder(), at=T0)
    repo.save_cycle(cycle)  # 예외 없음
    assert repo.load_cycle(cycle.cycle_id).status is CycleStatus.RUNNING


def test_load_active_cycles_excludes_closed(repo):
    from autotrading7s.domain.cycle import close
    from autotrading7s.domain.types import CloseReason

    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    assert [c.cycle_id for c in repo.load_active_cycles()] == [cycle.cycle_id]

    sold = [StageState(stage_no=n, status=StageStatus.SOLD,
                       trigger_price=a_ladder().trigger_price(n), planned_qty=1)
            for n in range(1, 8)]
    repo.save_cycle(close(cycle, reason=CloseReason.NORMAL, at=T0, states=sold))
    assert repo.load_active_cycles() == []


def test_load_stages_returns_the_complete_set_in_order(repo):
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    stages = repo.load_stages(cycle.cycle_id)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]


def test_load_stages_refuses_an_incomplete_set(repo):
    """H3. 행을 직접 지워 리포지토리 밖의 손상을 시뮬레이션한다."""
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    repo._conn.execute(  # noqa: SLF001 — 손상 시뮬레이션이므로 의도적
        "DELETE FROM stage_state WHERE cycle_id = ? AND stage_no = 4",
        (cycle.cycle_id,))
    repo._conn.commit()
    with pytest.raises(CorruptRowError, match="incomplete"):
        repo.load_stages(cycle.cycle_id)


def test_load_stages_refuses_a_trigger_price_mismatch(repo):
    """H4. 같은 방식으로 컬럼을 직접 바꿔 손상을 시뮬레이션한다."""
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    repo._conn.execute(  # noqa: SLF001
        "UPDATE stage_state SET trigger_price = 999999 "
        "WHERE cycle_id = ? AND stage_no = 2", (cycle.cycle_id,))
    repo._conn.commit()
    with pytest.raises(CorruptRowError, match="trigger_price"):
        repo.load_stages(cycle.cycle_id)


def test_save_stage_upserts(repo):
    """같은 (cycle_id, stage_no) 를 두 번 저장하면 갱신이어야 한다 —
    UNIQUE 제약이 있으므로 INSERT 만 하면 두 번째가 실패한다."""
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    lad = a_ladder()
    filled = to_holding(
        to_buy_pending(StageState(stage_no=2, status=StageStatus.WAITING,
                                  trigger_price=lad.trigger_price(2),
                                  planned_qty=lad.planned_qty(2))),
        fill_price=9_480, fill_qty=105, at=T0)
    repo.save_stage(cycle.cycle_id, filled)
    stages = repo.load_stages(cycle.cycle_id)
    assert stages[1].status is StageStatus.HOLDING
    assert stages[1].fill_price == 9_480
    assert len(stages) == 7


def test_load_stages_of_a_starting_cycle_skips_h4(repo):
    """STARTING 사이클은 사다리가 없으므로 대조 기준이 없다."""
    config_id = repo.save_config(a_config())
    cycle = repo.create_cycle(config_id, started_at=T0)
    lad = a_ladder()
    for n in range(1, 8):
        repo.save_stage(cycle.cycle_id, StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=lad.trigger_price(n), planned_qty=lad.planned_qty(n)))
    stages = repo.load_stages(cycle.cycle_id)
    assert len(stages) == 7
