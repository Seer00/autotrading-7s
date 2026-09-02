from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import (
    CorruptRowError,
    row_to_stage,
    rows_to_stages,
    stage_to_row,
)
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import StageStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def waiting(lad: Ladder, n: int) -> StageState:
    return StageState(stage_no=n, status=StageStatus.WAITING,
                      trigger_price=lad.trigger_price(n),
                      planned_qty=lad.planned_qty(n))


def holding(lad: Ladder, n: int, fill: int, qty: int) -> StageState:
    return StageState(stage_no=n, status=StageStatus.HOLDING,
                      trigger_price=lad.trigger_price(n),
                      planned_qty=lad.planned_qty(n),
                      fill_price=fill, fill_qty=qty, bought_at=T0)


def complete_rows(lad: Ladder, *, id_base: int = 1) -> list[dict]:
    rows = []
    for n in range(1, lad.max_stages + 1):
        rows.append(stage_to_row(1, waiting(lad, n)) | {"id": id_base + n - 1})
    return rows


def test_stage_round_trip_waiting():
    lad = a_ladder()
    original = waiting(lad, 3)
    restored = row_to_stage(stage_to_row(1, original) | {"id": 3})
    assert restored == original


def test_stage_round_trip_holding_with_timestamps():
    lad = a_ladder()
    original = holding(lad, 3, fill=8_950, qty=111)
    restored = row_to_stage(stage_to_row(1, original) | {"id": 3})
    assert restored == original
    assert restored.bought_at == T0
    assert restored.bought_at.tzinfo is not None


def test_stage_round_trip_after_rebuy():
    """last_sold_at 과 rebuy_count 가 왕복해야 쿨다운이 복원 후에도 동작한다."""
    lad = a_ladder()
    original = StageState(stage_no=2, status=StageStatus.WAITING,
                          trigger_price=lad.trigger_price(2),
                          planned_qty=lad.planned_qty(2),
                          last_sold_at=T0, rebuy_count=3)
    restored = row_to_stage(stage_to_row(1, original) | {"id": 2})
    assert restored == original
    assert restored.last_sold_at == T0 and restored.rebuy_count == 3


def test_row_to_stage_wraps_a_corrupt_row():
    lad = a_ladder()
    row = stage_to_row(1, waiting(lad, 3)) | {"id": 9, "trigger_price": -500}
    with pytest.raises(CorruptRowError) as exc:
        row_to_stage(row)
    assert "stage_state" in str(exc.value) and "9" in str(exc.value)


def test_row_to_stage_refuses_a_naive_timestamp():
    lad = a_ladder()
    row = stage_to_row(1, holding(lad, 3, 8_950, 111)) | {
        "id": 3, "bought_at": "2026-09-01T09:00:00"
    }
    with pytest.raises(CorruptRowError):
        row_to_stage(row)


# ── H3: 완전한 단계 집합 ──────────────────────────────────────────────────

def test_complete_set_is_accepted():
    lad = a_ladder()
    stages = rows_to_stages(complete_rows(lad), cycle_id=1, ladder=lad)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]


def test_stages_are_returned_in_stage_order():
    """DB 가 ORDER BY 없이 주더라도 매핑이 순서를 보장해야 한다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    shuffled = [rows[4], rows[0], rows[6], rows[2], rows[1], rows[5], rows[3]]
    stages = rows_to_stages(shuffled, cycle_id=1, ladder=lad)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]


def test_a_missing_stage_row_is_refused():
    """H3. decide() 는 없는 단계를 조용히 건너뛴다 — 리포지토리가 막는다."""
    lad = a_ladder()
    rows = [r for r in complete_rows(lad) if r["stage_no"] != 4]
    with pytest.raises(CorruptRowError, match="incomplete"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_the_error_names_the_missing_stage():
    lad = a_ladder()
    rows = [r for r in complete_rows(lad) if r["stage_no"] != 4]
    with pytest.raises(CorruptRowError) as exc:
        rows_to_stages(rows, cycle_id=1, ladder=lad)
    assert "4" in str(exc.value)


def test_a_duplicate_stage_row_is_refused():
    lad = a_ladder()
    rows = complete_rows(lad)
    rows.append(stage_to_row(1, waiting(lad, 3)) | {"id": 99})
    with pytest.raises(CorruptRowError, match="duplicate"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_an_out_of_range_stage_row_is_refused():
    """max_stages=7 인 사다리에 8단계 행이 있으면 손상이다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows.append(
        {"id": 99, "cycle_id": 1, "stage_no": 8, "status": "WAITING",
         "trigger_price": 6_000, "planned_qty": 166, "fill_price": None,
         "fill_qty": None, "bought_at": None, "last_sold_at": None,
         "rebuy_count": 0}
    )
    with pytest.raises(CorruptRowError):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_an_empty_row_list_is_refused():
    lad = a_ladder()
    with pytest.raises(CorruptRowError, match="incomplete"):
        rows_to_stages([], cycle_id=1, ladder=lad)


# ── H4: trigger_price 대조 ────────────────────────────────────────────────

def test_a_trigger_price_mismatch_is_refused():
    """H4. Plan 1 의 최종 리뷰가 재현한 것: trigger_price=999_999 인 행이
    앵커보다 높은 가격의 매수를 만든다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": 999_999}
    with pytest.raises(CorruptRowError, match="trigger_price"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_the_mismatch_error_names_both_values():
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": 999_999}
    with pytest.raises(CorruptRowError) as exc:
        rows_to_stages(rows, cycle_id=1, ladder=lad)
    message = str(exc.value)
    assert "999999" in message.replace(",", "")
    assert str(lad.trigger_price(2)) in message.replace(",", "")


def test_a_one_won_mismatch_is_still_refused():
    """호가 정규화 때문에 1원 차이가 우연히 나올 수 있다 — 그래도 손상이다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": lad.trigger_price(2) + 1}
    with pytest.raises(CorruptRowError, match="trigger_price"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_h4_is_skipped_when_the_cycle_has_no_ladder():
    """STARTING 사이클은 앵커가 없어 사다리도 없다 — H3 만 검사한다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": 999_999}
    stages = rows_to_stages(rows, cycle_id=1, ladder=None)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]
    assert stages[1].trigger_price == 999_999


def test_h3_still_applies_when_there_is_no_ladder():
    """사다리가 없어도 완전성은 검사한다 — 단, 기대 개수를 알 수 없으므로
    연속성과 중복만 본다."""
    lad = a_ladder()
    rows = [r for r in complete_rows(lad) if r["stage_no"] != 4]
    with pytest.raises(CorruptRowError, match="incomplete"):
        rows_to_stages(rows, cycle_id=1, ladder=None)
