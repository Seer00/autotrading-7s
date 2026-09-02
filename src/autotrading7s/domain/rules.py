"""트리거 판정 — 설계서 5절.

부작용이 없는 순수 함수다. 같은 입력에는 항상 같은 출력을 낸다. 네트워크·DB·
시계 없이 "이 틱이 왔을 때 무슨 일이 벌어져야 하나"를 밀리초 단위로 수천 케이스
검증할 수 있게 하려는 설계다.

이 모듈에는 **하락 조건 매도 분기가 존재하지 않는다.** 자동 손절매 배제
원칙(설계서 6절)을 코드 구조로 강제한 것이며, 누군가 손절 기능을 추가하려 하면
그것이 명확한 설계 변경으로 드러난다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import StageStatus, Tick


@dataclass(frozen=True, slots=True)
class TriggerParams:
    """판정에 필요한 설정값. split_config 에서 추출한 뷰."""

    target_pct: Decimal
    allow_rebuy: bool = True
    rebuy_cooldown_sec: int = 60

    def __post_init__(self) -> None:
        if self.target_pct <= 0:
            raise ValueError(f"target_pct must be positive: {self.target_pct}")
        if self.rebuy_cooldown_sec < 0:
            raise ValueError(f"rebuy_cooldown_sec must be non-negative: {self.rebuy_cooldown_sec}")


@dataclass(frozen=True, slots=True)
class BuyStage:
    stage_no: int
    limit_price: int
    qty: int
    reason: str


@dataclass(frozen=True, slots=True)
class SellStage:
    stage_no: int
    limit_price: int
    qty: int
    reason: str


Decision = BuyStage | SellStage


def decide(
    *,
    tick: Tick,
    cycle: Cycle,
    states: Sequence[StageState],
    params: TriggerParams,
    now: datetime,
    market_open: bool,
    stock_code: str,
) -> list[Decision]:
    """이 틱에 집행할 결정 목록. 부작용 없음."""
    # 프로그래밍 오류와 데이터 오류는 조용히 무시할 수 없다.
    # 이 검사들(1–2, 5)은 시장시간과 무관하게 진행한다.
    if tick.code != stock_code:
        raise ValueError(f"Tick code mismatch: tick has {tick.code!r}, "
                        f"but stock_code is {stock_code!r}")

    # 검사 3: 상태 목록의 중복 stage_no 감지
    by_no = {s.stage_no: s for s in states}
    if len(by_no) != len(states):
        seen: set[int] = set()
        for s in states:
            if s.stage_no in seen:
                raise ValueError(f"Duplicate stage_no in states: {s.stage_no}")
            seen.add(s.stage_no)

    # 규칙 4 — 장 운영시간 밖에서는 어떤 결정도 내리지 않는다.
    if not market_open:
        return []
    # RUNNING 이 아니면 판정하지 않는다. STARTING 은 앵커가 없어 사다리를
    # 계산할 수 없고, PAUSED·LIQUIDATING 은 자동 트리거가 정지된 상태다.
    if not cycle.accepts_triggers or cycle.ladder is None:
        return []

    # 검사 6: 사다리와 매개변수의 target_pct 일치 확인
    if cycle.ladder.target_pct != params.target_pct:
        raise ValueError(f"target_pct mismatch: ladder has "
                        f"{cycle.ladder.target_pct}, "
                        f"params has {params.target_pct}")

    buy = _eval_buy(tick, cycle.ladder, states, params, now)
    return [buy] if buy is not None else []


def _eval_buy(
    tick: Tick,
    ladder: Ladder,
    states: Sequence[StageState],
    params: TriggerParams,
    now: datetime,
) -> BuyStage | None:
    """규칙 2 — 조건을 만족하는 대기 단계 중 번호가 가장 낮은 하나만."""
    by_no = {s.stage_no: s for s in states}
    for stage_no in range(1, ladder.max_stages + 1):
        state = by_no.get(stage_no)
        # 규칙 5 — WAITING 이 아닌 단계는 판정 대상이 아니다. PENDING 을
        # 제외하는 것이 중복 주문을 막는 방어선이다.
        if state is None or state.status is not StageStatus.WAITING:
            continue
        if tick.price > state.trigger_price:
            continue
        qty = ladder.planned_qty(stage_no)
        if qty <= 0:
            continue
        return BuyStage(
            stage_no=stage_no,
            # 지정가는 관측된 현재가로 발주한다. 미체결이면 3초 후 취소하고
            # 다음 틱에 재시도한다(설계서 9절).
            limit_price=tick.price,
            qty=qty,
            reason=_buy_reason(stage_no=stage_no, tick=tick,
                               trigger=state.trigger_price, ladder=ladder),
        )
    return None


def _pct(value: Decimal) -> str:
    return f"{(value * 100).normalize()}"


def _buy_reason(*, stage_no: int, tick: Tick, trigger: int, ladder: Ladder) -> str:
    return (
        f"stage={stage_no} BUY | tick={tick.price}({tick.source.value}) "
        f"<= trigger={trigger} | anchor={ladder.anchor_price} "
        f"drop={_pct(ladder.drop_pct)}% stage_gap={stage_no - 1} | rule2_sequential"
    )
