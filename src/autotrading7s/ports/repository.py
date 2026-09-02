"""리포지토리 포트 — 설계서 12절 스키마의 접근면.

SQLite 구현(Task 8~10)이 이것을 만족한다. 엔진(Plan 2B)은 이 포트만 보므로,
저장 방식이 바뀌어도 엔진은 모른다.

메서드가 도메인 객체를 주고받는다 — 행이나 dict 가 아니다. 변환은 어댑터의 매핑
계층이 하며, 그곳이 Plan 1 이 넘긴 제약(완전한 단계 집합, trigger_price 대조,
tz-aware datetime)을 강제하는 지점이다.
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CloseReason, CycleStatus, OrderPath, Side


@dataclass(frozen=True, slots=True)
class SplitConfig:
    """분할 설정 — 설계서 12.1절 `split_config`.

    도메인에는 이 타입이 없다. 설정은 사용자 입력의 저장 형태이고, 도메인이 쓰는
    것은 그것으로 만든 `Ladder` 와 `TriggerParams` 다. 그렇다고 어댑터의 것도
    아니다 — **이 포트의 계약이 이 타입으로 쓰여 있으므로 포트와 함께 산다.**
    SQLite 어댑터든 다른 어떤 구현이든 이것을 가져다 쓴다.
    """

    config_id: int | None
    stock_code: str
    stock_name: str | None
    label: str | None
    max_stages: int
    drop_pct: Decimal
    target_pct: Decimal
    amount_per_stage: int
    allow_rebuy: bool
    rebuy_cooldown_sec: int
    total_limit: int
    status: str
    created_at: datetime
    updated_at: datetime

    def to_ladder(self, anchor_price: int) -> Ladder:
        """앵커가 확정된 뒤 이 설정으로 사다리를 만든다."""
        return Ladder(
            anchor_price=anchor_price,
            drop_pct=self.drop_pct,
            target_pct=self.target_pct,
            max_stages=self.max_stages,
            amount_per_stage=self.amount_per_stage,
        )


@dataclass(frozen=True, slots=True)
class HoldingRow:
    """설계서 12.3절 `holdings` 뷰의 한 행.

    현재가와 평가손익률은 실시간 값이므로 뷰에 없다 — UI 가 최신 틱과 결합해
    `domain/pnl.py` 의 순수 함수로 계산한다.
    """

    stock_code: str
    stock_name: str | None
    label: str | None
    cycle_id: int
    total_qty: int
    avg_price: int
    holding_stages: int
    max_stages: int
    cycle_status: CycleStatus


@runtime_checkable
class RepositoryPort(Protocol):
    # ── 설정 ────────────────────────────────────────────────────────────
    def save_config(self, config: SplitConfig) -> int:
        """새 설정을 저장하고 id 를 반환. 같은 (stock_code, label) 은 UNIQUE 로 거부."""
        ...

    def load_config(self, config_id: int) -> SplitConfig: ...

    def list_configs(self) -> list[SplitConfig]: ...

    def set_config_status(self, config_id: int, status: str) -> None:
        """IDLE | ACTIVE."""
        ...

    # ── 사이클과 단계 ───────────────────────────────────────────────────
    def create_cycle(self, config_id: int, started_at: datetime) -> Cycle:
        """seq 를 자동 증가시켜 STARTING 사이클을 만든다."""
        ...

    def load_cycle(self, cycle_id: int) -> Cycle: ...

    def save_cycle(self, cycle: Cycle) -> None: ...

    def load_active_cycles(self) -> list[Cycle]:
        """CLOSED 가 아닌 사이클. 재시작 복구(Plan 2B)가 쓴다."""
        ...

    def load_stages(self, cycle_id: int) -> list[StageState]:
        """사이클의 **모든** 단계. 개수가 max_stages 와 다르면 거부한다(H3).

        각 단계의 trigger_price 를 사이클의 ladder_json 과 대조한다(H4).
        """
        ...

    def save_stage(self, cycle_id: int, stage: StageState) -> None: ...

    # ── 주문 이력과 실현손익 ────────────────────────────────────────────
    def append_order_log(
        self, *, client_ref: str, cycle_id: int, stage_state_id: int | None,
        side: Side, order_type: str, path: OrderPath, req_price: int | None,
        req_qty: int, trigger_reason: str, tick_price: int | None,
        tick_source: str | None, sent_at: datetime,
    ) -> int:
        """status=SENDING 으로 기록하고 id 를 반환. 설계서 9절 ③."""
        ...

    def update_order_log(
        self, *, client_ref: str, status: str, broker_order_id: str | None = None,
        fill_price: int | None = None, fill_qty: int | None = None,
        api_code: str | None = None, api_message: str | None = None,
        settled_at: datetime | None = None,
    ) -> None: ...

    def load_pending_orders(self) -> list[dict[str, object]]:
        """SENDING·ACCEPTED·UNKNOWN 상태의 주문. 재시작 복구가 쓴다."""
        ...

    def realized_pnl_for_cycle(self, cycle_id: int) -> int:
        """order_log 에서 집계한 실현손익(H5).

        도메인에는 이 값이 없다 — after_sell 이 fill_price·fill_qty 를 비우므로
        단계 상태만으로는 계산할 수 없다.
        """
        ...

    # ── 긴급청산·대사 이력 ──────────────────────────────────────────────
    def append_emergency_log(
        self, *, scope: str, stock_code: str | None, cycle_id: int | None,
        requested_at: datetime, reason: str | None, qty_before: int | None,
        qty_after: int | None, canceled_orders: int | None, result: str,
        detail_json: str | None, completed_at: datetime | None,
    ) -> int: ...

    def append_reconcile_log(
        self, *, checked_at: datetime, stock_code: str, internal_qty: int,
        broker_qty: int, verdict: str, action_taken: str | None,
    ) -> int: ...

    # ── 보유현황 뷰 ─────────────────────────────────────────────────────
    def holdings(self) -> list[HoldingRow]:
        """설계서 12.3절의 holdings 뷰. 현재가·평가손익은 UI 가 최신 틱과 결합한다."""
        ...
