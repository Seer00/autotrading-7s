from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from autotrading7s.ports.repository import RepositoryPort, RowNotFound

AT = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)


def test_update_config_changes_an_idle_config(repo_two_stocks):
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    changed = dataclasses.replace(repo_two_stocks.load_config(1),
                                  label="공격형", amount_per_stage=2_000_000)

    repo_two_stocks.update_config(changed, at=AT)

    reloaded = repo_two_stocks.load_config(1)
    assert reloaded.label == "공격형"
    assert reloaded.amount_per_stage == 2_000_000
    assert reloaded.status == "IDLE"


def test_update_config_refuses_an_active_config(repo_two_stocks):
    """ACTIVE 설정의 값을 바꾸면 진행 중인 사이클의 사다리와 어긋난다.

    `cycle.ladder_json` 은 고정되어 있고 `load_stages` 의 H4 가 `trigger_price`
    를 그 사다리와 대조하므로, 설정을 바꾸는 것만으로 **그 사이클이 로드 불가**
    가 된다 — 2A 가 만든 안전장치가 정확히 그 상황을 잡는다. 저장 한 번으로
    복구 불가 상태를 만들 수 있으면 안 된다.
    """
    assert repo_two_stocks.load_config(1).status == "ACTIVE"
    changed = dataclasses.replace(repo_two_stocks.load_config(1),
                                  amount_per_stage=2_000_000)
    with pytest.raises(ValueError, match="IDLE"):
        repo_two_stocks.update_config(changed, at=AT)
    assert repo_two_stocks.load_config(1).amount_per_stage == 500_000


def test_update_config_requires_a_config_id(repo_two_stocks):
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    orphan = dataclasses.replace(repo_two_stocks.load_config(1),
                                 config_id=None)
    with pytest.raises(ValueError, match="config_id"):
        repo_two_stocks.update_config(orphan, at=AT)


def test_update_config_rejects_a_missing_row(repo_two_stocks):
    ghost = dataclasses.replace(repo_two_stocks.load_config(1),
                                config_id=9999)
    with pytest.raises(RowNotFound):
        repo_two_stocks.update_config(ghost, at=AT)


def test_update_config_does_not_change_status(repo_two_stocks):
    """상태는 `set_config_status` 의 몫이다 — 두 경로가 같은 컬럼을 쓰면
    어느 쪽이 최신인지 알 수 없다."""
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    changed = dataclasses.replace(repo_two_stocks.load_config(1),
                                  status="ACTIVE", label="바뀜")
    repo_two_stocks.update_config(changed, at=AT)
    assert repo_two_stocks.load_config(1).status == "IDLE"
    assert repo_two_stocks.load_config(1).label == "바뀜"


def test_update_config_does_not_change_created_at(repo_two_stocks):
    """최초 등록 시각은 이력이다 — 수정이 그것을 덮으면 언제 만든 설정인지
    알 수 없다."""
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    before = repo_two_stocks.load_config(1).created_at
    later = AT.replace(hour=15)
    repo_two_stocks.update_config(
        dataclasses.replace(repo_two_stocks.load_config(1), label="바뀜",
                            created_at=later),
        at=later)
    reloaded = repo_two_stocks.load_config(1)
    assert reloaded.created_at == before
    assert reloaded.updated_at == later


def test_port_declares_update_config():
    assert "update_config" in RepositoryPort.__protocol_attrs__


def test_update_config_refuses_to_change_the_stock_code(repo_two_stocks):
    """`integrity-control-bypass` — 배경 보안 리뷰가 지적한 것.

    `forced_close_baseline` 이 `cycle` 을 `split_config.stock_code` 로 조인하므로
    종목 코드를 바꾸면 **강제 종료 대사 기준선이 조용히 다른 종목으로 옮겨간다.**
    그러면 강제 종료한 적 없는 종목에서 실제 불일치가 그만큼 상쇄되고 D13 자동
    정지가 발동하지 않는다 — 대사는 프로그램이 브로커와의 어긋남을 잡는 유일한
    장치다.

    설정은 특정 종목에 대한 계획이므로 종목을 바꾸는 것은 애초에 다른 설정이다.
    """
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    moved = dataclasses.replace(repo_two_stocks.load_config(1),
                                stock_code="035720", stock_name="카카오")
    with pytest.raises(ValueError, match="stock_code"):
        repo_two_stocks.update_config(moved, at=AT)
    assert repo_two_stocks.load_config(1).stock_code == "005930"


def test_the_baseline_stays_with_the_stock_that_was_force_closed(
    repo_two_stocks,
):
    """위 거부가 실제로 무엇을 지키는지 보여준다.

    강제 종료된 100주의 기준선이 005930 에 남아 있어야, 035720 의 실제 100주
    불일치가 상쇄되지 않고 D13 자동 정지가 발동한다.
    """
    from autotrading7s.domain import cycle as cycle_mod
    from autotrading7s.domain import stage as stage_mod

    cyc = repo_two_stocks.load_active_cycles()[0]
    liq = cycle_mod.begin_liquidation(cyc)
    repo_two_stocks.save_cycle(liq)
    sold = [stage_mod.force_sold(s, at=AT)
            for s in repo_two_stocks.load_stages(cyc.cycle_id)]
    repo_two_stocks.emergency_close_cycle(
        cycle=cycle_mod.force_close(liq, reason="거래정지", qty=100, at=AT),
        stages=sold)
    assert repo_two_stocks.forced_close_baseline("005930") == 100
    assert repo_two_stocks.forced_close_baseline("035720") == 0

    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    with pytest.raises(ValueError, match="stock_code"):
        repo_two_stocks.update_config(
            dataclasses.replace(repo_two_stocks.load_config(1),
                                stock_code="035720"), at=AT)

    assert repo_two_stocks.forced_close_baseline("005930") == 100
    assert repo_two_stocks.forced_close_baseline("035720") == 0
