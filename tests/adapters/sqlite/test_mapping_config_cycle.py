from __future__ import annotations

import dataclasses
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


def test_cycle_round_trip_closed_forced_carries_statement_and_remainder():
    """D20 — 강제 종료의 증언과 잔량이 왕복한다 (설계서 11.4절).

    Plan 2A 는 이 테스트를 "그 둘이 왕복하지 **않는다**" 는 부재 고정으로
    남겨두고, `Cycle` 에 필드가 생기면 실패로 알려주도록 해뒀다 — 그때
    "이름을 바꿔 실제로 왕복하는지 검증하는 테스트로 승격해야 한다" 는 신호를
    붙여서. Plan 2B 가 D20 전이를 추가하면서 그 신호가 울렸고, 여기가 그
    승격이다.

    왕복이 중요한 이유: 증언은 사용자가 "잔량이 얼마인지 알고 있으며 내가
    처리한다" 고 기록한 것이고, 그것을 잃으면 프로그램 관리 밖에 남은 주식이
    왜 그렇게 됐는지 답할 수 있는 유일한 근거가 사라진다.
    """
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
    assert restored.forced_close_reason == "거래정지로 청산 불가"
    assert restored.forced_close_qty == 40
    # 왕복의 반대 방향도 확인한다 — cycle_to_row 가 두 컬럼을 내보내야
    # save_cycle 이 스키마의 D20 CHECK 를 만족시킬 수 있다.
    assert cycle_to_row(restored)["forced_close_reason"] == "거래정지로 청산 불가"
    assert cycle_to_row(restored)["forced_close_qty"] == 40
    cycle_fields = {f.name for f in dataclasses.fields(Cycle)}
    assert {"forced_close_reason", "forced_close_qty"} <= cycle_fields


def test_row_to_cycle_rejects_forced_without_a_statement():
    """스키마의 D20 CHECK 와 같은 것을 도메인이 말하므로 복원도 거부한다.

    두 층이 같은 불변식을 말하면 어긋날 수 없다. 스키마만 말하면, CHECK 가
    없던 시절에 쓰인 행이나 다른 경로로 들어온 행이 조용히 통과한다.
    """
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=10_000, ladder=a_ladder(), started_at=T0)
    ) | {
        "id": 3, "status": "CLOSED", "close_reason": "FORCED",
        "closed_at": "2026-09-01T15:30:00+00:00",
    }
    with pytest.raises(CorruptRowError) as exc:
        row_to_cycle(row)
    assert "FORCED" in str(exc.value)


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


# ── 감싸지 않는 것의 절반 ──────────────────────────────────────────────
#
# 위 테스트들은 전부 "이건 CorruptRowError 로 감싸지는가" 만 묻는다. 그것만으로는
# 부족하다 — CorruptRowError 는 DomainInvariantError 의 하위이고 그것은
# ValueError 의 하위이므로, 감싸는 범위가 넓어져도(예: TypeError 까지 잡아버려도)
# 이 assert 들은 여전히 통과한다. 호출자 버그(TypeError)가 감싸이지 않고 그대로
# 올라오는지를 TypeError 를 이름으로 못박아 확인해야 그 범위가 넓어지는 회귀를
# 잡는다.


def test_json_to_ladder_does_not_wrap_a_caller_type_error():
    """정수를 넘기면 json.loads 가 TypeError 를 낸다 — 그건 호출자 버그다."""
    with pytest.raises(TypeError):
        json_to_ladder(42)  # type: ignore[arg-type]


def test_row_to_config_does_not_wrap_a_caller_type_error():
    """비율 컬럼에 str 이 아닌 값(float)을 넣으면 text_to_ratio 가 TypeError 를 낸다."""
    row = config_to_row(a_config()) | {"id": 1, "drop_pct": 0.05}
    with pytest.raises(TypeError):
        row_to_config(row)


def test_row_to_cycle_does_not_wrap_a_caller_type_error():
    """시각 컬럼에 str 이 아닌 값을 넣으면 text_to_dt 가 TypeError 를 낸다."""
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE, started_at=T0)
    ) | {"id": 1, "started_at": 12345}
    with pytest.raises(TypeError):
        row_to_cycle(row)
