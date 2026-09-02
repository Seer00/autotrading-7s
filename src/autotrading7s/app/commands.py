"""GUI → 엔진 명령 — 설계서 7.1절.

GUI 와 엔진은 메시지로만 대화한다. 이 경계 덕분에 향후 프로세스 분리는 큐를
소켓으로 교체하는 작업으로 축소된다.

`PriorityCommand` 가 이 모듈의 핵심이다. 설계서 7.1절은 `priority_q` 가 긴급
기능의 즉시성을 **구조적으로** 보장한다고 규정하는데, 어떤 명령이 그 큐에 들어갈
자격이 있는지가 주석에만 있으면 구조가 아니다. 타입으로 표현하면 오케스트레이터가
`isinstance` 로 단정할 수 있고, 새 명령을 추가하는 사람이 우선순위를 의식적으로
선택하게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_EMERGENCY_SCOPES = frozenset({"SINGLE", "ALL"})
FORCE_CLOSE_CONFIRMATION = "강제종료"
LIQUIDATE_ALL_CONFIRMATION = "전체청산"


class Command:
    """모든 명령의 기반. 명령 소비 태스크가 하나의 타입으로 다룬다."""


class PriorityCommand(Command):
    """`priority_q` 에 들어갈 자격이 있는 명령 — 긴급청산 계열뿐이다."""


@dataclass(frozen=True, slots=True)
class StartCycle(Command):
    """사이클 시작. 앵커 가격은 엔진이 첫 틱에서 확정한다."""

    config_id: int


@dataclass(frozen=True, slots=True)
class PauseCycle(Command):
    config_id: int


@dataclass(frozen=True, slots=True)
class ResumeCycle(Command):
    config_id: int


@dataclass(frozen=True, slots=True)
class StopCycle(Command):
    """자동 트리거 정지. 사이클 종료는 보유 0 도달로만 일어난다 (D5)."""

    config_id: int


@dataclass(frozen=True, slots=True)
class ResetReconcileBaseline(Command):
    """강제 종료된 수량의 대사 기준선을 초기화한다 (설계서 11.4절)."""

    stock_code: str


@dataclass(frozen=True, slots=True)
class Shutdown(Command):
    """엔진 정상 종료."""


@dataclass(frozen=True, slots=True)
class EmergencyLiquidate(PriorityCommand):
    """긴급청산 — 설계서 11절.

    `scope="ALL"` 은 종목을 지정하지 않고 전체를 순차 청산하며, 설계서 11.2절에
    따라 `전체청산` 텍스트 입력을 요구한다.
    """

    scope: str
    config_id: int | None
    reason: str | None
    confirmed_text: str | None

    def __post_init__(self) -> None:
        if self.scope not in _EMERGENCY_SCOPES:
            raise ValueError(
                f"scope must be one of {sorted(_EMERGENCY_SCOPES)}: {self.scope!r}"
            )
        if self.scope == "SINGLE" and self.config_id is None:
            raise ValueError("config_id is required when scope is SINGLE")
        if self.scope == "ALL":
            if self.config_id is not None:
                raise ValueError("config_id must be None when scope is ALL")
            if self.confirmed_text != LIQUIDATE_ALL_CONFIRMATION:
                raise ValueError(
                    f"scope=ALL requires confirmed_text == "
                    f"{LIQUIDATE_ALL_CONFIRMATION!r} (설계서 11.2절)"
                )


@dataclass(frozen=True, slots=True)
class ForceClose(PriorityCommand):
    """D20 강제 종료 — 설계서 11.4절.

    `reason` 이 필수인 것은 `MarketSellRequest.reason` 과 같은 발상이다. 타입이
    강제하면 증언 기록을 빼먹을 수 없다.
    """

    config_id: int
    reason: str
    confirmed_text: str

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise ValueError("reason must be a non-empty statement (설계서 11.4절)")
        if self.confirmed_text != FORCE_CLOSE_CONFIRMATION:
            raise ValueError(
                f"confirmed_text must be {FORCE_CLOSE_CONFIRMATION!r} (설계서 11.4절)"
            )


@dataclass(frozen=True, slots=True)
class SaveConfig(Command):
    """분할 설정 등록·수정 — 설계서 14.2절.

    `config_id` 가 `None` 이면 신규, 정수면 수정이다. **수정은 `IDLE` 설정만
    가능하다** — `ACTIVE` 설정의 값을 바꾸면 진행 중인 사이클의 사다리
    (`cycle.ladder_json` 에 고정)와 어긋나고 `load_stages` 의 H4 가 그 사이클을
    로드 불가로 만든다.

    값은 이미 타입이 맞아야 한다. 문자열 → `Decimal` 파싱은 뷰모델의 몫이며
    (`ui/view_model.parse_config_form`), 그래야 파싱 실패가 입력 위젯 옆에
    보인다 — 엔진 스레드에서 일어나면 그 메시지는 로그에만 남는다.
    """

    config_id: int | None
    stock_code: str
    stock_name: str | None
    label: str | None
    max_stages: int
    drop_pct: Decimal
    target_pct: Decimal
    amount_per_stage: int
    allow_rebuy: bool
    rebuy_cooldown_sec: int
    total_limit: int

    def __post_init__(self) -> None:
        for name in ("drop_pct", "target_pct"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(
                    f"{name} must be Decimal, not {type(value).__name__} — "
                    f"문자열 파싱은 뷰모델의 몫이다"
                )
        for name in ("max_stages", "amount_per_stage", "rebuy_cooldown_sec",
                     "total_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(
                    f"{name} must be int, not {type(value).__name__}"
                )


PRIORITY_COMMANDS: frozenset[type[Command]] = frozenset(
    {EmergencyLiquidate, ForceClose}
)
