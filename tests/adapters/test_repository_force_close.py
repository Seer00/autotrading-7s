from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus
from autotrading7s.ports.repository import (
    RepositoryPort,
    RowNotFound,
    StageInvariantError,
)

AT = datetime(2026, 9, 2, 15, 28, tzinfo=UTC)


def _liquidating(repo):
    """첫 사이클(005930)을 LIQUIDATING 으로 만들어 반환한다.

    `load_active_cycles` 는 `ORDER BY id` 이므로 [0] 이 005930 이다.
    """
    cyc = repo.load_active_cycles()[0]
    liquidating = cycle_mod.begin_liquidation(cyc)
    repo.save_cycle(liquidating)
    return liquidating


def _all_forced(repo, cycle_id):
    return [stage_mod.force_sold(s, at=AT) for s in repo.load_stages(cycle_id)]


def test_emergency_close_writes_cycle_and_stages_together(repo_two_stocks):
    """설계서 11.4절 ⑤⑥ — 절반만 강제 종료된 상태가 남지 않아야 한다."""
    cyc = _liquidating(repo_two_stocks)
    stages = _all_forced(repo_two_stocks, cyc.cycle_id)
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)

    repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)

    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.status is CycleStatus.CLOSED
    assert reloaded.close_reason is CloseReason.FORCED
    assert reloaded.forced_close_qty == 100
    assert reloaded.forced_close_reason == "거래정지"
    assert all(s.status is StageStatus.SOLD
               for s in repo_two_stocks.load_stages(cyc.cycle_id))


def test_emergency_close_accepts_an_emergency_cycle(repo_two_stocks):
    """긴급청산(11.1절 ⑤⑦)도 같은 문을 쓴다 — 같은 문제를 갖기 때문이다.

    force_sold 로 전 단계를 일괄 갱신해야 하는데 save_stage 가 그것을 거부한다.
    """
    cyc = _liquidating(repo_two_stocks)
    stages = _all_forced(repo_two_stocks, cyc.cycle_id)
    closed = cycle_mod.close(cyc, reason=CloseReason.EMERGENCY, at=AT,
                             states=stages)

    repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)

    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.close_reason is CloseReason.EMERGENCY
    assert reloaded.forced_close_qty is None


def test_forced_close_removes_the_stock_from_holdings(repo_two_stocks):
    """설계서 11.4절 — 강제 종료 후 그 종목은 프로그램의 관리 밖이다.

    남은 주식이 holdings 뷰에서 사라지는 것이 의도다. 그 수량은
    forced_close_qty 에 남아 대사 기준선이 된다.
    """
    cyc = _liquidating(repo_two_stocks)
    stages = _all_forced(repo_two_stocks, cyc.cycle_id)
    repo_two_stocks.emergency_close_cycle(
        cycle=cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT),
        stages=stages,
    )
    codes = {h.stock_code for h in repo_two_stocks.holdings()}
    assert "005930" not in codes
    assert "000660" in codes          # 다른 종목은 영향받지 않는다


def test_rejects_a_cycle_that_is_not_emergency_or_forced(repo_two_stocks):
    """이 메서드는 전이표를 우회하므로 입력을 엄격히 검사해야 한다.

    검사가 없으면 이것이 save_stage 가드의 우회 수단이 되고, 그러면 가드가
    막고 있는 모든 것(체결값 덮어쓰기, 상태 역행)이 이 문으로 들어온다.
    정상 종료(NORMAL)도 거부한다 — 정상 경로는 close() 의 보유 0 검사와
    save_stage 의 가드를 통과해야 한다.
    """
    cyc = _liquidating(repo_two_stocks)
    stages = _all_forced(repo_two_stocks, cyc.cycle_id)
    with pytest.raises(ValueError, match="EMERGENCY"):
        repo_two_stocks.emergency_close_cycle(cycle=cyc, stages=stages)
    normal = cycle_mod.close(cyc, reason=CloseReason.NORMAL, at=AT,
                             states=stages)
    with pytest.raises(ValueError, match="EMERGENCY"):
        repo_two_stocks.emergency_close_cycle(cycle=normal, stages=stages)


def test_rejects_stages_that_are_not_all_sold(repo_two_stocks):
    cyc = _liquidating(repo_two_stocks)
    stages = repo_two_stocks.load_stages(cyc.cycle_id)      # force_sold 안 함
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)
    with pytest.raises(StageInvariantError, match="SOLD"):
        repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)


def test_rejects_an_incomplete_stage_set(repo_two_stocks):
    """단계 일부만 쓰면 load_stages 가 이후 그 사이클을 로드할 수 없다 (H3)."""
    cyc = _liquidating(repo_two_stocks)
    stages = _all_forced(repo_two_stocks, cyc.cycle_id)[:3]
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)
    with pytest.raises(StageInvariantError):
        repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)


def test_a_rejected_emergency_close_writes_nothing(repo_two_stocks):
    """원자성 — 거부된 강제 종료가 사이클만 CLOSED 로 남기면 안 된다."""
    cyc = _liquidating(repo_two_stocks)
    stages = repo_two_stocks.load_stages(cyc.cycle_id)
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)
    with pytest.raises(StageInvariantError):
        repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)
    assert any(s.status is StageStatus.HOLDING
               for s in repo_two_stocks.load_stages(cyc.cycle_id))


def test_set_realized_pnl_round_trips(repo_two_stocks):
    """2A 핸드오버 2 — cycle_to_row 가 이 컬럼을 제외하므로 전용 메서드가 필요하다.

    사이클 종료 시 realized_pnl_for_cycle 의 값을 여기 기록하는 것이 엔진의
    몫이다. 리포지토리는 집계만 한다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    repo_two_stocks.set_realized_pnl(cyc.cycle_id, -580_000)
    row = repo_two_stocks._conn.execute(
        "SELECT realized_pnl FROM cycle WHERE id = ?", (cyc.cycle_id,)
    ).fetchone()
    assert dict(row)["realized_pnl"] == -580_000


def test_set_realized_pnl_rejects_a_missing_cycle(repo_two_stocks):
    with pytest.raises(RowNotFound):
        repo_two_stocks.set_realized_pnl(9999, 0)


def test_save_cycle_no_longer_needs_a_separate_forced_path(repo_two_stocks):
    """Cycle 에 필드가 생겼으므로 save_cycle 로도 FORCED 를 쓸 수 있다.

    2A 는 이것을 IntegrityError 로 막고 있었다(핸드오버 1). 그래도 단계 쓰기가
    막혀 있으므로 **절반만 강제 종료된 상태**를 만들 수 있다는 것이 요점이며,
    그래서 엔진은 언제나 emergency_close_cycle 을 써야 한다.
    """
    cyc = _liquidating(repo_two_stocks)
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)
    repo_two_stocks.save_cycle(closed)          # 더 이상 IntegrityError 가 아니다
    assert repo_two_stocks.load_cycle(cyc.cycle_id).forced_close_qty == 100
    # 단계 쓰기는 여전히 막혀 있다 — 그 비대칭이 전용 메서드의 존재 이유다
    holding = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.status is StageStatus.HOLDING)
    with pytest.raises(StageInvariantError):
        repo_two_stocks.save_stage(cyc.cycle_id,
                                   stage_mod.force_sold(holding, at=AT))


def test_port_declares_both_new_methods():
    for name in ("emergency_close_cycle", "set_realized_pnl"):
        assert name in RepositoryPort.__protocol_attrs__
