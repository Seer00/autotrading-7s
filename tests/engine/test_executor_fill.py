from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import Event, StageFilled
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.domain.types import StageStatus, Tick, TickSource
from autotrading7s.engine.executor import Executor

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _tick(price: int) -> Tick:
    return Tick(code="005930", price=price, at=AT, source=TickSource.WS)


def _make(repo, broker):
    clock = FakeClock(current=AT)
    events: list[Event] = []
    return (Executor(repo=repo, broker=broker, clock=clock,
                     emit=events.append), clock, events)


async def _buy_leg(repo, ex, *, qty=100, price=10_000, stage_no=1):
    cyc = repo.load_active_cycles()[0]
    config = repo.load_config(cyc.config_id)
    stage = next(s for s in repo.load_stages(cyc.cycle_id)
                 if s.stage_no == stage_no)
    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=stage_no,
                                              limit_price=price, qty=qty,
                                              reason="매수"),
                            tick=_tick(price))
    return cyc, config, outcome


@pytest.mark.asyncio
async def test_full_fill_moves_the_stage_to_holding(repo_fresh):
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "FILLED"
    assert out.stage.status is StageStatus.HOLDING
    assert (out.stage.fill_price, out.stage.fill_qty) == (10_000, 100)
    assert repo_fresh.load_stages(cyc.cycle_id)[0].fill_qty == 100
    assert [type(e) for e in events] == [StageFilled]
    assert events[0].fill_qty == 100


@pytest.mark.asyncio
async def test_unfilled_order_stays_open_before_the_timeout(repo_fresh):
    """3초가 지나기 전에는 취소하지 않는다 — 유동성이 낮으면 곧 체결된다."""
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)
    clock.advance(2.9)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "STILL_OPEN"
    assert out.stage.status is StageStatus.BUY_PENDING
    assert events == []


@pytest.mark.asyncio
async def test_unfilled_order_is_canceled_at_the_timeout(repo_fresh):
    """⑥ 3초 후 미체결 → 취소 → WAITING (다음 틱에 재시도)."""
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)
    clock.advance(3.0)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "CANCELED_UNFILLED"
    assert out.stage.status is StageStatus.WAITING
    assert repo_fresh.load_pending_orders() == []
    assert events == []


@pytest.mark.asyncio
async def test_partial_buy_confirms_the_filled_portion(repo_fresh):
    """설계서 200행 — 매수 부분체결은 체결 수량만으로 HOLDING 을 확정하고
    잔량 주문을 취소한다.

    보유가 계획수량보다 적게 생기는 것이 정상이다. 계획수량으로 확정하면
    사지 않은 주식을 보유로 기록하게 되고, 목표가 매도에서 과다매도가 된다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"),
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)
    clock.advance(3.0)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "PARTIAL_CONFIRMED"
    assert out.stage.status is StageStatus.HOLDING
    assert out.stage.fill_qty == 40           # 100주 요청, 40주 체결
    assert out.stage.fill_price == 10_000
    assert repo_fresh.load_pending_orders() == []


@pytest.mark.asyncio
async def test_partial_sell_returns_the_remainder_to_holding(repo_fresh):
    """매도 부분체결의 비대칭 — 체결분만 매도로 처리하고 잔량은 보유로 복귀.

    한국 주식 주문은 당일에만 유효하므로 부분체결 매도의 잔량이 취소되면
    보유가 줄어드는 것이 일상적 경로다 (cancel_sell 의 존재 이유).
    """
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)
    filled = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                                client_ref=sent.client_ref,
                                broker_order_id=sent.broker_order_id,
                                sent_at=AT, timeout_sec=3)
    assert filled.stage.fill_qty == 100

    broker._fill_mode = FillMode.PARTIAL      # 매도만 부분체결로 바꾼다
    broker._partial_ratio = Decimal("0.4")
    sell = await ex.send(cycle=cyc, config=config, stage=filled.stage,
                         decision=SellStage(stage_no=1, limit_price=10_500,
                                            qty=100, reason="매도"),
                         tick=_tick(10_500))
    sent_at = clock.now()
    clock.advance(3.0)
    out = await ex.poll_fill(cycle=cyc, config=config, stage=sell.stage,
                             client_ref=sell.client_ref,
                             broker_order_id=sell.broker_order_id,
                             sent_at=sent_at, timeout_sec=3)

    assert out.action == "PARTIAL_CONFIRMED"
    assert out.stage.status is StageStatus.HOLDING
    assert out.stage.fill_qty == 60           # 100 − 40
    assert out.stage.fill_price == 10_000     # 취득원가는 불변


@pytest.mark.asyncio
async def test_realized_pnl_is_exact_across_a_partial_sell(repo_fresh):
    """부분체결 매도 후 잔량을 다시 팔면 실현손익이 정확히 맞아야 한다.

    100주를 10,000원에 사서 10,500원에 40주 + 60주로 나눠 팔면 정확히
    100 × 500 = 50,000원이다. 이 값이 틀리는 방식이 두 가지 있고 둘 다
    이 프로젝트가 이미 겪었다: (1) fill_qty 를 증분으로 기록하면 매수량이
    부풀려져 원가가 과소평가된다, (2) 잔량 취소로 생긴 CANCELED 행의 체결
    데이터가 집계에서 빠지면 매도금액이 통째로 사라진다. 후자가 Plan 2A 의
    최악의 결함이었다 (보고 +399,200 / 진짜 +19,200).
    """
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)
    held = (await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                               client_ref=sent.client_ref,
                               broker_order_id=sent.broker_order_id,
                               sent_at=AT, timeout_sec=3)).stage

    broker._fill_mode = FillMode.PARTIAL
    broker._partial_ratio = Decimal("0.4")
    first = await ex.send(cycle=cyc, config=config, stage=held,
                          decision=SellStage(stage_no=1, limit_price=10_500,
                                             qty=100, reason="매도"),
                          tick=_tick(10_500))
    sent_at = clock.now()
    clock.advance(3.0)
    after_partial = await ex.poll_fill(
        cycle=cyc, config=config, stage=first.stage,
        client_ref=first.client_ref, broker_order_id=first.broker_order_id,
        sent_at=sent_at, timeout_sec=3)
    assert after_partial.stage.fill_qty == 60

    broker._fill_mode = FillMode.INSTANT
    second = await ex.send(cycle=cyc, config=config,
                           stage=after_partial.stage,
                           decision=SellStage(stage_no=1, limit_price=10_500,
                                              qty=60, reason="잔량 매도"),
                           tick=_tick(10_500))
    done = await ex.poll_fill(cycle=cyc, config=config, stage=second.stage,
                              client_ref=second.client_ref,
                              broker_order_id=second.broker_order_id,
                              sent_at=clock.now(), timeout_sec=3)

    assert done.action == "FILLED"
    assert repo_fresh.realized_pnl_for_cycle(cyc.cycle_id) == 50_000


@pytest.mark.asyncio
async def test_full_sell_respects_allow_rebuy(repo_fresh):
    """전량 매도 후 목적지는 설정이 정한다 — allow_rebuy 면 WAITING."""
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)
    held = (await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                               client_ref=sent.client_ref,
                               broker_order_id=sent.broker_order_id,
                               sent_at=AT, timeout_sec=3)).stage
    assert config.allow_rebuy is True

    sell = await ex.send(cycle=cyc, config=config, stage=held,
                         decision=SellStage(stage_no=1, limit_price=10_500,
                                            qty=100, reason="매도"),
                         tick=_tick(10_500))
    out = await ex.poll_fill(cycle=cyc, config=config, stage=sell.stage,
                             client_ref=sell.client_ref,
                             broker_order_id=sell.broker_order_id,
                             sent_at=clock.now(), timeout_sec=3)

    assert out.stage.status is StageStatus.WAITING
    assert out.stage.rebuy_count == 1
    assert out.stage.fill_qty is None


@pytest.mark.asyncio
async def test_cancel_failure_keeps_the_stage_pending(repo_fresh):
    """취소가 실패하면 브로커에 주문이 살아 있으므로 PENDING 이 사실이다."""
    from autotrading7s.ports.broker import BrokerRejected

    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, ex)

    async def refuse(broker_order_id):
        raise BrokerRejected("40560", "취소 불가")

    broker.cancel_order = refuse              # type: ignore[method-assign]
    clock.advance(5.0)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "STILL_OPEN"
    assert out.stage.status is StageStatus.BUY_PENDING
    assert [r.status for r in repo_fresh.load_pending_orders()] == ["ACCEPTED"]


@pytest.mark.asyncio
async def test_poll_fill_refuses_a_non_pending_stage(repo_fresh):
    """PENDING 이 아닌 단계를 폴하는 것은 호출자 버그다.

    조용히 넘어가면 이미 반영된 체결을 다시 반영하려 시도하고, 그 실패가
    save_stage 의 가드에서야 드러난다 — 그때는 어느 호출이 잘못이었는지
    알 수 없다.
    """
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, _ = _make(repo_fresh, broker)
    cyc = repo_fresh.load_active_cycles()[0]
    config = repo_fresh.load_config(cyc.config_id)
    waiting = repo_fresh.load_stages(cyc.cycle_id)[0]

    with pytest.raises(ValueError, match="pending"):
        await ex.poll_fill(cycle=cyc, config=config, stage=waiting,
                           client_ref="x", broker_order_id="FAKE-1",
                           sent_at=AT, timeout_sec=3)
