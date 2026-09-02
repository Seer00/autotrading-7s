"""도메인 어휘 — 열거형과 값 객체.

설계서 3.3절. 이 모듈은 표준 라이브러리 외 어떤 것도 import 하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID

from autotrading7s.domain.errors import DomainInvariantError


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class TickSource(Enum):
    """시세 출처. WebSocket 끊김 구간을 사후에 식별하기 위해 기록한다."""

    WS = "WS"
    REST_POLL = "REST_POLL"


class OrderPath(Enum):
    """주문 경로. 자동 판단과 수동 개입을 데이터에 영구 구분한다(설계서 12.2절)."""

    TRIGGER = "TRIGGER"
    EMERGENCY = "EMERGENCY"


class StageStatus(Enum):
    WAITING = "WAITING"
    BUY_PENDING = "BUY_PENDING"
    HOLDING = "HOLDING"
    SELL_PENDING = "SELL_PENDING"
    SOLD = "SOLD"


class CycleStatus(Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    LIQUIDATING = "LIQUIDATING"
    CLOSED = "CLOSED"


class CloseReason(Enum):
    NORMAL = "NORMAL"
    EMERGENCY = "EMERGENCY"
    # D20 — 거래정지 등으로 정상 청산이 불가능해 잔량을 남긴 채 강제로 종료한
    # 사이클. 스키마(설계서 12.1절, `cycle.close_reason` CHECK)가 이미 이 값을
    # 허용하므로 매핑 계층(Plan 2A)이 저장된 행을 복원할 때 필요하다. 이 값을
    # *만드는* 상태 전이(`force_close`)는 그것을 쓰는 Emergency Control
    # Handler 와 함께 설계하기로 미뤄졌다(Plan 2B) — 이 멤버는 그 전이 없이도
    # 이미 저장된 행을 왕복시키는 데 쓰인다.
    FORCED = "FORCED"


class FillState(Enum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class Tick:
    code: str
    price: int
    at: datetime
    source: TickSource

    def __post_init__(self) -> None:
        if isinstance(self.price, bool) or not isinstance(self.price, int):
            raise TypeError(f"price must be int, not {type(self.price).__name__}")
        if self.price <= 0:
            raise DomainInvariantError(f"price must be positive: {self.price}")


@dataclass(frozen=True, slots=True)
class LimitOrderRequest:
    """자동 트리거 경로 전용 주문 요청.

    설계서 6절·8.2절: 신용·미수 필드가 존재하지 않으며, ``price`` 가 필수이므로
    시장가를 표현할 방법이 없다. 원칙을 문서가 아니라 타입으로 강제한다.
    """

    code: str
    side: Side
    qty: int
    price: int
    client_ref: UUID

    def __post_init__(self) -> None:
        if isinstance(self.qty, bool) or not isinstance(self.qty, int):
            raise TypeError(f"qty must be int, not {type(self.qty).__name__}")
        if isinstance(self.price, bool) or not isinstance(self.price, int):
            raise TypeError(f"price must be int, not {type(self.price).__name__}")
        if self.qty <= 0:
            raise DomainInvariantError(f"qty must be positive: {self.qty}")
        if self.price <= 0:
            raise DomainInvariantError(f"price must be positive: {self.price}")


@dataclass(frozen=True, slots=True)
class MarketSellRequest:
    """긴급청산 경로 전용 주문 요청.

    ``reason`` 은 필수 필드다. 사용자 입력 자체는 선택이므로 빈 문자열을
    허용하지만, 필드가 필수여서 사유 기록을 구조적으로 빼먹을 수 없다.
    """

    code: str
    qty: int
    client_ref: UUID
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.qty, bool) or not isinstance(self.qty, int):
            raise TypeError(f"qty must be int, not {type(self.qty).__name__}")
        if self.qty <= 0:
            raise DomainInvariantError(f"qty must be positive: {self.qty}")


@dataclass(frozen=True, slots=True)
class OrderAck:
    client_ref: UUID
    broker_order_id: str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class CancelAck:
    broker_order_id: str
    canceled_at: datetime


@dataclass(frozen=True, slots=True)
class OrderStatus:
    client_ref: UUID
    broker_order_id: str
    state: FillState
    filled_qty: int
    filled_price: int | None
    api_code: str | None = None
    api_message: str | None = None


@dataclass(frozen=True, slots=True)
class Holding:
    """계좌 잔고의 한 종목.

    ``qty`` 는 ``Balance.qty_of`` 를 거쳐 ``MarketSellRequest.qty`` 로
    흘러간다. 설계서 11.1절은 긴급청산 수량을 실제 계좌에서 가져오라고
    요구하므로, 이 타입이 그 수량의 경계다. 전량 매도된 종목이 0주로 남아
    오는 것은 정상이라 0 을 허용한다.
    """

    code: str
    qty: int
    avg_price: int

    def __post_init__(self) -> None:
        for name in ("qty", "avg_price"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be int, not {type(value).__name__}")
            if value < 0:
                raise DomainInvariantError(f"{name} must be non-negative: {value}")


@dataclass(frozen=True, slots=True)
class Balance:
    cash: int
    holdings: tuple[Holding, ...]

    def qty_of(self, code: str) -> int:
        for holding in self.holdings:
            if holding.code == code:
                return holding.qty
        return 0
