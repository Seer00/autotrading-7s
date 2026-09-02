from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from autotrading7s.domain.pnl import (
    avg_price,
    held_qty,
    holding_stage_count,
    invested_amount,
    unrealized_pnl,
    unrealized_pnl_pct,
)
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import StageStatus

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def held(stage_no: int, fill_price: int, fill_qty: int,
         status: StageStatus = StageStatus.HOLDING) -> StageState:
    return StageState(stage_no=stage_no, status=status, trigger_price=fill_price,
                      planned_qty=fill_qty, fill_price=fill_price,
                      fill_qty=fill_qty, bought_at=T0)


def waiting(stage_no: int) -> StageState:
    return StageState(stage_no=stage_no, status=StageStatus.WAITING,
                      trigger_price=8_000, planned_qty=125)


# 설계서 14.1절 보유현황 목업 — 삼성전자
SAMSUNG = [held(1, 10_000, 100), held(2, 9_480, 105), held(3, 8_950, 111)]
# 설계서 14.1절 보유현황 목업 — 카카오 (7단계 전부 보유)
KAKAO = [
    held(1, 10_000, 100), held(2, 9_500, 105), held(3, 9_000, 111),
    held(4, 8_500, 117), held(5, 8_000, 125), held(6, 7_500, 133),
    held(7, 7_000, 142),
]


def test_samsung_mockup_numbers():
    states = SAMSUNG + [waiting(4)]
    assert invested_amount(states) == 2_988_850
    assert held_qty(states) == 316
    assert avg_price(states) == 9_458
    assert holding_stage_count(states) == 3
    assert unrealized_pnl(states, 9_340) == -37_410
    assert unrealized_pnl_pct(states, 9_340) == Decimal("-1.25")


def test_kakao_mockup_numbers():
    assert invested_amount(KAKAO) == 6_982_500
    assert held_qty(KAKAO) == 833
    assert avg_price(KAKAO) == 8_382
    assert holding_stage_count(KAKAO) == 7
    assert unrealized_pnl(KAKAO, 7_910) == -393_470
    assert unrealized_pnl_pct(KAKAO, 7_910) == Decimal("-5.64")


def test_mockup_totals_add_up():
    """종목별 손익의 합이 설계서 목업의 합계와 일치해야 한다."""
    total = unrealized_pnl(SAMSUNG, 9_340) + unrealized_pnl(KAKAO, 7_910)
    assert total == -430_880


def test_sell_pending_counts_as_held():
    states = [held(1, 10_000, 100, status=StageStatus.SELL_PENDING)]
    assert held_qty(states) == 100
    assert invested_amount(states) == 1_000_000


def test_buy_pending_does_not_count():
    """PENDING 매수는 아직 보유가 아니다."""
    states = [
        StageState(stage_no=1, status=StageStatus.BUY_PENDING, trigger_price=10_000,
                   planned_qty=100)
    ]
    assert held_qty(states) == 0
    assert invested_amount(states) == 0


def test_empty_holdings():
    states = [waiting(1), waiting(2)]
    assert invested_amount(states) == 0
    assert held_qty(states) == 0
    assert avg_price(states) is None
    assert unrealized_pnl(states, 9_340) == 0
    assert unrealized_pnl_pct(states, 9_340) is None
    assert holding_stage_count(states) == 0


def test_profit_case():
    states = [held(1, 10_000, 100)]
    assert unrealized_pnl(states, 10_500) == 50_000
    assert unrealized_pnl_pct(states, 10_500) == Decimal("5.00")
