"""시뮬레이션 브로커 — 설계서 8.5절.

목적은 API 키 부재라는 임시 사정이 아니다. **모의투자로는 재현할 수 없는 실패
경로**를 검증하는 것이다 — 갭하락을 주문해서 만들 수 없고, 응답 타임아웃을 유발할
수 없고, WebSocket 을 끊었다 붙일 수도 없다.

그래서 검증이 두 층으로 나뉜다: 로직 정확성은 이 브로커로, 사양 적합성은 모의투자로.

**결정론적이다.** 같은 스크립트에 같은 모드면 항상 같은 결과가 나온다. 난수를 쓰지
않고 시간에 의존하지 않는다 — `delay_ticks` 는 실제 시간이 아니라 소비된 틱 수로
센다. 시간에 의존하면 느린 CI 에서 테스트가 흔들린다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from autotrading7s.domain.types import (
    Balance,
    CancelAck,
    FillState,
    Holding,
    LimitOrderRequest,
    MarketSellRequest,
    OrderAck,
    OrderStatus,
    Side,
    Tick,
    TickSource,
)

_EPOCH = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


class FillMode(Enum):
    INSTANT = "INSTANT"
    DELAYED = "DELAYED"
    PARTIAL = "PARTIAL"
    NEVER = "NEVER"


@dataclass
class _Order:
    broker_order_id: str
    client_ref: UUID
    code: str
    side: Side
    qty: int
    price: int | None          # None 이면 시장가
    state: FillState
    filled_qty: int = 0
    filled_price: int | None = None
    fill_at_tick: int | None = None   # DELAYED 모드에서 체결될 틱 번호


class FakeBroker:
    def __init__(
        self,
        script: list[int],
        *,
        code: str = "005930",
        fill_mode: FillMode = FillMode.INSTANT,
        partial_ratio: Decimal = Decimal("0.4"),
        delay_ticks: int = 3,
        cash: int = 100_000_000,
    ) -> None:
        if not script:
            raise ValueError("script must not be empty")
        self._script = list(script)
        self._code = code
        self._fill_mode = fill_mode
        self._partial_ratio = partial_ratio
        self._delay_ticks = delay_ticks
        self._cash = cash
        self._orders: dict[str, _Order] = {}
        self._positions: dict[str, tuple[int, int]] = {}   # code → (qty, 취득원가합)
        self._ticks_consumed = 0
        self._next_id = 1

    # ── 시세 ────────────────────────────────────────────────────────────
    def subscribe_quotes(self, codes: list[str]) -> AsyncIterator[Tick]:
        return self._replay()

    async def _replay(self) -> AsyncIterator[Tick]:
        for price in self._script[self._ticks_consumed:]:
            self._ticks_consumed += 1
            self._settle_delayed()
            yield Tick(
                code=self._code,
                price=price,
                at=_EPOCH + timedelta(seconds=self._ticks_consumed),
                source=TickSource.WS,
            )

    async def get_price(self, code: str) -> int:
        """마지막으로 재생된 틱. 아직 없으면 스크립트의 첫 값."""
        index = max(0, self._ticks_consumed - 1)
        return self._script[index]

    def _current_price(self) -> int:
        index = max(0, self._ticks_consumed - 1)
        return self._script[index]

    # ── 주문 ────────────────────────────────────────────────────────────
    async def place_limit_order(self, req: LimitOrderRequest) -> OrderAck:
        return self._accept(req.client_ref, req.code, req.side, req.qty, req.price)

    async def place_market_sell(self, req: MarketSellRequest) -> OrderAck:
        return self._accept(req.client_ref, req.code, Side.SELL, req.qty, None)

    def _accept(
        self, client_ref: UUID, code: str, side: Side, qty: int, price: int | None
    ) -> OrderAck:
        broker_order_id = f"FAKE-{self._next_id}"
        self._next_id += 1
        order = _Order(
            broker_order_id=broker_order_id, client_ref=client_ref, code=code,
            side=side, qty=qty, price=price, state=FillState.OPEN,
        )
        self._orders[broker_order_id] = order

        if price is None or self._fill_mode is FillMode.INSTANT:
            # 시장가는 모드와 무관하게 즉시 체결한다 — 실제 시장가의 성질이다.
            self._fill(order, qty)
        elif self._fill_mode is FillMode.PARTIAL:
            partial = int(Decimal(qty) * self._partial_ratio)
            # 0주 부분체결은 도메인의 StageState 불변식이 거부하는 상태를 만든다.
            self._fill(order, max(1, partial))
        elif self._fill_mode is FillMode.DELAYED:
            order.fill_at_tick = self._ticks_consumed + self._delay_ticks
        # NEVER 는 아무것도 하지 않는다 — OPEN 으로 남는다.

        return OrderAck(client_ref=client_ref, broker_order_id=broker_order_id,
                        accepted_at=_EPOCH)

    def _fill(self, order: _Order, qty: int) -> None:
        price = order.price if order.price is not None else self._current_price()
        order.filled_qty = qty
        order.filled_price = price
        order.state = FillState.FILLED if qty == order.qty else FillState.PARTIAL

        held_qty, held_cost = self._positions.get(order.code, (0, 0))
        if order.side is Side.BUY:
            self._cash -= price * qty
            self._positions[order.code] = (held_qty + qty, held_cost + price * qty)
        else:
            self._cash += price * qty
            unit_cost = 0 if held_qty == 0 else held_cost // held_qty
            remaining = max(0, held_qty - qty)
            self._positions[order.code] = (remaining, unit_cost * remaining)

    def _settle_delayed(self) -> None:
        for order in self._orders.values():
            if (order.state is FillState.OPEN
                    and order.fill_at_tick is not None
                    and self._ticks_consumed >= order.fill_at_tick):
                self._fill(order, order.qty)

    async def cancel_order(self, broker_order_id: str) -> CancelAck:
        order = self._orders[broker_order_id]
        if order.state in (FillState.FILLED, FillState.CANCELED):
            raise ValueError(
                f"order {broker_order_id} is already {order.state.value}"
            )
        order.state = FillState.CANCELED
        return CancelAck(broker_order_id=broker_order_id, canceled_at=_EPOCH)

    async def get_order(self, broker_order_id: str) -> OrderStatus:
        order = self._orders[broker_order_id]
        return OrderStatus(
            client_ref=order.client_ref, broker_order_id=order.broker_order_id,
            state=order.state, filled_qty=order.filled_qty,
            filled_price=order.filled_price,
        )

    async def list_orders_today(self, code: str | None) -> list[OrderStatus]:
        return [
            await self.get_order(o.broker_order_id)
            for o in self._orders.values()
            if code is None or o.code == code
        ]

    # ── 잔고 ────────────────────────────────────────────────────────────
    async def get_balance(self) -> Balance:
        """예수금과 보유종목.

        전량 매도된 종목은 `_positions` 에서 지우지 않고 `qty=0` 인 항목으로
        남긴다 — "응답에 없음"이 아니라 "보유 0"을 뜻한다. `Balance.qty_of` 는
        두 경우 모두 0 을 반환해 호출부에서 구별이 안 되지만(Plan 1 최종 리뷰
        handover 5), 이 브로커는 그 구별이 필요한 미래 테스트(Plan 2B)를 위해
        일부러 "보유 0" 쪽을 만든다. "응답에 없음"을 재현하려면 `_positions`
        에서 항목을 지우는 별도 경로가 필요하며 현재는 만들지 않았다.
        """
        holdings = tuple(
            Holding(code=code, qty=qty,
                    avg_price=0 if qty == 0 else cost // qty)
            for code, (qty, cost) in sorted(self._positions.items())
        )
        return Balance(cash=self._cash, holdings=holdings)
