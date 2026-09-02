"""엔진 → GUI 이벤트 — 설계서 7.1절.

GUI 는 DB 를 건드리지 않고 이 이벤트만 소비한다(설계서 14.4절). 그래서 화면에
필요한 것이 전부 이벤트에 실려 있어야 하며, 모든 이벤트는 tz-aware 시각을
가진다 — naive 가 새면 화면의 시각 표시가 조용히 틀린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from autotrading7s.domain.types import CloseReason, TickSource

EMERGENCY_RESULTS: frozenset[str] = frozenset(
    {"SUCCESS", "PARTIAL", "FAILED", "REJECTED_CLOSED_MARKET", "FORCED_CLOSE"}
)
RECONCILE_VERDICTS: frozenset[str] = frozenset(
    {"MATCH", "INTERNAL_LESS", "INTERNAL_MORE"}
)


def _require_aware(at: datetime) -> None:
    """같은 패키지의 `snapshot.py` 도 이것을 쓴다 — 같은 규칙을 두 번 쓰면
    어긋난다. 밑줄로 시작하지만 `app` 안에서는 의도된 공유다."""
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError(f"event timestamp must be tz-aware: {at!r}")


class Event:
    """모든 이벤트의 기반. `event_q` 가 하나의 타입으로 다룬다."""


@dataclass(frozen=True, slots=True)
class TickUpdate(Event):
    stock_code: str
    price: int
    source: TickSource
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class StageFilled(Event):
    """`fill_qty` 는 누적, `fill_price` 는 수량가중평균이다 (2A 핸드오버 6)."""

    config_id: int
    cycle_id: int
    stage_no: int
    side: str
    fill_price: int
    fill_qty: int
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class CycleClosed(Event):
    config_id: int
    cycle_id: int
    reason: CloseReason
    realized_pnl: int
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class CycleLoadFailed(Event):
    """복원 실패 — 2A 핸드오버 7. 크래시 대신 사용자에게 나갈 길을 준다."""

    config_id: int | None
    cycle_id: int
    detail: str
    action_taken: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class ReconcileMismatch(Event):
    stock_code: str
    internal_qty: int
    broker_qty: int
    verdict: str
    action_taken: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)
        if self.verdict not in RECONCILE_VERDICTS:
            raise ValueError(
                f"verdict must be one of {sorted(RECONCILE_VERDICTS)}: "
                f"{self.verdict!r}"
            )


@dataclass(frozen=True, slots=True)
class QuoteFallback(Event):
    """설계서 8.4절 — 폴백 구간을 로깅해야 하므로 진입과 복귀를 구분한다."""

    stock_codes: tuple[str, ...]
    active: bool
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderRejected(Event):
    """명시적 거부 — 단계는 이미 WAITING 으로 복구되었다."""

    config_id: int
    cycle_id: int
    stage_no: int
    api_code: str | None
    api_message: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderUnknown(Event):
    """D12 — 응답 유실. 재발주하지 않고 조회로 확인하는 중이다."""

    config_id: int
    cycle_id: int
    stage_no: int
    client_ref: str
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class EmergencyResult(Event):
    scope: str
    stock_code: str | None
    result: str
    qty_before: int | None
    qty_after: int | None
    canceled_orders: int | None
    detail: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)
        if self.result not in EMERGENCY_RESULTS:
            raise ValueError(
                f"result must be one of {sorted(EMERGENCY_RESULTS)}: "
                f"{self.result!r}"
            )


@dataclass(frozen=True, slots=True)
class GuardBlocked(Event):
    """가드가 만든 이유 문자열을 그대로 옮긴다 — 한도 숫자를 다시 쓰지 않는다."""

    config_id: int
    stage_no: int
    side: str
    reason: str
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class EngineStopped(Event):
    detail: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)
