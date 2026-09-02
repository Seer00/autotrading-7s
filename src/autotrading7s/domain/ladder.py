"""사다리 계산 — 설계서 3.1절.

D3에 따라 매수 트리거 기준점은 1단계 체결가 대비 누적이다. 앵커가 확정되면
사다리 전체가 사전에 결정되므로 자금계획과 총한도를 미리 계산할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.tick_size import normalize_tick
from autotrading7s.domain.types import Side

MIN_STAGES = 2
MAX_STAGES = 7


class LadderConfigError(DomainInvariantError):
    """사다리 설정이 실행 불가능할 때. 설정 등록 시점에 던진다.

    `DomainInvariantError` 를 상속하므로 매핑 계층이 복원된 `ladder_json` 의
    정합성 실패를 다른 도메인 불변식 실패와 같은 방식으로 잡을 수 있다.
    """


def _require_int(name: str, value: object) -> None:
    """금액·개수 필드는 원 단위 ``int`` 다 (설계서 3.1절).

    ``bool`` 은 ``int`` 의 하위 클래스이므로 명시적으로 거절한다.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, not {type(value).__name__}")


def _require_ratio(name: str, value: object) -> None:
    """비율 필드는 ``Decimal`` 이다 (설계서 3.1절).

    ``float`` 은 이 제약이 막으려던 바로 그 오차원이고, ``int`` 도 거절한다 —
    "금액은 int, 비율은 Decimal" 이 흐려지면 두 종류가 조용히 섞인다.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, not {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class Ladder:
    """사이클 시작 시 1회 계산되어 사이클 종료까지 불변인 매수 계획."""

    anchor_price: int
    drop_pct: Decimal
    target_pct: Decimal
    max_stages: int
    amount_per_stage: int

    def __post_init__(self) -> None:
        # 타입 검사가 값 검사보다 먼저다. float 은 모든 크기 비교를 통과하므로
        # 순서가 뒤바뀌면 float 금액이 비교를 지나 planned_qty → 주문 수량 →
        # 총한도 산술까지 흘러간다. Plan 4 의 사다리 미리보기 대화상자가
        # 사용자 입력으로 이 타입을 만들기 때문에 여기는 LimitOrderRequest 와
        # 같은 성격의 외부 경계다. 타입 오류는 TypeError 로 낸다 —
        # LadderConfigError 는 ValueError 하위라서, 값 오류를 잡으려는 호출자가
        # 타입 오류를 함께 삼키면 안 된다.
        _require_int("anchor_price", self.anchor_price)
        _require_int("amount_per_stage", self.amount_per_stage)
        _require_int("max_stages", self.max_stages)
        _require_ratio("drop_pct", self.drop_pct)
        _require_ratio("target_pct", self.target_pct)

        if not MIN_STAGES <= self.max_stages <= MAX_STAGES:
            raise LadderConfigError(
                f"max_stages must be {MIN_STAGES}~{MAX_STAGES}: {self.max_stages}"
            )
        if self.anchor_price <= 0:
            raise LadderConfigError(f"anchor_price must be positive: {self.anchor_price}")
        if self.amount_per_stage <= 0:
            raise LadderConfigError(
                f"amount_per_stage must be positive: {self.amount_per_stage}"
            )
        if not Decimal(0) < self.drop_pct < Decimal(1):
            raise LadderConfigError(f"drop_pct must be in (0, 1): {self.drop_pct}")
        if self.target_pct <= 0:
            raise LadderConfigError(f"target_pct must be positive: {self.target_pct}")

        total_drop = self.drop_pct * (self.max_stages - 1)
        if total_drop >= Decimal(1):
            raise LadderConfigError(
                f"drop_pct {self.drop_pct} × {self.max_stages - 1}단계 = {total_drop} "
                "→ 마지막 단계 발동가가 0 이하가 된다"
            )

        # 마지막 단계의 원가(정규화 전)가 1원 이상이어야 한다. 발동가는 단계가
        # 올라갈수록 낮아지므로 마지막 단계만 검사하면 충분하다. 원가 ≥ 1은
        # tick_unit(int(원가))이 최소 1원 호가(2,000원 미만)에서 유효하므로
        # floor 연산이 0을 생산하지 않음을 보장한다.
        last_raw = Decimal(self.anchor_price) * (Decimal(1) - self.drop_pct * (self.max_stages - 1))
        if last_raw < Decimal(1):
            raise LadderConfigError(
                f"last stage raw trigger price below 1 won: {last_raw} "
                f"(anchor {self.anchor_price} × (1 - {self.drop_pct} × {self.max_stages - 1}))"
            )

        # 발동가는 단계가 올라갈수록 낮아지므로 1단계에서 1주를 살 수 있으면
        # 모든 단계에서 살 수 있다. 1단계만 검사하면 충분하다.
        first_price = self.trigger_price(1)
        if self.amount_per_stage // first_price == 0:
            raise LadderConfigError(
                f"1단계 발동가 {first_price:,}원 > 단계금액 "
                f"{self.amount_per_stage:,}원 — 1주도 매수 불가"
            )

    def trigger_price(self, stage: int) -> int:
        """D3: anchor × (1 - drop×(n-1)). 호가 단위 내림."""
        self._check_stage(stage)
        raw = Decimal(self.anchor_price) * (Decimal(1) - self.drop_pct * (stage - 1))
        return normalize_tick(raw, Side.BUY)

    def planned_qty(self, stage: int) -> int:
        """D5 균등 금액 배분: floor(단계금액 / 발동가)."""
        return self.amount_per_stage // self.trigger_price(stage)

    def planned_investment(self, stage: int) -> int:
        return self.planned_qty(stage) * self.trigger_price(stage)

    def total_planned_investment(self) -> int:
        """계획 기준 총투입. 실제 한도 검사는 실체결금액으로 한다(설계서 6절)."""
        return sum(self.planned_investment(s) for s in range(1, self.max_stages + 1))

    def _check_stage(self, stage: int) -> None:
        if not 1 <= stage <= self.max_stages:
            raise ValueError(f"stage out of range 1~{self.max_stages}: {stage}")


def target_price(fill_price: int, target_pct: Decimal) -> int:
    """목표 매도가.

    발동가가 아니라 **실제 체결가** 기준이다(설계서 3.1절). 갭하락으로 여러
    단계가 같은 가격에 채워지면 발동가는 서로 달라도 목표가는 같아진다.
    이 때문에 목표가 계산은 ``Ladder`` 의 메서드가 아니라 별도 함수다.

    호가 단위 올림 — 내림하면 목표수익률에 미달한 채로 팔린다.
    """
    _require_int("fill_price", fill_price)
    _require_ratio("target_pct", target_pct)
    if fill_price <= 0:
        raise ValueError(f"fill_price must be positive: {fill_price}")
    raw = Decimal(fill_price) * (Decimal(1) + target_pct)
    return normalize_tick(raw, Side.SELL)
