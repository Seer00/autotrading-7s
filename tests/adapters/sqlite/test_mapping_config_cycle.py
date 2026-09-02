from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import (
    CorruptRowError,
    config_to_row,
    cycle_to_row,
    json_to_ladder,
    ladder_to_json,
    row_to_config,
    row_to_cycle,
)
from autotrading7s.ports.repository import SplitConfig
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import CloseReason, CycleStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_config(**over) -> SplitConfig:
    kwargs = dict(
        config_id=1, stock_code="005930", stock_name="삼성전자", label="기본",
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=True, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="IDLE", created_at=T0, updated_at=T0,
    )
    kwargs.update(over)
    return SplitConfig(**kwargs)  # type: ignore[arg-type]


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def test_config_round_trip():
    original = a_config()
    restored = row_to_config(config_to_row(original) | {"id": 1})
    assert restored == original


def test_config_round_trip_preserves_decimal_exactly():
    """0.1666 이 0.1666 으로 돌아와야 한다 — 사다리 계산이 이 값에 달려 있다."""
    original = a_config(drop_pct=Decimal("0.1666"))
    restored = row_to_config(config_to_row(original) | {"id": 1})
    assert restored.drop_pct == Decimal("0.1666")
    assert str(restored.drop_pct) == "0.1666"


def test_config_round_trip_preserves_bool():
    for value in (True, False):
        restored = row_to_config(config_to_row(a_config(allow_rebuy=value))
                                 | {"id": 1})
        assert restored.allow_rebuy is value


def test_config_row_stores_ratios_as_text():
    row = config_to_row(a_config())
    assert isinstance(row["drop_pct"], str)
    assert isinstance(row["target_pct"], str)
    assert row["allow_rebuy"] in (0, 1)


def test_row_to_config_wraps_a_corrupt_row():
    """max_stages=9 는 도메인이 거부한다 — 어느 행인지 알려줘야 한다."""
    row = config_to_row(a_config()) | {"id": 42, "max_stages": 9}
    with pytest.raises(CorruptRowError) as exc:
        row_to_config(row)
    assert "split_config" in str(exc.value)
    assert "42" in str(exc.value)


def test_corrupt_row_error_is_a_domain_invariant_error():
    assert issubclass(CorruptRowError, DomainInvariantError)


def test_row_to_config_refuses_a_naive_timestamp():
    row = config_to_row(a_config()) | {"id": 1, "created_at": "2026-09-01T09:00:00"}
    with pytest.raises(CorruptRowError):
        row_to_config(row)


def test_ladder_json_round_trip():
    original = a_ladder()
    restored = json_to_ladder(ladder_to_json(original))
    assert restored == original
    assert restored.trigger_price(7) == original.trigger_price(7)


def test_ladder_json_stores_ratios_as_text():
    import json

    payload = json.loads(ladder_to_json(a_ladder()))
    assert payload["drop_pct"] == "0.05"
    assert payload["anchor_price"] == 10_000


def test_json_to_ladder_wraps_a_corrupt_snapshot():
    with pytest.raises(CorruptRowError, match="ladder_json"):
        json_to_ladder('{"anchor_price": 10000, "drop_pct": "0.05", '
                       '"target_pct": "0.05", "max_stages": 9, '
                       '"amount_per_stage": 1000000}')


def test_json_to_ladder_wraps_malformed_json():
    with pytest.raises(CorruptRowError, match="ladder_json"):
        json_to_ladder("{not json")


def test_cycle_round_trip_running():
    lad = a_ladder()
    original = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
                     anchor_price=10_000, ladder=lad, started_at=T0)
    restored = row_to_cycle(cycle_to_row(original) | {"id": 1})
    assert restored == original


def test_cycle_round_trip_idle_with_no_anchor():
    original = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE,
                     started_at=T0)
    restored = row_to_cycle(cycle_to_row(original) | {"id": 1})
    assert restored == original
    assert restored.anchor_price is None and restored.ladder is None


def test_cycle_round_trip_closed_forced():
    """D20 — 강제 종료의 증언과 잔량이 왕복해야 한다."""
    lad = a_ladder()
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=10_000, ladder=lad, started_at=T0)
    ) | {
        "id": 1, "status": "CLOSED", "close_reason": "FORCED",
        "forced_close_reason": "거래정지로 청산 불가", "forced_close_qty": 40,
        "closed_at": "2026-09-01T15:30:00+00:00",
    }
    restored = row_to_cycle(row)
    assert restored.status is CycleStatus.CLOSED
    assert restored.close_reason is CloseReason.FORCED


def test_row_to_cycle_wraps_an_anchor_ladder_mismatch():
    """설계서 4.2절이 같은 숫자를 두 곳에 쓰므로 복원 시 어긋날 수 있다."""
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=10_000, ladder=a_ladder(), started_at=T0)
    ) | {"id": 7, "anchor_price": 9_000}
    with pytest.raises(CorruptRowError) as exc:
        row_to_cycle(row)
    assert "cycle" in str(exc.value) and "7" in str(exc.value)


def test_row_to_cycle_wraps_an_unknown_status():
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE, started_at=T0)
    ) | {"id": 3, "status": "BOGUS"}
    with pytest.raises(CorruptRowError):
        row_to_cycle(row)
