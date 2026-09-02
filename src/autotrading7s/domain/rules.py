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
from autotrading7s.domain.errors import DomainInvariantError
# _require_int·_require_ratio 는 "금액은 int, 비율은 Decimal"(설계서 3.1절)을
# 코드로 옮긴 것이며 ladder.py 에서 처음 필요해 거기에 있다. rules 는 이미
# ladder 에 의존하므로 의존 방향이 새로 생기지 않는다 — 같은 관용구를 여기에
# 다시 쓰면 두 곳의 메시지가 갈라진다.
from autotrading7s.domain.ladder import (
    Ladder,
    _require_int,
    _require_ratio,
    target_price,
)
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import StageStatus, Tick


@dataclass(frozen=True, slots=True)
class TriggerParams:
    """판정에 필요한 설정값. split_config 에서 추출한 뷰."""

    target_pct: Decimal
    allow_rebuy: bool = True
    rebuy_cooldown_sec: int = 60

    def __post_init__(self) -> None:
        # 타입 검사가 값 검사보다 먼저다. target_pct 는 특히 중요하다 —
        # decide() 의 목표율 대조로는 float 을 잡을 수 없기 때문이다. Decimal 은
        # float 과 정확한 값으로 비교되므로 2진수로 정확한 비율(0.25·0.5·
        # 0.125·0.0625 …)은 서로 같다고 나오고, 대조를 통과한 float 이
        # target_price 까지 흘러가 매 틱 TypeError 를 낸다. 손절매가 없는 이
        # 전략에서 그 결과는 "그 종목은 어떤 단계도 팔지 못한다" 이므로,
        # 값이 들어오는 이 경계에서 막아야 한다.
        _require_ratio("target_pct", self.target_pct)
        _require_int("rebuy_cooldown_sec", self.rebuy_cooldown_sec)
        # 재매수 허용은 진리값이 아니라 bool 이어야 한다. _eval_buy 가
        # `if not params.allow_rebuy` 로 읽으므로 "false" 같은 문자열은 참으로
        # 해석되어 사용자가 끈 재매수를 켠다. Plan 2 의 SQLite 는 boolean 을
        # 0/1 로 돌려주므로 그 변환을 저장소 경계에서 하도록 int 도 거절한다.
        if not isinstance(self.allow_rebuy, bool):
            raise TypeError(
                f"allow_rebuy must be bool, not {type(self.allow_rebuy).__name__}"
            )

        if self.target_pct <= 0:
            raise DomainInvariantError(f"target_pct must be positive: {self.target_pct}")
        if self.rebuy_cooldown_sec < 0:
            raise DomainInvariantError(
                f"rebuy_cooldown_sec must be non-negative: {self.rebuy_cooldown_sec}"
            )


def _check_decision_fields(decision: BuyStage | SellStage) -> None:
    """결정 타입의 공통 불변식 — 세 필드 모두 양의 ``int``.

    ``BuyStage`` 와 ``SellStage`` 는 ``guards`` 의 직접 입력이고, guards 는 이
    프로그램의 유일한 구조적 보호장치다. 음수 지정가는 예상 체결금액을 음수로
    만들어 총한도 검사를 통째로 무력화하고, 가격 0 은 국내 증권사 API 에서
    시장가의 전선 표현이다. 설계서 8.2절의 "자동 트리거 경로는 시장가를
    표현할 수 없다" 는 제약이 사슬 끝의 ``LimitOrderRequest`` 뿐 아니라 판정
    경계에서도 성립해야 한다.
    """
    for name in ("stage_no", "limit_price", "qty"):
        value = getattr(decision, name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be int, not {type(value).__name__}")
        if value <= 0:
            raise DomainInvariantError(f"{name} must be positive: {value}")


@dataclass(frozen=True, slots=True)
class BuyStage:
    stage_no: int
    limit_price: int
    qty: int
    reason: str

    def __post_init__(self) -> None:
        _check_decision_fields(self)


@dataclass(frozen=True, slots=True)
class SellStage:
    stage_no: int
    limit_price: int
    qty: int
    reason: str

    def __post_init__(self) -> None:
        _check_decision_fields(self)


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
                raise DomainInvariantError(f"Duplicate stage_no in states: {s.stage_no}")
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
        raise DomainInvariantError(f"target_pct mismatch: ladder has "
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
        # 목록에 없는 단계는 검사 대상이 아니다 — 일부 단계만 담긴 목록은
        # 유효하다.
        if state is None:
            continue
        # 검사 7: 저장된 발동가가 사다리의 계산과 일치하는지 확인한다.
        #
        # 설계서 4.2절은 이 숫자를 두 곳(cycle.ladder_json, stage_state.
        # trigger_price)에 쓴다. 수량은 사다리에서 매번 다시 계산하지만
        # 발동가는 단계 자신의 기록을 쓰므로, Plan 2 가 제약 없는 컬럼에서
        # 복원한 손상된 값이 그대로 "살지 말지"를 결정한다. 앵커보다 높은
        # 발동가는 하락 매수 전략을 거꾸로 돌린다. 조용히 건너뛰면 그 손상이
        # 숨으므로 — decide() 가 중복 stage_no 에 그러듯 — 예외를 던진다.
        expected = ladder.trigger_price(stage_no)
        if state.trigger_price != expected:
            raise DomainInvariantError(
                f"trigger_price mismatch on stage {stage_no}: state has "
                f"{state.trigger_price}, ladder computes {expected}"
            )
        # 규칙 5 — WAITING 이 아닌 단계는 판정 대상이 아니다. PENDING 을
        # 제외하는 것이 중복 주문을 막는 방어선이다.
        if state.status is not StageStatus.WAITING:
            continue
        # 규칙 3 — 재매수 쿨다운. last_sold_at 이 있으면 한 번 팔린 단계다.
        # 쿨다운이 없으면 같은 단계가 수수료를 태우며 분당 수십 번 회전한다.
        if state.last_sold_at is not None:
            if not params.allow_rebuy:
                continue
            _require_aware(now, f"now (stage {stage_no})")
            _require_aware(state.last_sold_at, f"last_sold_at (stage {stage_no})")
            elapsed = (now - state.last_sold_at).total_seconds()
            # elapsed 가 음수(시계 역행 — NTP 보정, 손상된 미래 타임스탬프)면
            # 이 비교는 항상 재매수를 막는다. 이것은 의도된 안전한 방향이다:
            # 도메인이 경과 시간을 알 수 없을 때는 거래하지 않는 것이 맞는
            # 답이고, 설계 문서의 일관된 태도와 맞는다. last_sold_at 이
            # 지속적으로 미래를 가리키는 경우 이 단계는 계속 막히지만, 그
            # 탐지는 Plan 2 의 정합성 검사(reconciliation)가 할 일이며 이
            # 쿨다운 검사의 책임이 아니다.
            if elapsed < params.rebuy_cooldown_sec:
                continue
        if tick.price > state.trigger_price:
            continue
        # 수량은 사다리에서 매번 다시 계산한다. Ladder.__post_init__ 이
        # 1단계에서 1주 이상 살 수 있음을 확인하고 발동가는 단계가 올라갈수록
        # 낮아지므로 planned_qty 는 항상 1 이상이다. 만약 그 불변식이 깨지면
        # BuyStage 가 "qty must be positive" 로 터진다 — 조용히 건너뛰는 것보다
        # 낫다.
        qty = ladder.planned_qty(stage_no)
        return BuyStage(
            stage_no=stage_no,
            # 지정가는 관측된 현재가로 발주한다. 미체결이면 3초 후 취소하고
            # 다음 틱에 재시도한다(설계서 9절).
            limit_price=tick.price,
            qty=qty,
            reason=_buy_reason(stage_no=stage_no, tick=tick,
                               trigger=state.trigger_price, ladder=ladder,
                               state=state),
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
        # 체결가·수량을 다시 확인하지 않는다 — HOLDING 이면
        # StageState.__post_init__ 이 둘 다 양의 int 임을 보장한다. 그 불변식이
        # 깨지면 target_price 가 TypeError 로 터진다. 손절매가 없는 전략에서
        # 매도를 조용히 건너뛰는 분기는 두면 안 된다.
        target = target_price(state.fill_price, params.target_pct)  # type: ignore[arg-type]
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


def _require_aware(dt: datetime, label: str) -> None:
    """`dt` 가 타임존 인식(aware) 인지 확인한다.

    naive datetime 을 aware 와 뺄셈하면 ``TypeError`` 가 터진다. 그 예외는
    틱 루프 안에서 어느 단계·어느 필드가 원인인지 알려주지 않는다. Plan 2 의
    SQLite 저장소가 TEXT 컬럼에서 타임스탬프를 파싱할 때 tzinfo 를 잃어버리기
    쉬우므로, 이 쿨다운 검사가 소비하는 지점에서 미리 검증해 원인을 밝힌다.
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{label} must be timezone-aware, got naive datetime: {dt!r}")


def _pct(value: Decimal) -> str:
    return f"{(value * 100).normalize()}"


def _buy_reason(*, stage_no: int, tick: Tick, trigger: int, ladder: Ladder,
                state: StageState) -> str:
    parts = [
        f"stage={stage_no} BUY",
        f"tick={tick.price}({tick.source.value}) <= trigger={trigger}",
        f"anchor={ladder.anchor_price} drop={_pct(ladder.drop_pct)}% "
        f"stage_gap={stage_no - 1}",
        "rule2_sequential",
    ]
    if state.last_sold_at is not None:
        parts.append(f"rebuy={state.rebuy_count} cooldown_ok")
    return " | ".join(parts)


def _sell_reason(
    *, state: StageState, tick: Tick, target: int, params: TriggerParams
) -> str:
    return (
        f"stage={state.stage_no} SELL | tick={tick.price}({tick.source.value}) "
        f">= target={target} | fill={state.fill_price} "
        f"target_pct={_pct(params.target_pct)}% | rule1_sell_first"
    )
