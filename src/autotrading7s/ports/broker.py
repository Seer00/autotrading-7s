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
