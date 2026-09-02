from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.stage import (
    IllegalStageTransition,
    StageState,
    after_sell,
    cancel_buy,
    cancel_sell,
    force_sold,
    to_buy_pending,
    to_holding,
    to_sell_pending,
)
from autotrading7s.domain.types import StageStatus

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def waiting(stage_no: int = 2) -> StageState:
    return StageState(
        stage_no=stage_no,
        status=StageStatus.WAITING,
        trigger_price=9_500,
        planned_qty=105,
    )


def holding() -> StageState:
    return to_holding(to_buy_pending(waiting()), fill_price=9_480, fill_qty=105, at=T0)


def test_happy_path_buy_then_sell_with_rebuy():
    st = waiting()
    assert st.held_qty == 0

    st = to_buy_pending(st)
    assert st.status is StageStatus.BUY_PENDING
    assert st.held_qty == 0, "PENDING 중에는 보유수량으로 세지 않는다"

    st = to_holding(st, fill_price=9_480, fill_qty=105, at=T0)
    assert st.status is StageStatus.HOLDING
    assert (st.fill_price, st.fill_qty, st.bought_at) == (9_480, 105, T0)
    assert st.held_qty == 105

    st = to_sell_pending(st)
    assert st.status is StageStatus.SELL_PENDING
    assert st.held_qty == 105, "매도 체결 전까지는 여전히 보유"

    sold_at = T0 + timedelta(minutes=10)
    st = after_sell(st, at=sold_at, allow_rebuy=True)
    assert st.status is StageStatus.WAITING
    assert st.last_sold_at == sold_at
    assert st.rebuy_count == 1
    assert st.fill_price is None and st.fill_qty is None
    assert st.held_qty == 0
    assert st.trigger_price == 9_500, "발동가는 사다리에 고정되어 변하지 않는다"


def test_after_sell_without_rebuy_is_terminal():
    st = after_sell(to_sell_pending(holding()), at=T0, allow_rebuy=False)
    assert st.status is StageStatus.SOLD
    assert st.rebuy_count == 0
    with pytest.raises(IllegalStageTransition):
        to_buy_pending(st)


def test_cancel_buy_returns_to_waiting():
    st = cancel_buy(to_buy_pending(waiting()))
    assert st.status is StageStatus.WAITING
    assert st.fill_price is None


def test_cancel_sell_returns_to_holding():
    """매도 주문이 체결 없이 취소되면 보유로 되돌아간다."""
    st = cancel_sell(to_sell_pending(holding()), remaining_qty=105)
    assert st.status is StageStatus.HOLDING
    assert st.held_qty == 105


def test_cancel_sell_partial_fill_keeps_remaining_qty_only():
    """당일 마감으로 미체결 잔량이 취소되면 남은 수량만 HOLDING 으로 복귀한다."""
    holding_111 = StageState(
        stage_no=3, status=StageStatus.HOLDING, trigger_price=9_000,
        planned_qty=111, fill_price=8_950, fill_qty=111,
    )
    st = cancel_sell(to_sell_pending(holding_111), remaining_qty=71)
    assert st.status is StageStatus.HOLDING
    assert st.fill_qty == 71
    assert st.held_qty == 71


def test_cancel_sell_rejects_zero_remaining_qty():
    """0 은 전량 체결이므로 after_sell 의 몫이다 — 취소로 표현하지 않는다."""
    with pytest.raises(ValueError, match="remaining_qty"):
        cancel_sell(to_sell_pending(holding()), remaining_qty=0)


def test_cancel_sell_rejects_remaining_qty_above_fill_qty():
    """보유했던 것보다 많은 잔량을 주장할 수 없다 — 메시지에 두 값 모두 기록."""
    with pytest.raises(ValueError, match=r"remaining_qty=112.*fill_qty=105"):
        cancel_sell(to_sell_pending(holding()), remaining_qty=112)


def test_cancel_sell_rejects_negative_remaining_qty():
    with pytest.raises(ValueError, match="remaining_qty"):
        cancel_sell(to_sell_pending(holding()), remaining_qty=-1)


def test_partial_buy_fill_confirms_with_filled_quantity_only():
    """설계서 4.1절: 매수 부분체결은 체결 수량만으로 HOLDING 확정."""
    st = to_holding(to_buy_pending(waiting()), fill_price=9_480, fill_qty=60, at=T0)
    assert st.status is StageStatus.HOLDING
    assert st.fill_qty == 60
    assert st.planned_qty == 105, "계획 수량은 기록으로 남는다"


@pytest.mark.parametrize(
    ("from_status", "action"),
    [
        (StageStatus.WAITING, "to_holding"),
        (StageStatus.WAITING, "to_sell_pending"),
        (StageStatus.WAITING, "cancel_buy"),
        (StageStatus.BUY_PENDING, "to_buy_pending"),
        (StageStatus.BUY_PENDING, "to_sell_pending"),
        (StageStatus.HOLDING, "to_buy_pending"),
        (StageStatus.HOLDING, "to_holding"),
        (StageStatus.SELL_PENDING, "to_sell_pending"),
        (StageStatus.SOLD, "to_sell_pending"),
        (StageStatus.SOLD, "cancel_buy"),
        (StageStatus.WAITING, "cancel_sell"),
        (StageStatus.HOLDING, "cancel_sell"),
        (StageStatus.SOLD, "cancel_sell"),
    ],
)
def test_illegal_transitions_are_rejected(from_status: StageStatus, action: str):
    st = StageState(
        stage_no=2, status=from_status, trigger_price=9_500, planned_qty=105,
        fill_price=9_480, fill_qty=105,
    )
    fn = {
        "to_buy_pending": lambda s: to_buy_pending(s),
        "to_holding": lambda s: to_holding(s, fill_price=1, fill_qty=1, at=T0),
        "to_sell_pending": lambda s: to_sell_pending(s),
        "cancel_buy": lambda s: cancel_buy(s),
        "cancel_sell": lambda s: cancel_sell(s, remaining_qty=50),
    }[action]
    with pytest.raises(IllegalStageTransition):
        fn(st)


@pytest.mark.parametrize(
    ("from_status", "action"),
    [
        # 표는 이 전이들을 허용한다(같은 목표에 다른 출발이 도달) 하지만
        # 이 도우미는 그 출발을 위한 것이 아니다.
        (StageStatus.SELL_PENDING, "cancel_buy"),  # WAITING 목표는 after_sell 의 몫
        (StageStatus.BUY_PENDING, "cancel_sell"),  # HOLDING 목표는 to_holding 의 몫
        (StageStatus.SELL_PENDING, "to_holding"),  # HOLDING 목표는 cancel_sell 의 몫
        (StageStatus.BUY_PENDING, "after_sell"),   # WAITING 목표는 cancel_buy 의 몫
    ],
)
def test_wrong_source_helper_rejected_even_though_table_allows_target(
    from_status: StageStatus, action: str
):
    """`_ALLOWED` 가 목표 상태를 허용해도, 도우미가 노리는 출발 상태가
    아니면 거부한다. `to_holding(SELL_PENDING)` 은 표만으로는 통과하지만
    실제로는 보유 기록을 조용히 덮어쓰는 가장 위험한 경우다."""
    st = StageState(
        stage_no=3, status=from_status, trigger_price=9_000, planned_qty=111,
        fill_price=9_000, fill_qty=111,
    )
    fn = {
        "cancel_buy": lambda s: cancel_buy(s),
        "cancel_sell": lambda s: cancel_sell(s, remaining_qty=50),
        "to_holding": lambda s: to_holding(s, fill_price=1, fill_qty=1, at=T0),
        "after_sell": lambda s: after_sell(s, at=T0, allow_rebuy=True),
    }[action]
    with pytest.raises(IllegalStageTransition):
        fn(st)


def test_to_holding_wrong_source_does_not_overwrite_recorded_fill():
    """가장 위험한 오배선을 데이터 파괴 관점에서 못박는다.

    `to_holding` 을 SELL_PENDING 상태에 걸면 표는 통과시키지만, 실제로는
    9,000×111 로 기록된 포지션을 1×1 로 덮어써 버린다. 예외가 필드를
    건드리기 전에 나야 하며, 원본 `st` 는 (frozen 이므로 항상 그렇지만)
    호출 전후로 값이 그대로여야 한다.
    """
    st = StageState(
        stage_no=3, status=StageStatus.SELL_PENDING, trigger_price=9_000,
        planned_qty=111, fill_price=9_000, fill_qty=111,
    )
    with pytest.raises(IllegalStageTransition):
        to_holding(st, fill_price=1, fill_qty=1, at=T0)
    assert (st.fill_price, st.fill_qty) == (9_000, 111), "원본 기록은 파괴되지 않는다"


def test_to_buy_pending_succeeds_from_waiting():
    st = to_buy_pending(waiting())
    assert st.status is StageStatus.BUY_PENDING


def test_to_holding_succeeds_from_buy_pending():
    st = to_holding(to_buy_pending(waiting()), fill_price=9_480, fill_qty=105, at=T0)
    assert st.status is StageStatus.HOLDING
    assert st.fill_qty == 105


def test_to_sell_pending_succeeds_from_holding():
    st = to_sell_pending(holding())
    assert st.status is StageStatus.SELL_PENDING


def test_after_sell_succeeds_from_sell_pending():
    st = after_sell(to_sell_pending(holding()), at=T0, allow_rebuy=True)
    assert st.status is StageStatus.WAITING


def test_cancel_buy_succeeds_from_buy_pending():
    st = cancel_buy(to_buy_pending(waiting()))
    assert st.status is StageStatus.WAITING


def test_cancel_sell_succeeds_from_sell_pending():
    st = cancel_sell(to_sell_pending(holding()), remaining_qty=105)
    assert st.status is StageStatus.HOLDING


@pytest.mark.parametrize(
    "status",
    [StageStatus.WAITING, StageStatus.BUY_PENDING, StageStatus.HOLDING,
     StageStatus.SELL_PENDING],
)
def test_force_sold_bypasses_transition_table(status: StageStatus):
    """긴급청산은 Trigger Engine을 우회하는 별도 경로다 (설계서 11.1절)."""
    st = StageState(stage_no=3, status=status, trigger_price=9_000, planned_qty=111,
                    fill_price=8_950, fill_qty=111)
    forced = force_sold(st, at=T0)
    assert forced.status is StageStatus.SOLD
    assert forced.last_sold_at == T0
    assert forced.held_qty == 0


def test_force_sold_on_already_sold_is_idempotent():
    st = StageState(stage_no=3, status=StageStatus.SOLD, trigger_price=9_000,
                    planned_qty=111)
    assert force_sold(st, at=T0).status is StageStatus.SOLD


def test_state_is_frozen():
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        waiting().status = StageStatus.HOLDING  # type: ignore[misc]


def test_holding_requires_fill_information():
    """HOLDING 상태는 체결가와 수량이 모두 있어야 한다."""
    with pytest.raises(ValueError, match="fill_price must be positive"):
        StageState(
            stage_no=2, status=StageStatus.HOLDING, trigger_price=9_500,
            planned_qty=105, fill_price=None, fill_qty=105,
        )

    with pytest.raises(ValueError, match="fill_qty must be positive"):
        StageState(
            stage_no=2, status=StageStatus.HOLDING, trigger_price=9_500,
            planned_qty=105, fill_price=9_480, fill_qty=None,
        )


def test_sell_pending_requires_fill_information():
    """SELL_PENDING 상태도 체결가와 수량이 모두 있어야 한다."""
    with pytest.raises(ValueError, match="fill_price must be positive"):
        StageState(
            stage_no=2, status=StageStatus.SELL_PENDING, trigger_price=9_500,
            planned_qty=105, fill_price=None, fill_qty=105,
        )

    with pytest.raises(ValueError, match="fill_qty must be positive"):
        StageState(
            stage_no=2, status=StageStatus.SELL_PENDING, trigger_price=9_500,
            planned_qty=105, fill_price=9_480, fill_qty=None,
        )


def test_holding_rejects_zero_or_negative_fill():
    """HOLDING 상태에서 체결가 또는 수량이 0 이하면 거부."""
    with pytest.raises(ValueError, match="fill_price must be positive"):
        StageState(
            stage_no=2, status=StageStatus.HOLDING, trigger_price=9_500,
            planned_qty=105, fill_price=0, fill_qty=105,
        )

    with pytest.raises(ValueError, match="fill_qty must be positive"):
        StageState(
            stage_no=2, status=StageStatus.HOLDING, trigger_price=9_500,
            planned_qty=105, fill_price=9_480, fill_qty=0,
        )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("fill_price", 9_000.5),
        ("fill_qty", 111.5),
        ("fill_price", True),
        ("fill_qty", True),
        ("fill_price", Decimal(9_000)),
    ],
)
def test_holding_rejects_non_int_fill(field: str, bad_value: object):
    """float·bool·Decimal 은 모두 `<= 0` 비교를 통과하므로 타입을 직접
    확인해야 한다 — 그러지 않으면 실수 수량이 조용히 fill_qty 로
    들어가 invested_amount 등 금액 계산까지 float 로 오염시킨다."""
    kwargs = {"fill_price": 9_000, "fill_qty": 111}
    kwargs[field] = bad_value
    with pytest.raises(TypeError, match=f"{field} must be int"):
        StageState(
            stage_no=3, status=StageStatus.HOLDING, trigger_price=9_000,
            planned_qty=111, **kwargs,
        )


def test_to_holding_rejects_non_int_fill_via_invariant():
    """to_holding 은 자체 타입 검사가 없다 — StageState.__post_init__ 이
    replace() 를 거쳐 대신 잡아준다."""
    st = to_buy_pending(waiting())
    with pytest.raises(TypeError, match="fill_price must be int"):
        to_holding(st, fill_price=9_000.5, fill_qty=105, at=T0)

    st = to_buy_pending(waiting())
    with pytest.raises(TypeError, match="fill_qty must be int"):
        to_holding(st, fill_price=9_000, fill_qty=True, at=T0)


@pytest.mark.parametrize("bad_value", [50.5, True])
def test_cancel_sell_rejects_non_int_remaining_qty(bad_value: object):
    """예외 메시지가 remaining_qty 를 가리켜야 한다 — fill_qty 를 가리키면
    호출자가 어느 인자를 잘못 넘겼는지 알 수 없다."""
    with pytest.raises(TypeError, match="remaining_qty must be int"):
        cancel_sell(to_sell_pending(holding()), remaining_qty=bad_value)


def test_cancel_sell_accepts_valid_int_remaining_qty():
    st = cancel_sell(to_sell_pending(holding()), remaining_qty=71)
    assert st.fill_qty == 71
    assert type(st.fill_qty) is int


def _base_kwargs(**over) -> dict:
    kwargs: dict = {
        "stage_no": 2,
        "status": StageStatus.WAITING,
        "trigger_price": 9_500,
        "planned_qty": 105,
    }
    kwargs.update(over)
    return kwargs


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("stage_no", 0, "stage_no must be positive"),
        ("stage_no", -1, "stage_no must be positive"),
        ("trigger_price", 0, "trigger_price must be positive"),
        ("trigger_price", -500, "trigger_price must be positive"),
        ("planned_qty", -5, "planned_qty must be non-negative"),
        ("rebuy_count", -3, "rebuy_count must be non-negative"),
    ],
)
def test_rejects_out_of_range_identity_fields(field: str, bad_value: int, message: str):
    """체결 필드만이 아니라 단계의 정체성 필드도 불변식을 갖는다.

    Plan 2 가 SQLite 행에서 이 값들을 복원할 때 손상된 행(단계 0, 음수
    발동가·수량)이 그대로 트리거 판정에 들어가면, 발동가 비교와 수량 산술이
    조용히 뒤집힌다.
    """
    with pytest.raises(ValueError, match=message):
        StageState(**_base_kwargs(**{field: bad_value}))


@pytest.mark.parametrize(
    "field", ["stage_no", "trigger_price", "planned_qty", "rebuy_count"]
)
@pytest.mark.parametrize("bad_value", [2.5, True, Decimal(2)])
def test_rejects_non_int_identity_fields(field: str, bad_value: object):
    """float·bool·Decimal 은 모두 크기 비교를 통과하므로 타입을 직접 검사한다."""
    with pytest.raises(TypeError, match=f"{field} must be int"):
        StageState(**_base_kwargs(**{field: bad_value}))


def test_accepts_zero_planned_qty_and_first_stage():
    """경계값은 유효하다 — 1단계, 계획수량 0, 재매수 0회."""
    st = StageState(**_base_kwargs(stage_no=1, planned_qty=0, rebuy_count=0))
    assert (st.stage_no, st.planned_qty, st.rebuy_count) == (1, 0, 0)


def test_to_holding_rejects_nonpositive_fill_in_transition_context():
    """전이 문맥의 검사는 전이 실패 메시지를 낸다 — `__post_init__` 이
    최종 방어선이지만, 그 메시지는 호출자가 넘긴 인자를 가리키지 않는다."""
    st = to_buy_pending(waiting())
    with pytest.raises(ValueError, match=r"invalid fill: price=0 qty=105"):
        to_holding(st, fill_price=0, fill_qty=105, at=T0)

    st = to_buy_pending(waiting())
    with pytest.raises(ValueError, match=r"invalid fill: price=9480 qty=0"):
        to_holding(st, fill_price=9_480, fill_qty=0, at=T0)


def test_non_holding_statuses_allow_no_fill():
    """WAITING, BUY_PENDING, SOLD 상태는 체결 정보 없이 구성 가능."""
    # WAITING
    st = StageState(
        stage_no=2, status=StageStatus.WAITING, trigger_price=9_500,
        planned_qty=105,
    )
    assert st.fill_price is None and st.fill_qty is None

    # BUY_PENDING
    st = StageState(
        stage_no=2, status=StageStatus.BUY_PENDING, trigger_price=9_500,
        planned_qty=105,
    )
    assert st.fill_price is None and st.fill_qty is None

    # SOLD
    st = StageState(
        stage_no=2, status=StageStatus.SOLD, trigger_price=9_500,
        planned_qty=105,
    )
    assert st.fill_price is None and st.fill_qty is None
