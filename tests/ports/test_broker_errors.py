from __future__ import annotations

import asyncio

from autotrading7s.adapters.fake import broker as fake_broker
from autotrading7s.ports.broker import (
    BrokerDisconnected,
    BrokerError,
    BrokerRejected,
    BrokerTimeout,
)


def test_three_transport_errors_share_one_base():
    """엔진이 `except BrokerError` 하나로 전송 실패를 잡을 수 있어야 한다.

    공통 상위가 없으면 엔진이 `except Exception` 을 쓰게 되고, 그것은 DB 손상
    (CorruptRowError)과 프로그래밍 오류까지 '응답 유실' 로 취급하는 것이다.
    """
    for cls in (BrokerTimeout, BrokerRejected, BrokerDisconnected):
        assert issubclass(cls, BrokerError), cls
    assert issubclass(BrokerError, Exception)


def test_broker_timeout_does_not_inherit_builtin_timeout():
    """2A 의 결정을 그대로 유지한다.

    `asyncio.TimeoutError is TimeoutError` 이므로, 상속하면 엔진의
    `except BrokerTimeout` 이 asyncio 자체의 취소·대기 타임아웃까지 삼킨다 —
    그러면 "브로커 응답 유실" 이 아닌 것을 UNKNOWN 으로 기록하고 재발주 금지
    상태에 들어간다.
    """
    assert not issubclass(BrokerTimeout, TimeoutError)
    assert BrokerTimeout is not asyncio.TimeoutError


def test_broker_rejected_carries_api_code_and_message():
    exc = BrokerRejected("40510", "거래정지")
    assert exc.code == "40510"
    assert exc.message == "거래정지"
    assert "40510" in str(exc)


def test_fake_adapter_reexports_the_port_exceptions():
    """기존 테스트의 import 경로가 살아 있어야 하고, 같은 타입이어야 한다.

    같은 이름의 별개 클래스가 두 곳에 생기면 엔진의 except 절이 어댑터가 던진
    예외를 놓친다 — 조용히 UNKNOWN 분기가 죽는다.
    """
    assert fake_broker.BrokerTimeout is BrokerTimeout
    assert fake_broker.BrokerRejected is BrokerRejected
    assert fake_broker.BrokerDisconnected is BrokerDisconnected


def test_corrupt_row_error_lives_in_the_port_and_is_reexported():
    from autotrading7s.adapters.sqlite import mapping
    from autotrading7s.domain.errors import DomainInvariantError
    from autotrading7s.ports.repository import CorruptRowError

    assert mapping.CorruptRowError is CorruptRowError
    assert issubclass(CorruptRowError, DomainInvariantError)
    assert issubclass(CorruptRowError, ValueError)
