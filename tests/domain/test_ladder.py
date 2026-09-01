from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.ladder import Ladder, LadderConfigError, target_price

FIVE = Decimal("0.05")


def make_ladder(**over) -> Ladder:
    kwargs = dict(
        anchor_price=9_340,
        drop_pct=FIVE,
        target_pct=FIVE,
        max_stages=7,
        amount_per_stage=1_000_000,
    )
    kwargs.update(over)
    return Ladder(**kwargs)  # type: ignore[arg-type]


# 설계서 3.1절 예시 표를 그대로 고정한다.
# (단계, 발동가, 수량, 투입금액, 누적투입)
SPEC_TABLE = [
    (1, 9_340, 107, 999_380, 999_380),
    (2, 8_870, 112, 993_440, 1_992_820),
    (3, 8_400, 119, 999_600, 2_992_420),
    (4, 7_930, 126, 999_180, 3_991_600),
    (5, 7_470, 133, 993_510, 4_985_110),
    (6, 7_000, 142, 994_000, 5_979_110),
    (7, 6_530, 153, 999_090, 6_978_200),
]


@pytest.mark.parametrize(("stage", "trigger", "qty", "invest", "cum"), SPEC_TABLE)
def test_matches_spec_table(stage: int, trigger: int, qty: int, invest: int, cum: int):
    ladder = make_ladder()
    assert ladder.trigger_price(stage) == trigger
    assert ladder.planned_qty(stage) == qty
    assert ladder.planned_investment(stage) == invest


def test_total_planned_investment_matches_spec():
    assert make_ladder().total_planned_investment() == 6_978_200


def test_total_quantity_matches_spec():
    ladder = make_ladder()
    assert sum(ladder.planned_qty(s) for s in range(1, 8)) == 892


def test_stage_one_trigger_equals_anchor_when_tick_aligned():
    assert make_ladder(anchor_price=10_000).trigger_price(1) == 10_000


def test_trigger_price_is_monotonically_decreasing():
    ladder = make_ladder()
    prices = [ladder.trigger_price(s) for s in range(1, 8)]
    assert prices == sorted(prices, reverse=True)


def test_stage_out_of_range():
    ladder = make_ladder()
    with pytest.raises(ValueError):
        ladder.trigger_price(0)
    with pytest.raises(ValueError):
        ladder.trigger_price(8)


def test_rejects_stage_count_out_of_range():
    with pytest.raises(LadderConfigError):
        make_ladder(max_stages=1)
    with pytest.raises(LadderConfigError):
        make_ladder(max_stages=8)


def test_rejects_when_first_stage_cannot_buy_one_share():
    """설계서 3.1절: 1주도 살 수 없는 설정은 등록 시점에 거부한다."""
    with pytest.raises(LadderConfigError, match="1주도 매수 불가"):
        make_ladder(anchor_price=161_200, amount_per_stage=100_000)


def test_rejects_drop_pct_that_drives_price_nonpositive():
    with pytest.raises(LadderConfigError):
        make_ladder(drop_pct=Decimal("0.20"), max_stages=7)


@pytest.mark.parametrize(
    ("drop", "stages"), [(Decimal("0"), 7), (Decimal("1"), 7), (Decimal("-0.05"), 7)]
)
def test_rejects_invalid_drop_pct(drop: Decimal, stages: int):
    with pytest.raises(LadderConfigError):
        make_ladder(drop_pct=drop, max_stages=stages)


def test_rejects_nonpositive_amounts():
    with pytest.raises(LadderConfigError):
        make_ladder(amount_per_stage=0)
    with pytest.raises(LadderConfigError):
        make_ladder(anchor_price=0)


@pytest.mark.parametrize(
    ("fill", "expected"),
    [
        (10_000, 10_500),   # 설계서 14.1절 1단계
        (9_480, 9_960),     # 2단계 (9,954 → 올림)
        (8_950, 9_400),     # 3단계 (9,397.5 → 올림)
        (8_400, 8_820),     # 설계서 규칙2 갭하락 예시
    ],
)
def test_target_price_uses_fill_price_and_ceils(fill: int, expected: int):
    """목표가는 발동가가 아니라 실제 체결가 기준 — 설계서 3.1절."""
    assert target_price(fill, FIVE) == expected


def test_target_price_rejects_nonpositive():
    with pytest.raises(ValueError):
        target_price(0, FIVE)


def test_ladder_is_frozen():
    import dataclasses

    ladder = make_ladder()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ladder.anchor_price = 1  # type: ignore[misc]


def test_rejects_last_stage_raw_price_below_one_won_repro1():
    """첫 번째 재현: anchor 3, drop 0.4, max_stages 3.
    Last stage raw: 3*(1-0.4*2)=0.6 → tick_unit(0) 실패."""
    with pytest.raises(LadderConfigError):
        make_ladder(anchor_price=3, drop_pct=Decimal("0.4"), max_stages=3, amount_per_stage=3)


def test_rejects_last_stage_raw_price_below_one_won_repro2():
    """두 번째 재현: anchor 10, drop 0.16, max_stages 7.
    Last stage raw: 10*(1-0.16*6)=0.4 → tick_unit(0) 실패."""
    with pytest.raises(LadderConfigError):
        make_ladder(
            anchor_price=10,
            drop_pct=Decimal("0.16"),
            max_stages=7,
            amount_per_stage=1_000_000,
        )


def test_rejects_realistic_near_limit_case():
    """현실적 경계값 근처: anchor 1000, drop 0.1666, max_stages 7.
    Last stage raw: 1000*(1-0.1666*6)=0.004 → tick_unit(0) 실패."""
    with pytest.raises(LadderConfigError):
        make_ladder(
            anchor_price=1_000,
            drop_pct=Decimal("0.1666"),
            max_stages=7,
            amount_per_stage=1_000_000,
        )


def test_accepts_last_stage_raw_price_exactly_one_won():
    """마지막 단계 원가가 정확히 1원인 경우 구성 성공해야 한다.
    anchor 10, drop 0.15, max_stages 7 → last raw: 10*(1-0.15*6)=1.0"""
    ladder = make_ladder(
        anchor_price=10,
        drop_pct=Decimal("0.15"),
        max_stages=7,
        amount_per_stage=1_000_000,
    )
    # Verify it constructs and trigger_price(7) works
    assert ladder.trigger_price(7) == 1


def test_rejects_total_drop_exactly_one_boundary():
    """drop_pct == 1 / (max_stages - 1) 경계값: total_drop >= 1 검사 대상.
    drop 0.25, max_stages 5 → total_drop = 0.25 * 4 = 1.0."""
    with pytest.raises(LadderConfigError):
        make_ladder(drop_pct=Decimal("0.25"), max_stages=5)
