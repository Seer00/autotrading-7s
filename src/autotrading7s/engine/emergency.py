"""긴급청산 — 설계서 11.1~11.3절.

**이 모듈은 가드를 거치지 않는다.** `engine/guards.py` 도 `domain/guards.py`
도 import 하지 않으며, 그 사실을 테스트가 고정한다. `max_orders_per_minute=0`
이 매도를 막게 되고, 그것은 손절 없는 전략의 유일한 탈출구에 레이트 리미터를
거는 것이다 (Plan 1 핸드오버 1).

순서(설계서 11.1절):

    ① 대상 사이클 → LIQUIDATING  (자동 트리거 즉시 정지)
    ② 해당 종목 미체결 주문 전량 취소
    ③ get_balance() 로 실계좌 실제 보유수량 확인
    ④ MarketSellRequest(qty=실계좌수량, reason) 발주
    ⑤ 체결 확인 → 전 단계 SOLD 일괄 갱신
    ⑥ emergency_liquidation_log 기록
    ⑦ 사이클 CLOSED(EMERGENCY) → 설정 IDLE

**②를 빠뜨리면 긴급청산이 무력화된다.** 전량 매도 직후 살아 있던 매수 지정가가
체결되면 방금 다 팔았는데 다시 보유가 생긴다. 급락 중이라면 매수 주문이 체결될
확률은 오히려 높다. "판다"는 명령은 "더 이상 사지 않는다"를 포함해야 한다.

**③에서 실계좌를 신뢰한다.** 긴급청산이 불리는 상황은 바로 "시스템 오작동이
의심되는" 상황이다. 그 순간에 오작동했을지도 모르는 내부 기록을 근거로 수량을
정하는 것은 자기모순이다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from autotrading7s.app.events import (
    EMERGENCY_RESULTS,
    CycleClosed,
    EmergencyResult,
    Event,
)
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import pnl
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import (
    Balance,
    CloseReason,
    CycleStatus,
    FillState,
    MarketSellRequest,
    OrderPath,
    Side,
)
from autotrading7s.ports.broker import BrokerError, BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import RepositoryPort, SplitConfig

FORCE_CLOSE_STATEMENT_KEY = "stage_remainders"


def broker_qty(balance: Balance, code: str) -> int | None:
    """실계좌 보유수량. **응답에 그 종목이 없으면 `None`.**

    `Balance.qty_of` 는 없는 종목에 0 을 반환한다 — 평가금액 산술에는 맞는
    답이지만 긴급청산에는 아니다. '응답에 없음'은 '보유 0'의 증거가 아니고,
    그 상태에서 사이클을 닫으면 실계좌에 주식이 남은 채 프로그램이 손을 뗀다
    (Plan 1 핸드오버 3).
    """
    for holding in balance.holdings:
        if holding.code == code:
            return holding.qty
    return None


@dataclass(frozen=True, slots=True)
class EmergencyOutcome:
    result: str
    stock_code: str | None
    qty_before: int | None
    qty_after: int | None
    canceled_orders: int
    detail: str | None

    def __post_init__(self) -> None:
        if self.result not in EMERGENCY_RESULTS:
            raise ValueError(f"unknown emergency result: {self.result!r}")


class EmergencyHandler:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: Callable[[Event], None],
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    # ── 11.1절 전체 청산 ────────────────────────────────────────────────
    async def liquidate_all(self, *, reason: str | None) -> list[EmergencyOutcome]:
        """전체 종목 청산 — **종목별 순차 처리**.

        병렬로 발주하면 TR 호출 제한에 걸려 일부가 조용히 실패할 수 있다.
        순차 처리하면 각 종목의 결과가 개별 로그로 남고 중간에 실패해도
        어디까지 됐는지 명확하다.
        """
        outcomes: list[EmergencyOutcome] = []
        for cyc in self._repo.load_active_cycles():
            outcomes.append(
                await self.liquidate_single(cyc.config_id, reason=reason,
                                            scope="ALL")
            )
        return outcomes

    async def liquidate_single(
        self, config_id: int, *, reason: str | None, scope: str = "SINGLE",
    ) -> EmergencyOutcome:
        requested_at = self._clock.now()
        config = self._repo.load_config(config_id)
        code = config.stock_code

        if not self._clock.is_market_open(requested_at):
            # D16 — 예약 청산은 시스템이 타이밍을 정하는 쪽이고, 사용자가
            # 예약을 잊으면 의도치 않은 청산이 발생한다. 요청 자체는 남긴다.
            return self._finish(
                scope=scope, code=code, cycle_id=None,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "REJECTED_CLOSED_MARKET", code, None, None, 0,
                    f"장 운영시간이 아니어서 시장가 매도를 실행할 수 "
                    f"없습니다 ({requested_at.isoformat()})",
                ),
            )

        cyc = self._active_cycle(config_id)
        if cyc is None:
            return self._finish(
                scope=scope, code=code, cycle_id=None,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome("FAILED", code, None, None, 0,
                                         "활성 사이클이 없습니다"),
            )

        # ① 자동 트리거 즉시 정지
        if cyc.status is not CycleStatus.LIQUIDATING:
            cyc = cycle_mod.begin_liquidation(cyc)
            self._repo.save_cycle(cyc)

        # ② 미체결 주문 전량 취소
        canceled = await self._cancel_open_orders(cyc.cycle_id)

        # ③ 실계좌 수량 확인
        balance = await self._broker.get_balance()
        actual = broker_qty(balance, code)
        stages = self._repo.load_stages(cyc.cycle_id)
        internal = pnl.held_qty(stages)

        if actual is None:
            return self._finish(
                scope=scope, code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "FAILED", code, None, None, canceled,
                    f"잔고 응답에 {code} 가 없습니다 — 보유 0 으로 단정할 수 "
                    f"없어 청산을 중단합니다 (내부 기록 {internal}주)",
                ),
            )
        if actual == 0:
            if internal > 0:
                return self._finish(
                    scope=scope, code=code, cycle_id=cyc.cycle_id,
                    requested_at=requested_at, reason=reason,
                    outcome=EmergencyOutcome(
                        "FAILED", code, 0, 0, canceled,
                        f"실계좌 보유 0 이지만 내부 기록 {internal}주 — "
                        f"강제 종료가 필요합니다 (설계서 11.4절)",
                    ),
                )
            return self._close(
                cyc=cyc, config=config, stages=stages, scope=scope,
                requested_at=requested_at, reason=reason, qty_before=0,
                sold=0, canceled=canceled,
            )

        # ④ 시장가 매도 — 기록이 발주보다 먼저 온다 (설계서 9절과 같은 논리)
        client_ref = uuid.uuid4()
        self._repo.append_order_log(
            client_ref=str(client_ref), cycle_id=cyc.cycle_id,
            stage_state_id=None, side=Side.SELL, order_type="MARKET",
            path=OrderPath.EMERGENCY, req_price=None, req_qty=actual,
            trigger_reason=reason or "긴급청산", tick_price=None,
            tick_source=None, sent_at=self._clock.now(),
        )
        req = MarketSellRequest(code=code, qty=actual, client_ref=client_ref,
                                reason=reason or "긴급청산")
        try:
            ack = await self._broker.place_market_sell(req)
        except BrokerError as exc:
            self._repo.update_order_log(
                client_ref=str(client_ref), status="REJECTED",
                api_message=str(exc), settled_at=self._clock.now(),
            )
            return self._finish(
                scope=scope, code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome("FAILED", code, actual, actual,
                                         canceled, f"시장가 매도 실패: {exc}"),
            )

        # ⑤ 체결 확인 — 한 번만 본다. 부분체결로 남으면 자동 재시도하지 않는다:
        # 급락 중 재시도 루프는 무한히 팔려 들 수 있고, 재시도인지 강제 종료인지
        # 는 사용자의 선택이다.
        status = await self._broker.get_order(ack.broker_order_id)
        terminal = "FILLED" if status.state is FillState.FILLED else "PARTIAL"
        self._repo.update_order_log(
            client_ref=str(client_ref), status=terminal,
            broker_order_id=ack.broker_order_id,
            fill_price=status.filled_price, fill_qty=status.filled_qty,
            settled_at=self._clock.now() if terminal == "FILLED" else None,
        )
        if status.filled_qty < actual:
            return self._finish(
                scope=scope, code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "PARTIAL", code, actual, actual - status.filled_qty,
                    canceled,
                    f"{actual}주 중 {status.filled_qty}주 체결 — 재시도 또는 "
                    f"강제 종료를 선택하세요",
                ),
            )
        return self._close(
            cyc=cyc, config=config, stages=stages, scope=scope,
            requested_at=requested_at, reason=reason, qty_before=actual,
            sold=status.filled_qty, canceled=canceled,
        )

    # ── 11.4절 강제 종료 ────────────────────────────────────────────────
    async def force_close(self, config_id: int, *, reason: str) -> EmergencyOutcome:
        """D20 강제 종료 — 설계서 11.4절.

        긴급청산이 끝까지 가지 못하는 상황(거래정지, 유동성 부재, 사용자가
        증권사 앱에서 직접 매도)에 사용자가 증언과 함께 호출한다. 설계서
        10.2절이 금지하는 것과 구분된다 — 10.2절이 금지하는 것은 **프로그램이**
        불일치를 조용히 만드는 것이고, 이것은 사용자의 의도적 개입이다.

        `LIQUIDATING` 에서만 호출된다. 사용자가 먼저 긴급청산을 시도해야 하고,
        그 시도 이력이 다이얼로그의 근거가 된다.
        """
        requested_at = self._clock.now()
        config = self._repo.load_config(config_id)
        code = config.stock_code
        cyc = self._active_cycle(config_id)
        if cyc is None or cyc.status is not CycleStatus.LIQUIDATING:
            status = cyc.status.value if cyc is not None else "없음"
            return self._finish(
                scope="SINGLE", code=code,
                cycle_id=None if cyc is None else cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "FAILED", code, None, None, 0,
                    f"강제 종료는 LIQUIDATING 에서만 가능합니다 (현재 "
                    f"{status}) — 긴급청산을 먼저 시도하세요 (설계서 11.4절)",
                ),
            )

        # ② 실계좌 잔고 재조회 — 11.1절 ③과 같은 이유
        balance = await self._broker.get_balance()
        actual = broker_qty(balance, code)
        stages = self._repo.load_stages(cyc.cycle_id)
        if actual is None:
            return self._finish(
                scope="SINGLE", code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "FAILED", code, None, None, 0,
                    f"잔고 응답에 {code} 가 없습니다 — 잔량을 모르는 채로 "
                    f"증언을 기록할 수 없습니다",
                ),
            )

        # ④ 미체결 취소 — 11.1절 ②과 같은 이유
        canceled = await self._cancel_open_orders(cyc.cycle_id)

        # ③ 잔량 0 → 정상 종료 경로. 실계좌가 비었으면 프로그램 관리 밖에
        # 남는 주식이 없으므로 FORCED 가 아니다.
        if actual == 0:
            closed = self._close(
                cyc=cyc, config=config, stages=stages, scope="SINGLE",
                requested_at=requested_at, reason=reason, qty_before=0,
                sold=0, canceled=canceled,
            )
            return EmergencyOutcome(
                closed.result, code, 0, 0, canceled,
                "실계좌 잔량이 0 이므로 강제 종료가 아니라 정상 종료로 "
                "처리했습니다 (설계서 11.4절 절차 ③)",
            )

        # ⑤⑥ 사이클과 단계를 한 트랜잭션에. 단계별 잔량은 상태를 SOLD 로
        # 덮으면 사라지므로 이력에만 남는다 — 사용자가 나중에 "어느 단계에
        # 얼마가 남았는지" 를 물을 수 있는 유일한 곳이다.
        at = self._clock.now()
        remainders = {str(s.stage_no): s.held_qty for s in stages
                      if s.held_qty > 0}
        sold_stages = [stage_mod.force_sold(s, at=at) for s in stages]
        forced = cycle_mod.force_close(cyc, reason=reason, qty=actual, at=at)
        self._repo.emergency_close_cycle(cycle=forced, stages=sold_stages)
        self._repo.set_realized_pnl(
            cyc.cycle_id, self._repo.realized_pnl_for_cycle(cyc.cycle_id)
        )
        self._repo.set_config_status(config_id, "IDLE", at=at)

        # ⑦ — qty_after 가 잔량 그대로인 것이 의도다. 강제 종료는 아무것도
        # 팔지 않으므로 종료 후에도 그 수량이 실계좌에 남아 있다. 0 으로
        # 기록하면 이력이 "다 팔았다" 고 말하게 되고, 그것이 설계서 11.4절이
        # 방지하려는 바로 그 거짓이다.
        completed_at = self._clock.now()
        self._repo.append_emergency_log(
            scope="SINGLE", stock_code=code, cycle_id=cyc.cycle_id,
            requested_at=requested_at, reason=reason, qty_before=actual,
            qty_after=actual, canceled_orders=canceled,
            result="FORCED_CLOSE",
            detail_json=json.dumps(
                {FORCE_CLOSE_STATEMENT_KEY: remainders, "broker_qty": actual},
                ensure_ascii=False,
            ),
            completed_at=completed_at,
        )
        outcome = EmergencyOutcome("FORCED_CLOSE", code, actual, actual,
                                   canceled, None)
        self._emit(EmergencyResult(
            scope="SINGLE", stock_code=code, result="FORCED_CLOSE",
            qty_before=actual, qty_after=actual, canceled_orders=canceled,
            detail=None, at=completed_at,
        ))
        self._emit(CycleClosed(
            config_id=config_id, cycle_id=cyc.cycle_id,
            reason=CloseReason.FORCED,
            realized_pnl=self._repo.realized_pnl_for_cycle(cyc.cycle_id),
            at=at,
        ))
        return outcome

    # ── 내부 ────────────────────────────────────────────────────────────
    def _active_cycle(self, config_id: int) -> Cycle | None:
        for cyc in self._repo.load_active_cycles():
            if cyc.config_id == config_id:
                return cyc
        return None

    async def _cancel_open_orders(self, cycle_id: int) -> int:
        """②. 취소 실패는 세지 않는다 — 살아 있는 주문이 있다는 사실이 이후
        대사에서 드러난다."""
        canceled = 0
        for row in self._repo.load_pending_orders():
            if row.cycle_id != cycle_id or row.broker_order_id is None:
                continue
            try:
                await self._broker.cancel_order(row.broker_order_id)
            except BrokerError:
                continue
            self._repo.update_order_log(
                client_ref=row.client_ref, status="CANCELED",
                api_message="긴급청산으로 취소 (설계서 11.1절 ②)",
                settled_at=self._clock.now(),
            )
            canceled += 1
        return canceled

    def _close(
        self, *, cyc: Cycle, config: SplitConfig,
        stages: Sequence[StageState], scope: str, requested_at: datetime,
        reason: str | None, qty_before: int, sold: int, canceled: int,
    ) -> EmergencyOutcome:
        """⑤⑦ — 전 단계를 SOLD 로 일괄 갱신하고 사이클을 닫는다.

        `emergency_close_cycle` 을 쓰는 이유: `force_sold` 는 전이표를
        우회하는데 `save_stage` 의 가드는 그 표를 참조한다. 사이클과 단계가
        한 트랜잭션에 써져야 절반만 청산된 상태가 남지 않는다.
        """
        at = self._clock.now()
        sold_stages = [stage_mod.force_sold(s, at=at) for s in stages]
        closed = cycle_mod.close(cyc, reason=CloseReason.EMERGENCY, at=at,
                                 states=sold_stages)
        self._repo.emergency_close_cycle(cycle=closed, stages=sold_stages)
        realized = self._repo.realized_pnl_for_cycle(cyc.cycle_id)
        self._repo.set_realized_pnl(cyc.cycle_id, realized)
        self._repo.set_config_status(config.config_id, "IDLE", at=at)
        outcome = EmergencyOutcome("SUCCESS", config.stock_code, qty_before,
                                   qty_before - sold, canceled, None)
        result = self._finish(scope=scope, code=config.stock_code,
                              cycle_id=cyc.cycle_id,
                              requested_at=requested_at, reason=reason,
                              outcome=outcome)
        self._emit(CycleClosed(
            config_id=config.config_id, cycle_id=cyc.cycle_id,
            reason=CloseReason.EMERGENCY, realized_pnl=realized, at=at,
        ))
        return result

    def _finish(
        self, *, scope: str, code: str | None, cycle_id: int | None,
        requested_at: datetime, reason: str | None,
        outcome: EmergencyOutcome,
    ) -> EmergencyOutcome:
        """⑥ — 모든 경로가 이력을 남기고 이벤트를 낸다.

        거부와 실패도 남긴다. 긴급청산은 사용자가 개입한 사건이므로 결과와
        무관하게 이력에 있어야 한다 (설계서 11.2절 전용 이력 로그).
        """
        completed_at = self._clock.now()
        self._repo.append_emergency_log(
            scope=scope, stock_code=code, cycle_id=cycle_id,
            requested_at=requested_at, reason=reason,
            qty_before=outcome.qty_before, qty_after=outcome.qty_after,
            canceled_orders=outcome.canceled_orders, result=outcome.result,
            detail_json=(None if outcome.detail is None
                         else json.dumps({"detail": outcome.detail},
                                         ensure_ascii=False)),
            completed_at=completed_at,
        )
        self._emit(EmergencyResult(
            scope=scope, stock_code=code, result=outcome.result,
            qty_before=outcome.qty_before, qty_after=outcome.qty_after,
            canceled_orders=outcome.canceled_orders, detail=outcome.detail,
            at=completed_at,
        ))
        return outcome
