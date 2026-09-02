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
from autotrading7s.domain.ladder import Ladder, target_price
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

    # 규칙 1 — 매도를 먼저 평가한다. 매도가 하나라도 있으면 이 틱에서는
    # 매도만 집행하고, 매수는 다음 틱에 평가한다. 매도 대금이 들어온 뒤
    # 매수하도록 하려는 것이며, 틱 간격이 1초 미만이라 실질 지연은 없다.
    sells = _eval_sells(tick, states, params)
    if sells:
        return list(sells)

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


def _eval_sells(
    tick: Tick, states: Sequence[StageState], params: TriggerParams
) -> list[SellStage]:
    """목표가에 도달한 보유 단계 전부. 번호가 낮은 순.

    매수와 달리 개수를 제한하지 않는다. 매도는 포지션을 줄이는 방향이므로
    과다 집행 위험이 없고, 반등 구간에서 여러 단계가 동시에 목표에 닿는 것은
    세븐스플릿의 의도된 동작이다.
    """
    out: list[SellStage] = []
    for state in sorted(states, key=lambda s: s.stage_no):
        # 규칙 5 — SELL_PENDING 은 이미 주문이 나갔다.
        if state.status is not StageStatus.HOLDING:
            continue
        if state.fill_price is None or not state.fill_qty:
            continue
        target = target_price(state.fill_price, params.target_pct)
        if tick.price < target:
            continue
        out.append(
            SellStage(
                stage_no=state.stage_no,
                # 목표가로 지정가 발주한다. 지정가 매도는 시장의 최우선
                # 매수호가에 체결되므로, 목표가로 걸어도 현재가가 더 높으면
                # 더 좋은 가격에 체결된다. 목표 보장과 체결 확률을 동시에 얻는다.
                limit_price=target,
                # 매수(_eval_buy)는 qty 를 ladder.planned_qty(n) 로 매번 다시
                # 계산한다 — 사다리는 각 단계가 "얼마를 사려고 하는지"의
                # 계획이라 다시 계산해도 항상 같다. 매도는 다르다: 실제로
                # "얼마를 들고 있는지"는 사다리가 알 수 없고 단계 자신의
                # 기록만이 안다. cancel_sell 이 부분체결 후 취소 시 남은
                # 수량으로 fill_qty 를 갱신해 주므로, 여기서 state.fill_qty
                # 를 그대로 신뢰하는 것이 맞다.
                qty=state.fill_qty,
                reason=_sell_reason(state=state, tick=tick, target=target,
                                    params=params),
            )
        )
    return out


def _pct(value: Decimal) -> str:
    return f"{(value * 100).normalize()}"


def _buy_reason(*, stage_no: int, tick: Tick, trigger: int, ladder: Ladder) -> str:
    return (
        f"stage={stage_no} BUY | tick={tick.price}({tick.source.value}) "
        f"<= trigger={trigger} | anchor={ladder.anchor_price} "
        f"drop={_pct(ladder.drop_pct)}% stage_gap={stage_no - 1} | rule2_sequential"
    )


def _sell_reason(
    *, state: StageState, tick: Tick, target: int, params: TriggerParams
) -> str:
    return (
        f"stage={state.stage_no} SELL | tick={tick.price}({tick.source.value}) "
        f">= target={target} | fill={state.fill_price} "
        f"target_pct={_pct(params.target_pct)}% | rule1_sell_first"
    )
