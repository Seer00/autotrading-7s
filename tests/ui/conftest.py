"""뷰모델 테스트의 스냅샷 픽스처.

설계서 14.1절 목업의 세 행(삼성전자 3/7 감시, 카카오 7/7 소진, NAVER 0/5 IDLE)
을 그대로 만든다 — 목업이 이 계획의 사양이므로 그것을 재현할 수 있어야 한다.
목업의 `보유 316주 / 평균단가 9,458원` 이 이 픽스처에서 그대로 나온다.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus

AT = datetime(2026, 9, 2, 9, 42, tzinfo=UTC)
PCT = Decimal("0.05")


def ladder(anchor: int, *, stages: int = 7, amount: int = 1_000_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=PCT, target_pct=PCT,
                  max_stages=stages, amount_per_stage=amount)


def stages_of(lad: Ladder, *, holding: dict[int, tuple[int, int]] | None = None,
              sold: tuple[int, ...] = ()) -> tuple[StageState, ...]:
    """`holding` 은 {단계: (체결가, 수량)}."""
    holding = holding or {}
    out = []
    for n in range(1, lad.max_stages + 1):
        st = StageState(stage_no=n, status=StageStatus.WAITING,
                        trigger_price=lad.trigger_price(n),
                        planned_qty=lad.planned_qty(n))
        if n in holding:
            price, qty = holding[n]
            st = dataclasses.replace(st, status=StageStatus.HOLDING,
                                     fill_price=price, fill_qty=qty,
                                     bought_at=AT)
        elif n in sold:
            st = dataclasses.replace(st, status=StageStatus.SOLD,
                                     last_sold_at=AT, rebuy_count=1)
        out.append(st)
    return tuple(out)


def config(**over) -> ConfigSnapshot:
    lad = over.pop("ladder", ladder(10_000))
    default_stages = () if lad is None else stages_of(
        lad, holding={1: (10_000, 100), 2: (9_480, 105), 3: (8_950, 111)})
    kw = dict(
        config_id=1, stock_code="005930", stock_name="삼성전자", label="기본",
        config_status="ACTIVE", max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=True, rebuy_cooldown_sec=60,
        stock_limit=7_000_000, cycle_id=2, cycle_seq=2,
        cycle_status=CycleStatus.RUNNING,
        anchor_price=None if lad is None else lad.anchor_price,
        ladder=lad, cycle_started_at=AT, pending_orders=0,
        stages=default_stages,
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def idle_config(**over) -> ConfigSnapshot:
    """설계서 14.1절의 `NAVER 0/5 IDLE` 행."""
    kw = dict(
        config_id=3, stock_code="035420", stock_name="NAVER", label="기본",
        config_status="IDLE", max_stages=5, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=False, rebuy_cooldown_sec=60,
        stock_limit=5_000_000, cycle_id=None, cycle_seq=None,
        cycle_status=None, anchor_price=None, ladder=None,
        cycle_started_at=None, stages=(), pending_orders=0,
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def exhausted_config(**over) -> ConfigSnapshot:
    """설계서 14.1절의 `카카오 7/7 소진` 행 — 전 단계가 보유 중이다."""
    lad = ladder(9_000, amount=1_000_000)
    holding = {n: (lad.trigger_price(n), lad.planned_qty(n))
               for n in range(1, 8)}
    kw = dict(
        config_id=2, stock_code="035720", stock_name="카카오", label="공격형",
        config_status="ACTIVE", max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=True, rebuy_cooldown_sec=60,
        stock_limit=7_000_000, cycle_id=5, cycle_seq=1,
        cycle_status=CycleStatus.RUNNING, anchor_price=9_000, ladder=lad,
        cycle_started_at=AT, stages=stages_of(lad, holding=holding),
        pending_orders=0,
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def snapshot(*configs: ConfigSnapshot, total_limit: int = 21_000_000) -> Snapshot:
    return Snapshot(configs=configs or (config(),), total_limit=total_limit,
                    at=AT)


@pytest.fixture
def three_row_snapshot() -> Snapshot:
    return snapshot(config(), exhausted_config(), idle_config())
