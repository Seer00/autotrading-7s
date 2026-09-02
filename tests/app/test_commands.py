from __future__ import annotations

import dataclasses

import pytest

from autotrading7s.app.commands import (
    Command,
    EmergencyLiquidate,
    ForceClose,
    PauseCycle,
    PriorityCommand,
    ResetReconcileBaseline,
    ResumeCycle,
    Shutdown,
    StartCycle,
    StopCycle,
)


def test_all_commands_are_frozen_dataclasses():
    for cls in (StartCycle, PauseCycle, ResumeCycle, StopCycle, EmergencyLiquidate,
                ForceClose, ResetReconcileBaseline, Shutdown):
        assert dataclasses.is_dataclass(cls), cls
        assert cls.__dataclass_params__.frozen, cls


def test_only_emergency_commands_are_priority():
    """priority_q 자격이 타입으로 표현된다 — 설계서 7.1절.

    긴급 기능의 즉시성을 주석이 아니라 타입이 보장해야 한다. 오케스트레이터가
    priority_q 에서 꺼낸 것이 PriorityCommand 인지 단정할 수 있어야 한다.
    """
    assert issubclass(EmergencyLiquidate, PriorityCommand)
    assert issubclass(ForceClose, PriorityCommand)
    assert not issubclass(StartCycle, PriorityCommand)
    assert not issubclass(StopCycle, PriorityCommand)
    assert issubclass(PriorityCommand, Command)


def test_force_close_requires_nonempty_reason():
    """D20 — 증언 기록을 타입이 강제한다 (설계서 11.4절 설계 제약)."""
    with pytest.raises(ValueError, match="reason"):
        ForceClose(config_id=1, reason="", confirmed_text="강제종료")
    with pytest.raises(ValueError, match="reason"):
        ForceClose(config_id=1, reason="   ", confirmed_text="강제종료")


def test_force_close_requires_exact_confirmation_text():
    """설계서 11.4절 — `강제종료` 를 직접 입력해야 한다."""
    with pytest.raises(ValueError, match="강제종료"):
        ForceClose(config_id=1, reason="거래정지", confirmed_text="네")
    cmd = ForceClose(config_id=1, reason="거래정지", confirmed_text="강제종료")
    assert cmd.reason == "거래정지"


def test_emergency_liquidate_all_scope_has_no_config_id():
    """전체 청산은 종목을 지정하지 않는다 (설계서 11.2절)."""
    cmd = EmergencyLiquidate(scope="ALL", config_id=None, reason=None,
                             confirmed_text="전체청산")
    assert cmd.scope == "ALL"
    with pytest.raises(ValueError, match="config_id"):
        EmergencyLiquidate(scope="SINGLE", config_id=None, reason=None,
                           confirmed_text=None)
    with pytest.raises(ValueError, match="전체청산"):
        EmergencyLiquidate(scope="ALL", config_id=None, reason=None,
                           confirmed_text=None)


def test_emergency_liquidate_rejects_unknown_scope():
    with pytest.raises(ValueError, match="scope"):
        EmergencyLiquidate(scope="EVERYTHING", config_id=None, reason=None,
                           confirmed_text="전체청산")


def test_start_cycle_carries_config_id_only():
    """앵커 확정은 엔진이 첫 틱에서 한다 — GUI 가 가격을 정하지 않는다."""
    cmd = StartCycle(config_id=7)
    assert dataclasses.asdict(cmd) == {"config_id": 7}


def test_reset_reconcile_baseline_targets_a_stock():
    """2A 핸드오버 7 / 설계서 11.4절 — 강제 종료 기준선 초기화."""
    cmd = ResetReconcileBaseline(stock_code="005930")
    assert cmd.stock_code == "005930"


def test_shutdown_is_a_plain_command():
    assert isinstance(Shutdown(), Command)
    assert not isinstance(Shutdown(), PriorityCommand)
