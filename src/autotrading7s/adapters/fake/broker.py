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


class BrokerTimeout(Exception):
    """브로커가 응답하지 않았다.

    `TimeoutError` 를 상속하지 않는다. `asyncio.wait_for` 가 `TimeoutError` 를
    던지므로, 상속하면 엔진의 `except BrokerTimeout` 이 asyncio 자체의 타임아웃까지
    잡는다. 둘은 다른 사건이다 — 이것은 브로커가 답하지 않은 것이고, 그것은 우리
    쪽 대기 한도를 넘긴 것이다.

    **이 예외를 받았을 때 재발주해서는 안 된다.** 요청이 서버에 도달했는지 알 수
    없고, 도달했을 수도 있다. 설계서 9절 ⑤ 가 규정한 유일한 안전한 행동은
    `list_orders_today` 로 `client_ref` 를 대조해 사실을 확인하는 것이다.
    """


class BrokerRejected(Exception):
    """브로커가 명시적으로 거부했다. 타임아웃과 달리 미접수가 확실하다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BrokerDisconnected(Exception):
    """시세 스트림이 끊겼다. 설계서 8.4절의 REST 폴백이 여기서 시작된다."""


class FailMode(Enum):
    NONE = "NONE"
    TIMEOUT = "TIMEOUT"
    REJECT = "REJECT"
    DISCONNECT = "DISCONNECT"


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
    """`BrokerPort` 의 시뮬레이션 구현.

    두 계층의 실패를 따로 흉내낸다. `FailMode` 는 **전송 계층**(응답 유실,
    명시적 거부, 스트림 끊김)이고 `validate_account=True` 는 **거래소
    계층**(예수금 부족, 보유수량 부족)이다. 전송이 먼저 판정된다 — 실제 순서가
    그렇고, `fail_after` 가 "실패할 수 있었던 호출 N번" 이라는 의미를 유지한다.

    **`validate_account` 는 기본 꺼짐이다.** 켜지 않으면 `_cash` 가 조용히
    음수가 되고 보유 0 인 종목의 매도가 현금을 늘린다. 총투입 한도 캡이 이
    프로그램의 유일한 구조적 보호장치이므로(설계서 6절), **한도나 긴급청산을
    검증하는 테스트는 반드시 켜야 한다** — 끄고 돌리면 한도를 넘겨 매수하거나
    없는 포지션을 매도하는 엔진 버그가 전부 통과한다. G2 게이트는 자기 소스에서
    모든 생성에 `validate_account=True` 가 있는지 직접 확인한다.

    `holdings` 로 엔진이 모르는 포지션을 미리 심을 수 있다 — 대사 불일치와
    긴급청산 시나리오(설계서 10.2절·11.1절 ③)의 출발점이다.
    """

    def __init__(
        self,
        script: list[int],
        *,
        code: str = "005930",
        fill_mode: FillMode = FillMode.INSTANT,
        partial_ratio: Decimal = Decimal("0.4"),
        delay_ticks: int = 3,
        cash: int = 100_000_000,
        fail_mode: FailMode = FailMode.NONE,
        fail_after: int = 0,
        validate_account: bool = False,
        holdings: dict[str, tuple[int, int]] | None = None,
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
        self._validate_account = validate_account
        # code → (qty, 취득원가합). `holdings` 로 엔진이 모르는 포지션을 미리
        # 심을 수 있다 — 대사 불일치와 긴급청산 시나리오의 출발점이다.
        self._positions: dict[str, tuple[int, int]] = dict(holdings or {})
        self._ticks_consumed = 0
        self._next_id = 1
        self._fail_mode = fail_mode
        self._fail_after = fail_after
        self._calls = 0

    def clear_failure(self) -> None:
        """실패 모드를 해제한다. 재연결·재시도 시나리오에 쓴다."""
        self._fail_mode = FailMode.NONE
        self._calls = 0

    def _should_fail(self, *applicable: FailMode) -> bool:
        """fail_after 번째 호출까지는 통과시키고 그 다음부터 실패한다.

        `applicable` 은 호출부가 실제로 실패시킬 수 있는 모드들이다. 현재
        `fail_mode` 가 그 안에 없으면(예: `get_balance` 가 `REJECT` 모드에서
        불렸을 때) 카운터를 건드리지 않고 `False` 를 반환한다 — 그래야
        `fail_after` 가 "실패할 수 있었던 호출 N번"이라는 깨끗한 의미를 갖는다.
        무관한 호출이 카운터를 몰래 소모하면 그 의미가 깨진다.
        """
        if self._fail_mode not in applicable:
            return False
        self._calls += 1
        return self._calls > self._fail_after

    # ── 시세 ────────────────────────────────────────────────────────────
    def subscribe_quotes(self, codes: list[str]) -> AsyncIterator[Tick]:
        return self._replay()

    async def _replay(self) -> AsyncIterator[Tick]:
        for price in self._script[self._ticks_consumed:]:
            if (self._fail_mode is FailMode.DISCONNECT
                    and self._ticks_consumed >= self._fail_after):
                # 끊김은 `_should_fail` 을 쓰지 않는다 — 틱 소비 수를 기준으로
                # 해야 결정론적이고, 재구독 후 남은 틱이 이어지려면 호출 카운터가
                # 아니라 소비된 틱 수로 판정해야 한다.
                raise BrokerDisconnected("stream lost (simulated)")
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
        if self._should_fail(FailMode.REJECT, FailMode.TIMEOUT):
            if self._fail_mode is FailMode.REJECT:
                # 명시적 거부는 주문을 등록하지 않는다 — 미접수가 확실하다.
                raise BrokerRejected("40510", "주문 거부 (시뮬레이션)")
            # TIMEOUT: 등록한 뒤 던진다. 실제 타임아웃의 성질이 그렇고, 설계서
            # 9절 ⑤ 의 "접수됨" 분기를 테스트할 수 있게 하는 장치다.
            self._register(client_ref, code, side, qty, price)
            raise BrokerTimeout("no response from broker (simulated)")
        # DISCONNECT 는 시세 스트림 전용이다 — 주문 경로를 막지 않는다. 설계서
        # 8.4절: WS 가 끊겨도 REST 폴링으로 전환해 트리거 판정과 발주는 계속된다.
        if self._validate_account:
            self._validate(code, side, qty, price)
        return OrderAck(
            client_ref=client_ref,
            broker_order_id=self._register(client_ref, code, side, qty, price),
            accepted_at=_EPOCH,
        )

    def _validate(
        self, code: str, side: Side, qty: int, price: int | None
    ) -> None:
        """거래소 계층의 거부. `validate_account=True` 일 때만 동작한다.

        `FailMode` 뒤에 오는 이유: `FailMode` 는 전송 계층(응답 유실)을
        모델링하고 이것은 거래소 계층이다. 순서를 뒤집으면 `fail_after` 가
        "실패할 수 있었던 호출 N번" 이라는 의미를 잃는다.

        매도 검증은 **요청 수량** 기준이다. 체결 수량으로 검증하면 부분체결로
        조금씩 팔아 없는 포지션을 비울 수 있다.
        """
        if side is Side.BUY:
            # 매수는 지정가만 존재한다 — 자동 트리거 경로에 시장가가 없다
            # (설계서 8.2절). price 가 None 인 매수는 만들 수 없다.
            assert price is not None
            need = price * qty
            if need > self._cash:
                raise BrokerRejected(
                    "40940", f"예수금 부족: 필요 {need:,} > 보유 {self._cash:,}"
                )
            return
        held_qty, _ = self._positions.get(code, (0, 0))
        if qty > held_qty:
            raise BrokerRejected(
                "40950", f"보유수량 부족: 요청 {qty:,} > 보유 {held_qty:,}"
            )

    def _register(
        self, client_ref: UUID, code: str, side: Side, qty: int, price: int | None
    ) -> str:
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

        return broker_order_id

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

        `fail_mode` 가 `TIMEOUT` 이면 실패 카운터가 `fail_after` 를 넘긴 뒤부터
        타임아웃한다 — 설계서 10.2절의 대사가 잔고 조회 실패도 다뤄야 하므로.

        전량 매도된 종목은 `_positions` 에서 지우지 않고 `qty=0` 인 항목으로
        남긴다 — "응답에 없음"이 아니라 "보유 0"을 뜻한다. `Balance.qty_of` 는
        두 경우 모두 0 을 반환해 호출부에서 구별이 안 되지만(Plan 1 최종 리뷰
        handover 5), 이 브로커는 그 구별이 필요한 미래 테스트(Plan 2B)를 위해
        일부러 "보유 0" 쪽을 만든다. "응답에 없음"을 재현하려면 `_positions`
        에서 항목을 지우는 별도 경로가 필요하며 현재는 만들지 않았다.
        """
        if self._should_fail(FailMode.TIMEOUT):
            raise BrokerTimeout("no response from broker (simulated)")
        holdings = tuple(
            Holding(code=code, qty=qty,
                    avg_price=0 if qty == 0 else cost // qty)
            for code, (qty, cost) in sorted(self._positions.items())
        )
        return Balance(cash=self._cash, holdings=holdings)
