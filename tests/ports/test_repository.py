from __future__ import annotations

import dataclasses
import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import CycleStatus
from autotrading7s.ports.repository import HoldingRow, RepositoryPort, SplitConfig

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_config(**over) -> SplitConfig:
    kw = dict(config_id=None, stock_code="005930", stock_name="삼성전자",
              label="기본", max_stages=7, drop_pct=FIVE, target_pct=FIVE,
              amount_per_stage=1_000_000, allow_rebuy=True,
              rebuy_cooldown_sec=60, total_limit=7_000_000, status="ACTIVE",
              created_at=T0, updated_at=T0)
    return SplitConfig(**{**kw, **over})


def test_repository_port_declares_the_expected_methods():
    """Task 8~10 이 채워야 하는 목록. 여기가 고정되어야 진행 상황을 셀 수 있다."""
    expected = {
        # 설정
        "save_config", "load_config", "list_configs", "set_config_status",
        # 사이클과 단계
        "create_cycle", "load_cycle", "save_cycle", "load_stages", "save_stage",
        "load_active_cycles",
        # 주문 이력과 실현손익
        "append_order_log", "update_order_log", "load_pending_orders",
        "realized_pnl_for_cycle",
        # 긴급청산·대사 이력
        "append_emergency_log", "append_reconcile_log",
        # 보유현황 뷰
        "holdings",
    }
    declared = {
        name for name, _ in inspect.getmembers(RepositoryPort, inspect.isfunction)
        if not name.startswith("_")
    }
    assert declared == expected


def test_repository_port_is_runtime_checkable():
    assert getattr(RepositoryPort, "_is_runtime_protocol", False) is True


def test_split_config_to_ladder_carries_every_field_through():
    """설정의 어느 필드가 사다리로 흘러가는지 고정한다 — 이름을 잘못 짝지으면
    앵커 확정 시점에 조용히 다른 사다리가 만들어진다."""
    lad = a_config().to_ladder(anchor_price=10_000)
    assert lad == Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=FIVE,
                         max_stages=7, amount_per_stage=1_000_000)
    # 1단계는 앵커 그대로, 2단계는 5% 아래(호가 단위로 내림)
    assert lad.trigger_price(1) == 10_000
    assert lad.trigger_price(2) == 9_500


def test_split_config_to_ladder_rejects_an_invalid_anchor():
    """`Ladder` 의 검증이 이 경계에서도 살아 있어야 한다 — 설정이 유효해도
    앵커가 유효하지 않으면 사다리는 만들어지지 않는다."""
    from autotrading7s.domain.ladder import LadderConfigError

    with pytest.raises(LadderConfigError):
        a_config().to_ladder(anchor_price=0)


def test_the_contract_dtos_are_frozen():
    """엔진과 UI 가 같은 객체를 들고 있으므로 변경 불가여야 한다."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        a_config().stock_code = "000660"  # type: ignore[misc]

    row = HoldingRow(stock_code="005930", stock_name="삼성전자", label="기본",
                     cycle_id=1, total_qty=316, avg_price=9_458,
                     holding_stages=3, max_stages=7,
                     cycle_status=CycleStatus.RUNNING)
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.total_qty = 0  # type: ignore[misc]
