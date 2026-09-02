from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder, LadderConfigError, target_price
from autotrading7s.domain.rules import BuyStage, TriggerParams
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.tick_size import normalize_tick
from autotrading7s.domain.types import Side, StageStatus, Tick, TickSource

FIVE = Decimal("0.05")
T0 = __import__("datetime").datetime(2026, 9, 1, 9, 0,
                                     tzinfo=__import__("datetime").timezone.utc)


def test_domain_invariant_error_is_a_value_error():
    """기존 호출부가 ValueError 를 잡고 있으므로 하위 호환을 유지한다."""
    assert issubclass(DomainInvariantError, ValueError)


def test_ladder_config_error_is_a_domain_invariant_error():
    """복원된 ladder_json 이 이것을 낼 수 있으므로 매핑 계층이 함께 잡아야 한다."""
    assert issubclass(LadderConfigError, DomainInvariantError)


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(
            lambda: Tick(code="005930", price=0, at=T0, source=TickSource.WS),
            id="Tick.price",
        ),
        pytest.param(
            lambda: StageState(stage_no=0, status=StageStatus.WAITING,
                               trigger_price=9_000, planned_qty=111),
            id="StageState.stage_no",
        ),
        pytest.param(
            lambda: TriggerParams(target_pct=Decimal("0")),
            id="TriggerParams.target_pct",
        ),
        pytest.param(
            lambda: BuyStage(stage_no=1, limit_price=0, qty=10, reason="t"),
            id="BuyStage.limit_price",
        ),
    ],
)
def test_post_init_value_failures_raise_domain_invariant_error(make):
    """복원된 행이 만드는 실패는 DomainInvariantError 여야 한다."""
    with pytest.raises(DomainInvariantError):
        make()


def test_ladder_value_failure_raises_ladder_config_error():
    with pytest.raises(LadderConfigError):
        Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=Decimal("0"),
               max_stages=7, amount_per_stage=1_000_000)


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: normalize_tick(Decimal(0), Side.BUY),
                     id="normalize_tick"),
        pytest.param(lambda: target_price(0, FIVE), id="target_price"),
    ],
)
def test_argument_failures_stay_plain_value_error(make):
    """호출 인자 검증은 DomainInvariantError 가 아니다 — 데이터 손상이 아니라 버그다."""
    with pytest.raises(ValueError) as exc:
        make()
    assert not isinstance(exc.value, DomainInvariantError)


def test_type_failures_are_unchanged():
    """TypeError 는 하나도 바꾸지 않는다."""
    with pytest.raises(TypeError):
        Tick(code="005930", price=9340.5, at=T0, source=TickSource.WS)
