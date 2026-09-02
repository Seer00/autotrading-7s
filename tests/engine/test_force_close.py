from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import Event
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.rules import BuyStage
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    Tick,
    TickSource,
)
from autotrading7s.engine.emergency import EmergencyHandler
from autotrading7s.engine.executor import Executor

AT = datetime(2026, 9, 2, 15, 28, tzinfo=UTC)
STATEMENT = "거래정지로 청산 불가, 잔량 100주는 직접 처리 예정"


def _handler(repo, broker, *, market_open=True):
    events: list[Event] = []
    return (EmergencyHandler(repo=repo, broker=broker,
                             clock=FakeClock(current=AT,
                                             market_open=market_open),
                             emit=events.append),
            events)


def _liquidating(repo):
    cyc = repo.load_active_cycles()[0]
    liq = cycle_mod.begin_liquidation(cyc)
    repo.save_cycle(liq)
    return liq


@pytest.mark.asyncio
async def test_force_close_requires_liquidating(repo_two_stocks):
    """설계서 11.4절 — 사용자가 먼저 긴급청산을 시도해야 한다.

    RUNNING 에서 바로 강제 종료할 수 있으면, 그 시도 이력(횟수·시각·실패
    사유)이라는 다이얼로그의 근거 없이 내부 기록과 실계좌를 어긋나게 만들 수
    있다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    assert cyc.status is CycleStatus.RUNNING
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, events = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FAILED"
    assert "LIQUIDATING" in out.detail
    assert repo_two_stocks.load_cycle(cyc.cycle_id).status is CycleStatus.RUNNING


@pytest.mark.asyncio
async def test_force_close_records_statement_and_remainder(repo_two_stocks):
    """⑤⑦⑧ — 증언과 잔량이 영구 기록되고 설정이 IDLE 로 돌아간다."""
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, events = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FORCED_CLOSE"
    assert out.qty_after == 100
    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.status is CycleStatus.CLOSED
    assert reloaded.close_reason is CloseReason.FORCED
    assert reloaded.forced_close_qty == 100
    assert reloaded.forced_close_reason == STATEMENT
    assert repo_two_stocks.load_config(cyc.config_id).status == "IDLE"
    row = repo_two_stocks._conn.execute(
        "SELECT result, reason, qty_before, qty_after "
        "FROM emergency_liquidation_log"
    ).fetchone()
    assert dict(row) == {"result": "FORCED_CLOSE", "reason": STATEMENT,
                         "qty_before": 100, "qty_after": 100}


@pytest.mark.asyncio
async def test_qty_after_is_the_remainder_not_zero(repo_two_stocks):
    """강제 종료는 아무것도 팔지 않는다.

    qty_after=0 으로 기록하면 이력이 "다 팔았다" 고 말하게 되고, 그것이
    설계서 11.4절이 방지하려는 바로 그 거짓이다 — 사용자가 나중에 이력을
    보고 그 주식이 어디 갔는지 물을 때 답이 틀려 있다.
    """
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _ = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert (out.qty_before, out.qty_after) == (100, 100)


@pytest.mark.asyncio
async def test_force_close_keeps_per_stage_remainders_in_the_log(repo_two_stocks):
    """⑥ — 전 단계를 SOLD 로 갱신하되 단계별 잔량을 이력에 남긴다.

    단계 상태를 SOLD 로 덮으면 그 정보가 사라진다. 사용자가 나중에 "어느
    단계에 얼마가 남았는지" 를 물을 수 있는 유일한 곳이 이 로그다.
    """
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _ = _handler(repo_two_stocks, broker)

    await handler.force_close(cyc.config_id, reason=STATEMENT)

    row = repo_two_stocks._conn.execute(
        "SELECT detail_json FROM emergency_liquidation_log"
    ).fetchone()
    detail = json.loads(dict(row)["detail_json"])
    assert detail["stage_remainders"] == {"1": 100}
    assert detail["broker_qty"] == 100


@pytest.mark.asyncio
async def test_zero_remainder_takes_the_normal_close_path(repo_two_stocks):
    """③ — 잔량 0 의 강제 종료는 의미가 없다.

    허용하면 정상 종료 경로의 보유 0 검사를 건너뛰는 수단이 된다. 실계좌가
    0 이면 사용자가 이미 직접 팔았다는 뜻이므로, 프로그램 관리 밖에 남는
    주식이 없다 — FORCED 가 아니라 EMERGENCY 종료다.
    """
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (0, 0)})
    handler, events = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "SUCCESS"
    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.close_reason is CloseReason.EMERGENCY
    assert reloaded.forced_close_qty is None
    assert "정상 종료" in out.detail


@pytest.mark.asyncio
async def test_absent_from_balance_blocks_force_close_too(repo_two_stocks):
    """잔량을 모르는 채로 증언을 기록하면 그 증언이 근거 없는 숫자를 담는다."""
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True)
    handler, _ = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FAILED"
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)


@pytest.mark.asyncio
async def test_force_close_cancels_open_orders(repo_two_stocks):
    """④ — 11.1절 ②과 같은 이유. 남은 매수 주문이 체결되면 관리 밖 주식이 늘어난다."""
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000,
                        holdings={"005930": (100, 1_000_000)})
    ex = Executor(repo=repo_two_stocks, broker=broker,
                  clock=FakeClock(current=AT), emit=lambda e: None)
    waiting = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.stage_no == 2)
    await ex.send(cycle=cyc, config=config, stage=waiting,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))
    liq = cycle_mod.begin_liquidation(repo_two_stocks.load_cycle(cyc.cycle_id))
    repo_two_stocks.save_cycle(liq)

    handler, _ = _handler(repo_two_stocks, broker)
    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FORCED_CLOSE"
    assert out.canceled_orders == 1
    assert repo_two_stocks.load_pending_orders() == []


@pytest.mark.asyncio
async def test_forced_stock_disappears_from_holdings(repo_two_stocks):
    """설계서 11.4절 — 강제 종료 후 그 종목은 프로그램의 관리 밖이다."""
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _ = _handler(repo_two_stocks, broker)

    await handler.force_close(cyc.config_id, reason=STATEMENT)

    codes = {h.stock_code for h in repo_two_stocks.holdings()}
    assert "005930" not in codes
    assert "000660" in codes
