from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autotrading7s.app.events import Event
from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus

AT = datetime(2026, 9, 2, 9, 42, tzinfo=UTC)
PCT = Decimal("0.05")


def _ladder(anchor=10_000, stages=7) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=PCT, target_pct=PCT,
                  max_stages=stages, amount_per_stage=1_000_000)


def _stages(ladder: Ladder, holding=()) -> tuple[StageState, ...]:
    out = []
    for n in range(1, ladder.max_stages + 1):
        st = StageState(stage_no=n, status=StageStatus.WAITING,
                        trigger_price=ladder.trigger_price(n),
                        planned_qty=ladder.planned_qty(n))
        if n in holding:
            st = dataclasses.replace(
                st, status=StageStatus.HOLDING,
                fill_price=ladder.trigger_price(n),
                fill_qty=ladder.planned_qty(n), bought_at=AT)
        out.append(st)
    return tuple(out)


def _config(**over) -> ConfigSnapshot:
    ladder = over.pop("ladder", _ladder())
    # 사다리를 None 으로 덮는 경우(IDLE 설정)에는 기본 단계도 만들 수 없다.
    default_stages = () if ladder is None else _stages(ladder,
                                                       holding=(1, 2, 3))
    kw = dict(
        config_id=1, stock_code="005930", stock_name="삼성전자", label="기본",
        config_status="ACTIVE", max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=True, rebuy_cooldown_sec=60,
        stock_limit=7_000_000, cycle_id=2, cycle_seq=2,
        cycle_status=CycleStatus.RUNNING, anchor_price=10_000, ladder=ladder,
        cycle_started_at=AT, stages=default_stages,
        pending_orders=0,
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def test_snapshot_is_an_event_so_it_flows_on_the_event_queue():
    """스냅샷은 이벤트다 — 큐 계약이 한 방향으로 유지된다.

    요청-응답 채널을 만들면 상관 ID 와 블로킹이 필요한 두 번째 프로토콜이
    생기고, 설계서 7.1절이 말한 "큐를 소켓으로 교체" 가 단순한 작업이 아니게
    된다.
    """
    snap = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    assert isinstance(snap, Event)


def test_snapshot_and_config_snapshot_are_frozen():
    for cls in (Snapshot, ConfigSnapshot):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, cls


def test_snapshot_rejects_a_naive_timestamp():
    with pytest.raises(ValueError, match="tz-aware"):
        Snapshot(configs=(), total_limit=1, at=datetime(2026, 9, 2, 9, 42))


def test_config_snapshot_carries_config_id_for_commands():
    """`holdings()` 뷰에는 config_id 가 없다 — 그래서 명령 대상을 알 수 없다.

    GUI 의 [시작]·[일시정지]·[긴급청산]이 모두 config_id 를 보내므로 표의 각
    행이 그것을 알아야 한다.
    """
    assert _config().config_id == 1


def test_config_snapshot_can_describe_an_idle_config_with_no_cycle():
    """설계서 14.1절 목업의 `NAVER 0/5 IDLE` 행.

    `holdings()` 뷰는 보유 단계가 없으면 행 자체를 만들지 않는다 — 그 행을
    그릴 수 있는 것이 스냅샷을 따로 두는 이유다.
    """
    idle = _config(config_status="IDLE", cycle_id=None, cycle_seq=None,
                   cycle_status=None, anchor_price=None, ladder=None,
                   cycle_started_at=None, stages=())
    assert idle.cycle_status is None
    assert idle.stages == ()


def test_stages_are_domain_objects_so_pnl_can_be_called_directly():
    """설계서 14.4절 — 표시용 계산조차 domain/pnl.py 를 호출한다.

    별도 DTO 로 옮기면 뷰모델이 pnl 을 쓸 수 없어 계산을 다시 구현하게 되고,
    그것이 14.4절이 금지한 바로 그것이다.
    """
    from autotrading7s.domain import pnl

    snap = _config()
    assert all(isinstance(s, StageState) for s in snap.stages)
    assert pnl.holding_stage_count(snap.stages) == 3
    assert pnl.held_qty(snap.stages) > 0


def test_core_ignores_the_timestamp_so_idle_ticks_emit_nothing():
    """리비전 비교는 `at` 을 무시해야 한다.

    포함하면 매 틱마다 스냅샷이 달라져서 유휴 구간에도 큐가 자란다. 시간
    기준으로 주기를 두는 대안은 FakeClock 이 멈춘 테스트에서 첫 스냅샷만
    나가게 만든다.
    """
    a = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    b = Snapshot(configs=(_config(),), total_limit=21_000_000,
                 at=AT.replace(hour=10))
    assert a != b                      # `at` 이 다르므로 객체는 다르다
    assert a.core == b.core             # 그러나 리비전은 같다


def test_core_changes_when_a_stage_fills():
    a = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    ladder = _ladder()
    b = Snapshot(configs=(_config(stages=_stages(ladder, holding=(1, 2, 3, 4))),),
                 total_limit=21_000_000, at=AT)
    assert a.core != b.core


def test_core_changes_when_the_total_limit_changes():
    a = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    b = Snapshot(configs=(_config(),), total_limit=20_000_000, at=AT)
    assert a.core != b.core


def test_core_changes_when_pending_orders_change():
    """미체결 건수는 긴급청산 다이얼로그의 '함께 취소됩니다' 안내에 쓰인다.

    리비전에서 빠지면 그 숫자가 화면에서 갱신되지 않는다.
    """
    a = Snapshot(configs=(_config(pending_orders=0),), total_limit=1, at=AT)
    b = Snapshot(configs=(_config(pending_orders=2),), total_limit=1, at=AT)
    assert a.core != b.core
