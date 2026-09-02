from __future__ import annotations

import inspect
from datetime import datetime, timezone

from autotrading7s.domain.types import CancelAck
from autotrading7s.ports.broker import BrokerPort

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def test_cancel_ack_is_frozen():
    import dataclasses

    ack = CancelAck(broker_order_id="X1", canceled_at=T0)
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        ack.broker_order_id = "X2"  # type: ignore[misc]


def test_broker_port_declares_the_eight_methods():
    """설계서 8.1절의 메서드 목록. 하나라도 빠지면 어댑터가 구현을 빼먹는다."""
    expected = {
        "subscribe_quotes", "place_limit_order", "place_market_sell",
        "cancel_order", "get_order", "list_orders_today", "get_balance",
        "get_price",
    }
    declared = {
        name for name, _ in inspect.getmembers(BrokerPort, inspect.isfunction)
        if not name.startswith("_")
    }
    assert declared == expected


def test_broker_port_is_runtime_checkable():
    """어댑터가 포트를 만족하는지 테스트에서 단정할 수 있어야 한다."""

    class Stub:
        def subscribe_quotes(self, codes): ...   # 포트와 같이 일반 def 다
        async def place_limit_order(self, req): ...
        async def place_market_sell(self, req): ...
        async def cancel_order(self, broker_order_id): ...
        async def get_order(self, broker_order_id): ...
        async def list_orders_today(self, code): ...
        async def get_balance(self): ...
        async def get_price(self, code): ...

    assert isinstance(Stub(), BrokerPort)


def test_incomplete_stub_does_not_satisfy_the_port():
    class Missing:
        def subscribe_quotes(self, codes): ...

    assert not isinstance(Missing(), BrokerPort)


def test_subscribe_quotes_is_not_a_coroutine_function():
    """이 결정은 `runtime_checkable` 이 검사하지 않으므로 여기서 고정한다.

    `async def` 로 선언하면 호출이 코루틴을 반환해 호출부가 `async for` 를 바로
    쓸 수 없다. Plan 3 의 키움 어댑터가 이 결정을 어기면 여기서 잡힌다.
    """
    assert not inspect.iscoroutinefunction(BrokerPort.subscribe_quotes)
    for name in ("place_limit_order", "place_market_sell", "cancel_order",
                 "get_order", "list_orders_today", "get_balance", "get_price"):
        assert inspect.iscoroutinefunction(getattr(BrokerPort, name)), name
