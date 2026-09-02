from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.cycle import Cycle, IllegalCycleTransition
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    StageStatus,
)

AT = datetime(2026, 9, 2, 15, 28, tzinfo=UTC)


def _ladder() -> Ladder:
    return Ladder(anchor_price=10_000, drop_pct=Decimal("0.05"),
                  target_pct=Decimal("0.05"), max_stages=7,
                  amount_per_stage=1_000_000)


def _liquidating() -> Cycle:
    return Cycle(cycle_id=1, config_id=1, seq=1,
                 status=CycleStatus.LIQUIDATING, anchor_price=10_000,
                 ladder=_ladder(), started_at=AT)


def test_force_close_records_the_statement_and_the_remainder():
    """설계서 11.4절 ⑤ — 증언과 잔량이 둘 다 기록된다."""
    closed = cycle_mod.force_close(
        _liquidating(), reason="거래정지로 청산 불가, 잔량 40주는 직접 처리 예정",
        qty=40, at=AT,
    )
    assert closed.status is CycleStatus.CLOSED
    assert closed.close_reason is CloseReason.FORCED
    assert closed.forced_close_qty == 40
    assert "거래정지" in closed.forced_close_reason
    assert closed.closed_at == AT


def test_force_close_only_from_liquidating():
    """설계서 11.4절 설계 제약 — 사용자가 먼저 긴급청산을 시도해야 한다.

    그 시도 이력(횟수·시각·실패 사유)이 강제 종료 다이얼로그의 근거가 된다.
    RUNNING 에서 바로 강제 종료하는 경로를 두면 그 근거 없이 내부 기록과
    실계좌를 어긋나게 만들 수 있다.
    """
    for status in (CycleStatus.RUNNING, CycleStatus.PAUSED):
        cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status,
                    anchor_price=10_000, ladder=_ladder(), started_at=AT)
        with pytest.raises(IllegalCycleTransition):
            cycle_mod.force_close(cyc, reason="사유", qty=40, at=AT)
    starting = Cycle(cycle_id=1, config_id=1, seq=1,
                     status=CycleStatus.STARTING, started_at=AT)
    with pytest.raises(IllegalCycleTransition):
        cycle_mod.force_close(starting, reason="사유", qty=40, at=AT)


def test_force_close_does_not_check_completion():
    """close() 와 달리 단계 목록을 요구하지 않는다 — 보유가 남은 채로 끝난다.

    이것이 close() 의 우회가 아니라 별도 경로인 이유다. close() 는 보유 0 을
    확인하고, force_close 는 보유가 남았다는 사실 자체를 기록한다.
    """
    import ast
    import textwrap

    # 문자열 검색이 아니라 호출 그래프를 본다 — 독스트링이 그 이름을 언급하는
    # 것과 실제로 부르는 것은 다르고, 문자열 검색은 그 차이를 구별하지 못한다.
    tree = ast.parse(textwrap.dedent(inspect.getsource(cycle_mod.force_close)))
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "is_cycle_complete" not in called
    assert "close" not in called


def test_force_close_rejects_an_empty_statement():
    with pytest.raises(DomainInvariantError, match="reason"):
        cycle_mod.force_close(_liquidating(), reason="   ", qty=40, at=AT)


def test_force_close_rejects_zero_remainder():
    """설계서 11.4절 절차 ③ — 잔량이 0 이면 정상 close() 로 처리해야 한다.

    잔량 0 의 강제 종료는 의미가 없고, 허용하면 정상 종료 경로를 우회해
    보유 0 검사를 건너뛰는 수단이 된다.
    """
    with pytest.raises(DomainInvariantError, match="qty"):
        cycle_mod.force_close(_liquidating(), reason="사유", qty=0, at=AT)
    with pytest.raises(TypeError):
        cycle_mod.force_close(_liquidating(), reason="사유", qty=1.0, at=AT)


def test_forced_fields_and_close_reason_must_agree():
    """스키마의 D20 CHECK 와 같은 것을 도메인에서도 말한다.

    두 층이 같은 불변식을 말하면 어긋날 수 없다. 한 층만 말하면 다른 경로로
    들어온 값이 통과한다.
    """
    with pytest.raises(DomainInvariantError, match="FORCED"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED,
              close_reason=CloseReason.FORCED, started_at=AT, closed_at=AT)
    with pytest.raises(DomainInvariantError, match="FORCED"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED,
              close_reason=CloseReason.NORMAL, forced_close_qty=40,
              forced_close_reason="사유", started_at=AT, closed_at=AT)


def test_forced_close_qty_must_be_a_positive_int():
    with pytest.raises(TypeError, match="forced_close_qty"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED,
              close_reason=CloseReason.FORCED, forced_close_reason="사유",
              forced_close_qty=40.0, started_at=AT, closed_at=AT)
    with pytest.raises(DomainInvariantError, match="forced_close_qty"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED,
              close_reason=CloseReason.FORCED, forced_close_reason="사유",
              forced_close_qty=-1, started_at=AT, closed_at=AT)


def test_normal_close_leaves_forced_fields_empty():
    lad = _ladder()
    states = [StageState(stage_no=n, status=StageStatus.WAITING,
                         trigger_price=lad.trigger_price(n),
                         planned_qty=lad.planned_qty(n))
              for n in range(1, 8)]
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
                anchor_price=10_000, ladder=lad, started_at=AT)
    closed = cycle_mod.close(cyc, reason=CloseReason.NORMAL, at=AT,
                             states=states)
    assert closed.forced_close_reason is None
    assert closed.forced_close_qty is None
