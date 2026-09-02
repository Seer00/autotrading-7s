"""손익 집계 — 설계서 12.3절·14.1절.

보유현황 표의 평균단가·평가손익은 이 모듈의 순수 함수로 계산한다. UI 파일
안에서 직접 계산하지 않는 이유는 설계서 14.4절에 있다 — GUI 코드는 개발
환경(Linux EC2)에서 자동 테스트가 불가능한 사각지대다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from autotrading7s.domain.stage import StageState

_CENT = Decimal("0.01")


def _held(states: Iterable[StageState]) -> list[StageState]:
    return [s for s in states if s.held_qty > 0 and s.fill_price is not None]


def invested_amount(states: Sequence[StageState]) -> int:
    """보유 중 단계의 실체결금액 합.

    총한도 검사도 이 기준(실체결금액)을 쓴다. 계획금액으로 세면
    floor(금액/가격) 오차 때문에 한도가 실제보다 헐거워진다(설계서 6절).
    """
    return sum(s.fill_price * s.held_qty for s in _held(states))  # type: ignore[operator]


def held_qty(states: Sequence[StageState]) -> int:
    return sum(s.held_qty for s in states)


def holding_stage_count(states: Sequence[StageState]) -> int:
    """보유현황 표의 '단계' 열에 쓰는 진행 단계 수."""
    return len(_held(states))


def avg_price(states: Sequence[StageState]) -> int | None:
    """가중 평균단가. 보유가 없으면 None."""
    qty = held_qty(states)
    if qty == 0:
        return None
    return int(
        (Decimal(invested_amount(states)) / qty).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )


def unrealized_pnl(states: Sequence[StageState], current_price: int) -> int:
    """평가손익 금액. 평단 반올림을 거치지 않고 실체결금액에서 직접 뺀다."""
    return held_qty(states) * current_price - invested_amount(states)


def unrealized_pnl_pct(
    states: Sequence[StageState], current_price: int
) -> Decimal | None:
    """평가손익률(%). 소수 2자리. 보유가 없으면 None."""
    invested = invested_amount(states)
    if invested == 0:
        return None
    ratio = Decimal(unrealized_pnl(states, current_price)) / invested * 100
    return ratio.quantize(_CENT, rounding=ROUND_HALF_UP)
