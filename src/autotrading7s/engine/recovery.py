"""재시작 복구 — 설계서 10.1절.

강제종료·정전·블루스크린 대비로 필수다. 죽어 있는 동안 체결된 주문을 놓치면
그 단계는 영원히 PENDING 이고, 규칙 5 가 판정에서 제외하므로 그 자본이 조용히
잠긴다.

```
1. DB에서 PENDING(BUY_PENDING / SELL_PENDING) 상태 단계 조회
2. list_orders_today()로 각 client_ref의 결말 확인
     체결됨    → HOLDING / WAITING 으로 정정
     취소·거부 → 원래 상태 복구
     기록 없음 → 원래 상태 복구 (전일 미체결은 장 마감에 자동 소멸)
3. get_balance()로 초기 동기화 대사 (불일치 시 경고, 정지하지는 않음)
4. RUNNING 사이클의 구독 복원 → 감시 재개
```

**3단계는 `Reconciler` 를 쓰지 않는다.** `Reconciler` 는 `INTERNAL_MORE` 에서
사이클을 `PAUSED` 로 만들지만, 설계서 10.1절 3 은 "정지하지는 않음" 을
명시한다. 재시작 직후의 불일치는 아직 정정되지 않은 주문 때문일 수 있으므로,
경고만 남기고 정지는 첫 정기 대사(10.2절)에 맡긴다.

**손상된 사이클은 격리하고 기동은 계속한다** (2A 핸드오버 7). `load_stages` 는
fail-closed 이고 복구 API 가 없으므로 단계 행 하나의 손상이 사이클 전체를
로드 불가로 만든다. 그것으로 프로그램이 기동 실패하면 사용자에게 나갈 길이
없고, 자동 손절매가 없는 프로그램에서 크래시 루프는 포지션을 방치하는 것과
같다.

**넓은 `except` 를 쓰지 않는다.** `CorruptRowError` 가 `ValueError` 의 하위이고
`ValueError` 를 넓게 잡으면 DB 손상을 삼킨다 — 잘못된 가격이 올라와도 조용히
넘어가고 그 가격으로 주문이 나간다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autotrading7s.app.events import CycleLoadFailed, Event, ReconcileMismatch
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import pnl
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import CycleStatus, FillState, StageStatus
from autotrading7s.engine.emergency import broker_qty
from autotrading7s.ports.broker import BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import CorruptRowError, RepositoryPort


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    resolved_orders: int
    restored_stages: int
    failed_cycles: tuple[int, ...]
    subscribe_codes: tuple[str, ...]


class Recovery:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: Callable[[Event], None],
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    async def run(self) -> RecoveryReport:
        resolved, restored = await self._resolve_pending_orders()
        failed, codes = await self._load_and_reconcile()
        return RecoveryReport(resolved, restored, tuple(failed), tuple(codes))

    async def _resolve_pending_orders(self) -> tuple[int, int]:
        """1~2단계. 당일 주문 조회로 각 미체결 주문의 결말을 확인한다."""
        resolved = restored = 0
        # 조회는 한 번만 한다. 루프 안에서 부르면 미체결 주문 수만큼 TR 호출이
        # 나가고, 기동 직후에 호출 제한에 걸릴 수 있다.
        orders = await self._broker.list_orders_today(None)
        by_ref = {str(o.client_ref): o for o in orders}
        for row in self._repo.load_pending_orders():
            if row.stage_no is None:
                # 긴급청산 주문 — 단계에 붙지 않는다. 그 결말은 사용자가
                # 긴급청산을 다시 시도할 때 정해지며, 여기서 단계를 정정할
                # 대상이 없다.
                continue
            try:
                stages = self._repo.load_stages(row.cycle_id)
            except CorruptRowError:
                continue          # 아래 _load_and_reconcile 이 격리한다
            stage = next(s for s in stages if s.stage_no == row.stage_no)
            if stage.status not in (StageStatus.BUY_PENDING,
                                    StageStatus.SELL_PENDING):
                continue          # 이미 정정됐다
            is_buy = stage.status is StageStatus.BUY_PENDING
            at = self._clock.now()
            found = by_ref.get(row.client_ref)

            if found is None or found.filled_qty == 0:
                # 기록 없음 / 취소 / 거부 → 원래 상태 복구. 전일 미체결은 장
                # 마감에 자동 소멸한다(한국 주식 주문은 당일에만 유효).
                self._repo.update_order_log(
                    client_ref=row.client_ref, status="CANCELED",
                    api_message="재시작 복구 — 체결 흔적 없음", settled_at=at,
                )
                back = (stage_mod.cancel_buy(stage) if is_buy
                        else stage_mod.cancel_sell(
                            stage, remaining_qty=stage.fill_qty))
                self._repo.save_stage(row.cycle_id, back)
                restored += 1
                continue

            terminal = ("FILLED" if found.state is FillState.FILLED
                        else "CANCELED")
            self._repo.update_order_log(
                client_ref=row.client_ref, status=terminal,
                broker_order_id=found.broker_order_id,
                fill_price=found.filled_price, fill_qty=found.filled_qty,
                settled_at=at,
            )
            config = self._repo.load_config(
                self._repo.load_cycle(row.cycle_id).config_id)
            if is_buy:
                applied = stage_mod.to_holding(
                    stage, fill_price=found.filled_price,
                    fill_qty=found.filled_qty, at=at)
            elif found.filled_qty >= stage.fill_qty:
                applied = stage_mod.after_sell(
                    stage, at=at, allow_rebuy=config.allow_rebuy)
            else:
                applied = stage_mod.cancel_sell(
                    stage, remaining_qty=stage.fill_qty - found.filled_qty)
            self._repo.save_stage(row.cycle_id, applied)
            resolved += 1
        return resolved, restored

    async def _load_and_reconcile(self) -> tuple[list[int], list[str]]:
        """3~4단계. 경고만 하고 정지하지 않는다 (설계서 10.1절 3)."""
        balance = await self._broker.get_balance()
        at = self._clock.now()
        failed: list[int] = []
        codes: list[str] = []
        for cyc in self._repo.load_active_cycles():
            config = self._repo.load_config(cyc.config_id)
            try:
                stages = self._repo.load_stages(cyc.cycle_id)
            except CorruptRowError as exc:
                # 격리는 **사이클**을 멈추는 것이다. RUNNING 일 때만 전이한다 —
                # STARTING 은 이미 트리거를 받지 않고(accepts_triggers False),
                # LIQUIDATING 을 되돌리면 진행 중인 긴급청산의 상태를
                # 프로그램이 뒤집는 것이 된다.
                action = None
                if cyc.status is CycleStatus.RUNNING:
                    self._repo.save_cycle(cycle_mod.pause(cyc))
                    action = "PAUSED"
                self._emit(CycleLoadFailed(
                    config_id=cyc.config_id, cycle_id=cyc.cycle_id,
                    detail=str(exc), action_taken=action, at=at,
                ))
                failed.append(cyc.cycle_id)
                continue
            internal = pnl.held_qty(stages)
            reported = broker_qty(balance, config.stock_code)
            baseline = self._repo.forced_close_baseline(config.stock_code)
            actual = (0 if reported is None else reported) - baseline
            if actual != internal:
                verdict = ("INTERNAL_LESS" if internal < actual
                           else "INTERNAL_MORE")
                self._repo.append_reconcile_log(
                    checked_at=at, stock_code=config.stock_code,
                    internal_qty=internal, broker_qty=actual,
                    verdict=verdict, action_taken=None,
                )
                self._emit(ReconcileMismatch(
                    stock_code=config.stock_code, internal_qty=internal,
                    broker_qty=actual, verdict=verdict, action_taken=None,
                    at=at,
                ))
            if cyc.status in (CycleStatus.RUNNING, CycleStatus.STARTING):
                codes.append(config.stock_code)
        return failed, codes
