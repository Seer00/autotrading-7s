"""주문 실행 파이프라인 — 설계서 9절.

이 모듈은 주문 **한 건의 생애**를 담당한다. 어느 단계를 살지 팔지는
`rules.decide()` 가 정하고, 가드는 호출자가 이미 통과시킨 뒤에 여기로 온다.

순서가 이 모듈의 전부다:

    ③ order_log INSERT (SENDING)
    ④ stage_state UPDATE → PENDING          ← 여기서 커밋
    ⑤ broker.place_limit_order()

**발주보다 먼저 기록하고 커밋한다.** 발주 후에 기록하면 그 사이에 프로세스가
죽었을 때 '브로커에는 주문이 있는데 우리는 모르는' 고아 주문이 생기고 다음
실행에서 중복 발주로 이어진다. 반대 순서의 최악은 '우리는 보냈다고 기록했는데
실제로는 없는' 상태인데, 이건 조회로 안전하게 정정할 수 있다. 설계서 9절:
**잘못 기록된 쪽이 잘못 잊힌 쪽보다 항상 낫다.**

**⑤의 UNKNOWN 분기가 이 시스템에서 가장 중요한 부분이다.** 응답이 오지 않았다면
서버에 도달하지 못했거나 도달했지만 응답만 유실됐다. 여기서 재발주하면 같은
단계를 두 번 산다. 유일하게 안전한 행동은 `list_orders_today` 로 사실을 확인하는
것이다 (D12).

두 홉(④와 체결 반영)을 합성해 한 번만 저장하는 것은 `save_stage` 가드가
거부한다. 그것은 버그가 아니라 이 순서의 강제다 (2A 핸드오버 9).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from autotrading7s.app.events import Event, OrderRejected, OrderUnknown
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import LimitOrderRequest, OrderPath, Side, Tick
from autotrading7s.ports.broker import (
    BrokerError,
    BrokerPort,
    BrokerRejected,
    BrokerTimeout,
)
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import RepositoryPort, SplitConfig

SEND_STATUSES = frozenset(
    {"ACCEPTED", "REJECTED", "UNKNOWN_ACCEPTED", "UNKNOWN_NOT_SENT",
     "UNKNOWN_UNRESOLVED"}
)


@dataclass(frozen=True, slots=True)
class SendOutcome:
    status: str
    client_ref: str
    broker_order_id: str | None
    stage: StageState

    def __post_init__(self) -> None:
        if self.status not in SEND_STATUSES:
            raise ValueError(f"unknown send status: {self.status!r}")


class Executor:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: Callable[[Event], None],
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    async def send(
        self, *, cycle: Cycle, config: SplitConfig, stage: StageState,
        decision: BuyStage | SellStage, tick: Tick,
    ) -> SendOutcome:
        is_buy = isinstance(decision, BuyStage)
        side = Side.BUY if is_buy else Side.SELL
        client_ref = uuid.uuid4()
        now = self._clock.now()

        # ③ 기록 먼저
        self._repo.append_order_log(
            client_ref=str(client_ref), cycle_id=cycle.cycle_id,
            stage_state_id=self._repo.stage_row_id(cycle.cycle_id,
                                                   stage.stage_no),
            side=side, order_type="LIMIT", path=OrderPath.TRIGGER,
            req_price=decision.limit_price, req_qty=decision.qty,
            trigger_reason=decision.reason, tick_price=tick.price,
            tick_source=tick.source.value, sent_at=now,
        )

        # ④ 단계를 PENDING 으로 — 여기서 커밋된다
        pending = (stage_mod.to_buy_pending(stage) if is_buy
                   else stage_mod.to_sell_pending(stage))
        self._repo.save_stage(cycle.cycle_id, pending)

        # ⑤ 발주
        req = LimitOrderRequest(
            code=config.stock_code, side=side, qty=decision.qty,
            price=decision.limit_price, client_ref=client_ref,
        )
        try:
            ack = await self._broker.place_limit_order(req)
        except BrokerRejected as exc:
            self._repo.update_order_log(
                client_ref=str(client_ref), status="REJECTED",
                api_code=exc.code, api_message=exc.message,
                settled_at=self._clock.now(),
            )
            restored = self._restore(cycle, stage, pending, is_buy)
            self._emit(OrderRejected(
                config_id=config.config_id, cycle_id=cycle.cycle_id,
                stage_no=stage.stage_no, api_code=exc.code,
                api_message=exc.message, at=self._clock.now(),
            ))
            return SendOutcome("REJECTED", str(client_ref), None, restored)
        except BrokerTimeout:
            return await self._resolve_unknown(
                cycle=cycle, config=config, stage=stage, pending=pending,
                client_ref=client_ref, is_buy=is_buy,
            )

        self._repo.update_order_log(
            client_ref=str(client_ref), status="ACCEPTED",
            broker_order_id=ack.broker_order_id,
        )
        return SendOutcome("ACCEPTED", str(client_ref), ack.broker_order_id,
                           pending)

    async def _resolve_unknown(
        self, *, cycle: Cycle, config: SplitConfig, stage: StageState,
        pending: StageState, client_ref: uuid.UUID, is_buy: bool,
    ) -> SendOutcome:
        """D12 — 재발주 금지. 조회로 접수 여부를 확인한다."""
        self._repo.update_order_log(
            client_ref=str(client_ref), status="UNKNOWN",
            api_message="응답 유실 — 당일 주문 조회로 확인 중",
        )
        self._emit(OrderUnknown(
            config_id=config.config_id, cycle_id=cycle.cycle_id,
            stage_no=stage.stage_no, client_ref=str(client_ref),
            at=self._clock.now(),
        ))
        try:
            orders = await self._broker.list_orders_today(config.stock_code)
        except BrokerError:
            # 확인 조회 자체가 실패했다. **되돌리지 않는다** — WAITING 으로
            # 복구하면 다음 틱에 재발주되고, 그것이 정확히 D12 가 막는 중복
            # 주문이다. order_log 는 UNKNOWN 으로, 단계는 PENDING 으로 남긴다:
            # 규칙 5 가 PENDING 단계를 판정에서 제외하므로 중복이 없고,
            # 재시작 복구(설계서 10.1절 2)가 같은 조회로 정정한다.
            return SendOutcome("UNKNOWN_UNRESOLVED", str(client_ref), None,
                               pending)
        found = next((o for o in orders if o.client_ref == client_ref), None)
        if found is not None:
            self._repo.update_order_log(
                client_ref=str(client_ref), status="ACCEPTED",
                broker_order_id=found.broker_order_id,
            )
            return SendOutcome("UNKNOWN_ACCEPTED", str(client_ref),
                               found.broker_order_id, pending)
        # 미접수 확인 — CANCELED 로 종결한다. REJECTED 는 브로커의 명시적
        # 판단(그리고 api_code)을 위해 남긴다. 두 경로는 사용자에게 다르게
        # 보여야 한다: 하나는 브로커가 판단한 것이고 하나는 도달하지 않은 것이다.
        self._repo.update_order_log(
            client_ref=str(client_ref), status="CANCELED",
            api_message="응답 유실 후 당일 주문 조회에서 미접수 확인",
            settled_at=self._clock.now(),
        )
        restored = self._restore(cycle, stage, pending, is_buy)
        return SendOutcome("UNKNOWN_NOT_SENT", str(client_ref), None, restored)

    def _restore(
        self, cycle: Cycle, original: StageState, pending: StageState,
        is_buy: bool,
    ) -> StageState:
        """발주 실패 후 단계를 원래 상태로 되돌린다.

        매도의 경우 `cancel_sell` 이 `remaining_qty` 를 요구한다. 발주 자체가
        실패했으므로 체결은 0 이고 원래 `fill_qty` 를 그대로 넘긴다 — 잘못
        넘기면 보유가 조용히 줄고, 그 수량이 이후 모든 목표가 계산의 근거가
        된다.
        """
        if is_buy:
            restored = stage_mod.cancel_buy(pending)
        else:
            restored = stage_mod.cancel_sell(pending,
                                             remaining_qty=original.fill_qty)
        self._repo.save_stage(cycle.cycle_id, restored)
        return restored
