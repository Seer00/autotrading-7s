"""브로커 포트 — 설계서 8.1절.

도메인이 증권사를 보는 유일한 창이다. 키움 어댑터(Plan 3)와 시뮬 브로커(Task 11)가
이것을 구현하며, 엔진은 둘을 구분하지 못한다.

주문 요청이 두 타입으로 나뉘어 있는 것이 이 포트의 핵심이다. 자동 트리거 경로는
`LimitOrderRequest` 만 만들 수 있고 그 타입에는 시장가를 표현할 방법이 없다. 시장가는
`MarketSellRequest` 뿐이며 긴급청산 전용이고 `reason` 이 필수다(설계서 8.2절).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from autotrading7s.domain.types import (
    Balance,
    CancelAck,
    LimitOrderRequest,
    MarketSellRequest,
    OrderAck,
    OrderStatus,
    Tick,
)


class BrokerError(Exception):
    """브로커 전송 계층의 실패 — 어댑터가 던지고 엔진이 분기한다.

    예외를 포트에 두는 이유: 엔진은 `adapters/` 를 import 할 수 없으므로
    (설계서 7.2절 의존 규칙), 예외가 어댑터에만 있으면 엔진은 UNKNOWN 분기를
    타입으로 구분할 수 없고 결국 `except Exception` 을 쓰게 된다. 그것은 DB
    손상(`CorruptRowError`)과 프로그래밍 오류까지 '응답 유실' 로 취급한다는
    뜻이다. 어떤 실패를 어떤 이름으로 던지는지는 포트 계약의 일부이며, Plan 3
    의 키움 어댑터도 같은 예외를 던져야 한다.
    """


class BrokerTimeout(BrokerError):
    """브로커가 응답하지 않았다 — 접수 여부를 알 수 없다.

    `TimeoutError` 를 상속하지 **않는다.** `asyncio.TimeoutError is
    TimeoutError` 이므로 상속하면 엔진의 `except BrokerTimeout` 이 asyncio
    자체의 대기 타임아웃까지 삼키고, 브로커와 무관한 일을 UNKNOWN 으로 기록해
    재발주 금지 상태에 들어간다.

    **이 예외를 받았을 때 재발주해서는 안 된다.** 요청이 서버에 도달했는지 알
    수 없고, 도달했을 수도 있다. 설계서 9절 ⑤ 가 규정한 유일한 안전한 행동은
    `list_orders_today` 로 `client_ref` 를 대조해 사실을 확인하는 것이다.
    """


class BrokerRejected(BrokerError):
    """브로커가 명시적으로 거부했다. 타임아웃과 달리 미접수가 확실하다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BrokerDisconnected(BrokerError):
    """시세 스트림이 끊겼다. 설계서 8.4절의 REST 폴백이 여기서 시작된다.

    주문 경로는 막지 않는다 — 8.4절은 WS 가 끊겨도 트리거 판정과 발주를 계속
    수행하도록 규정한다.
    """


@runtime_checkable
class BrokerPort(Protocol):
    def subscribe_quotes(self, codes: list[str]) -> AsyncIterator[Tick]:
        """실시간 체결가 스트림. 끊김 시 어댑터가 재연결하고 구독을 복원한다."""
        ...

    async def place_limit_order(self, req: LimitOrderRequest) -> OrderAck:
        """자동 트리거 경로 전용. 지정가만 표현할 수 있다."""
        ...

    async def place_market_sell(self, req: MarketSellRequest) -> OrderAck:
        """긴급청산 전용. `req.reason` 이 필수다."""
        ...

    async def cancel_order(self, broker_order_id: str) -> CancelAck: ...

    async def get_order(self, broker_order_id: str) -> OrderStatus:
        """이 주문의 현재 체결 상태.

        `OrderStatus.filled_qty` 는 **누적값**이다 — 이 주문이 지금까지
        체결한 총 수량이며, 마지막 조회 이후의 증분이 아니다.
        `OrderStatus.filled_price` 는 지금까지 모든 체결의 **수량가중평균가**
        다, 가장 최근 체결의 가격이 아니다. `RepositoryPort.update_order_log`
        의 `fill_qty`·`fill_price` 는 이 값을 그대로 받아 쓰도록 되어 있다.
        """
        ...

    async def list_orders_today(self, code: str | None) -> list[OrderStatus]:
        """당일 주문 내역. 설계서 9절의 UNKNOWN 분기가 client_ref 대조에 쓴다."""
        ...

    async def get_balance(self) -> Balance:
        """예수금과 보유종목. 대사(설계서 10.2절)와 긴급청산의 수량 확정에 쓴다."""
        ...

    async def get_price(self, code: str) -> int:
        """WebSocket 끊김 시 REST 폴백(설계서 8.4절)."""
        ...
