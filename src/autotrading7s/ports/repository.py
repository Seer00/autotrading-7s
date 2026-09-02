"""리포지토리 포트 — 설계서 12절 스키마의 접근면.

SQLite 구현(Task 8~10)이 이것을 만족한다. 엔진(Plan 2B)은 이 포트만 보므로,
저장 방식이 바뀌어도 엔진은 모른다.

메서드가 도메인 객체를 주고받는다 — 행이나 dict 가 아니다. 변환은 어댑터의 매핑
계층이 하며, 그곳이 Plan 1 이 넘긴 제약(완전한 단계 집합, trigger_price 대조,
tz-aware datetime)을 강제하는 지점이다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, OrderPath, Side


class CorruptRowError(DomainInvariantError):
    """저장된 행에서 도메인 객체를 복원할 수 없다 — 테이블과 rowid 를 지목한다.

    호출자 버그(`TypeError`)와 구분되며, 엔진이 이것을 잡아 그 사이클만
    격리한다. 예외를 포트에 두는 이유는 브로커 예외와 같다: `engine/` 은
    `adapters/` 를 알지 못하므로, 예외가 어댑터에만 있으면 엔진은 넓은
    `except ValueError` 를 쓰게 되고 그것은 DB 손상을 삼킨다 (2A 핸드오버 7).
    """


class OrderLogNotFound(LookupError):
    """`update_order_log` 이 존재하지 않는 `client_ref` 를 갱신하려 할 때.

    조용히 아무 일도 하지 않으면 호출자는 브로커 응답이 기록됐다고 믿지만
    DB 는 영영 다른 상태로 남는다. `LookupError` 를 상속한다 — `ValueError`
    도 `TypeError` 도 아니므로 매핑 계층의 wrap/no-wrap 구분과 부딪치지
    않는다(매핑 계층은 이 예외를 보지 않는다: `update_order_log` 는 행을
    도메인 객체로 복원하지 않는다).
    """


class RowNotFound(LookupError):
    """이름으로 지정한 행이 존재하지 않을 때 갱신 메서드가 낸다.

    `save_cycle`·`set_config_status` 등, "존재하는 행을 갱신한다" 라고 약속한
    메서드가 조용히 0행을 갱신하고 성공한 것처럼 반환하면 호출자는 상태
    전이가 영속화됐다고 믿지만 DB 는 영영 다른 상태로 남는다 —
    `update_order_log` 를 `OrderLogNotFound` 로 강화한 것과 같은 이유다.
    테이블마다 예외를 따로 두지 않는다 — "이름 붙인 행이 없다" 는 모든
    테이블에서 같은 사건이다. `LookupError` 를 상속한다 — `ValueError` 도
    `TypeError` 도 아니므로 매핑 계층의 wrap/no-wrap 구분과 부딪치지 않는다.
    """


class OrderLogInvariantError(ValueError):
    """`update_order_log` 갱신이 주문 이력 자체의 불변식을 어길 때.

    세 가지를 막는다: 종결 상태(`FILLED`·`CANCELED`·`REJECTED`)에서 다른
    상태로의 역행, 이미 기록된 체결값(`fill_price`·`fill_qty`)을 다른 값으로
    덮어쓰는 것, `fill_qty` 가 `req_qty` 를 넘는 것.

    `DomainInvariantError` 를 상속하지 않는다 — `order_log` 는 도메인
    객체가 아니라 저장 형태이므로, 이 예외는 그 계층과 별개다.
    """


class StageInvariantError(ValueError):
    """`save_stage` 갱신이 저장된 단계 이력의 불변식을 어길 때(Fix Round 4).

    세 가지를 막는다: 이미 기록된 `fill_price` 를 다른 값으로 덮어쓰는 것,
    `fill_qty` 를 `SELL_PENDING → HOLDING` 축소(`cancel_sell`, 당일 유효한
    매도 잔량의 마감 취소 — 이전 계획의 과매도 결함을 고친 경로) 이외의
    방식으로 바꾸는 것(특히 어떤 전이에서든 증가시키는 것 — 과매도 방향),
    도메인의 전이표(`domain.stage._ALLOWED`)가 허용하지 않는 상태 전이로
    갱신하는 것. 도메인은 이 규칙들을 `to_holding`·`cancel_sell` 등
    도우미를 거칠 때만 강제한다 — `StageState` 를 직접 만들어 넘기면(이
    프로젝트 이력에서 가장 흔한 결함 유형) 우회된다. `update_order_log`
    가 `OrderLogInvariantError` 로 강화된 것과 같은 이유로, 저장소 경계가
    최종 방어선이 된다.

    `OrderLogInvariantError` 와 마찬가지로 `DomainInvariantError` 를
    상속하지 **않는다**. `StageState.__post_init__` 이 이미 단일 객체의
    필드 정합성(타입·양수 등)을 `DomainInvariantError` 로 강제하므로, 그와
    구분한다 — 이 예외가 막는 것은 단일 객체의 내부 정합성이 아니라 "이
    갱신이 이전에 저장된 행과 시계열로 맞는가" 라는 저장소 계층의 쓰기
    순서 불변식이다. 두 예외를 섞으면 넓은 `except DomainInvariantError`
    가 이 쓰기 순서 위반까지 삼켜, 어느 것이 손상되고 어느 것이 잘못된
    호출인지 구분할 수 없게 된다.
    """


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


@dataclass(frozen=True, slots=True)
class PendingOrderRow:
    """`load_pending_orders` 가 반환하는 한 행 — 재시작 복구가 쓴다.

    `load_pending_orders` 는 이 타입이 생기기 전에는 `dict(row)` 를 그대로
    돌려줬다 — `sent_at` 이 `str` 로, `side`·`path`·`status` 가 맨 문자열로
    새어나갔다. 다른 모든 읽기 경로는 `codec.text_to_dt` 를 거치므로 naive
    시각을 거부하는데, 이 메서드만 그 경계를 건너뛰었다. 복구 로직(Plan 2B)이
    바로 그 시각들로 쿨다운·타임아웃 산술을 하는 지점이므로, H2 가 지키려던
    실패가 여기서 재현된다.

    `status` 는 `str` 로 남긴다 — 스키마의 상태 어휘(`SENDING`·`ACCEPTED`·
    `PARTIAL`·`FILLED`·`CANCELED`·`REJECTED`·`UNKNOWN`)에는 대응하는 도메인
    enum 이 없다(`order_log` 는 도메인 객체로 복원되지 않는다).
    """

    order_log_id: int
    client_ref: str
    broker_order_id: str | None
    cycle_id: int
    stage_state_id: int | None
    side: Side
    path: OrderPath
    req_price: int | None
    req_qty: int
    fill_price: int | None
    fill_qty: int | None
    status: str
    sent_at: datetime


@runtime_checkable
class RepositoryPort(Protocol):
    # ── 설정 ────────────────────────────────────────────────────────────
    def save_config(self, config: SplitConfig) -> int:
        """새 설정을 저장하고 id 를 반환. 같은 (stock_code, label) 은 UNIQUE 로 거부."""
        ...

    def load_config(self, config_id: int) -> SplitConfig:
        """없는 `config_id` 는 `KeyError` 를 낸다.

        복원된 행이 도메인 불변식을 어기면(`Ladder` 로 만들 수 없는
        설정이면) `CorruptRowError` 를 낸다 — `DomainInvariantError` 의
        하위이며 그것은 `ValueError` 의 하위다. 엔진 루프의 넓은
        `except ValueError` 는 이것도 잡으므로, 그런 핸들러는 DB 손상을
        평범한 입력 오류와 구분하지 못하고 삼킬 수 있다.
        """
        ...

    def list_configs(self) -> list[SplitConfig]: ...

    def set_config_status(self, config_id: int, status: str, *, at: datetime) -> None:
        """IDLE | ACTIVE. 없는 `config_id` 는 `RowNotFound` 를 낸다.

        `at` 은 갱신 시각이다 — 벽시계를 직접 읽지 않는다. `ports/clock.py`
        가 존재하는 이유와 같다: "갭하락이 15:29에 오면?" 같은 시나리오가
        재현 가능해야 한다.
        """
        ...

    # ── 사이클과 단계 ───────────────────────────────────────────────────
    def create_cycle(self, config_id: int, started_at: datetime) -> Cycle:
        """seq 를 자동 증가시켜 STARTING 사이클을 만든다."""
        ...

    def load_cycle(self, cycle_id: int) -> Cycle:
        """없는 `cycle_id` 는 `KeyError` 를 낸다.

        복원된 행이 도메인 불변식을 어기면 `CorruptRowError` 를 낸다 —
        `DomainInvariantError`(→ `ValueError`)의 하위다. `load_config` 의
        같은 경고가 여기도 적용된다: 넓은 `except ValueError` 는 DB 손상을
        삼킨다.
        """
        ...

    def save_cycle(self, cycle: Cycle) -> None:
        """없는 `cycle.cycle_id` 는 `RowNotFound` 를 낸다."""
        ...

    def load_active_cycles(self) -> list[Cycle]:
        """CLOSED 가 아닌 사이클. 재시작 복구(Plan 2B)가 쓴다."""
        ...

    def load_stages(self, cycle_id: int) -> list[StageState]:
        """사이클의 **모든** 단계. 개수가 max_stages 와 다르면 거부한다(H3).

        각 단계의 trigger_price 를 사이클의 ladder_json 과 대조한다(H4).

        완전성·대조 실패는 `CorruptRowError` 를 낸다 — `DomainInvariantError`
        (→ `ValueError`)의 하위다. `load_config` 의 같은 경고가 여기도
        적용된다.
        """
        ...

    def stage_row_id(self, cycle_id: int, stage_no: int) -> int:
        """`stage_state` 행의 id. 없으면 `RowNotFound`.

        `order_log.stage_state_id` 를 채우기 위해 필요하다. 이 연결이 없으면
        재시작 복구가 미체결 주문이 어느 단계의 것인지 알 수 없고, 설계서
        10.1절 2단계('체결됨 → HOLDING 으로 정정')를 수행할 방법이 없다.
        """
        ...

    def save_stage(self, cycle_id: int, stage: StageState) -> None:
        """(cycle_id, stage_no) 로 upsert 한다.

        같은 단계에 이미 저장된 행이 있으면 `StageInvariantError` 를 낸다:
        이미 기록된 `fill_price` 를 다른 non-null 값으로 덮어쓰거나,
        `fill_qty` 를 `SELL_PENDING → HOLDING` 축소(당일 유효한 매도 잔량의
        마감 취소, `cancel_sell`) 이외의 방식으로 바꾸거나(특히 어떤
        전이에서든 증가시키거나), 도메인 전이표가 허용하지 않는 상태로
        옮기려 할 때. 같은 상태로의 재저장(매 틱의 정상 흐름)과 같은 값의
        재확인은 허용한다.
        """
        ...

    def emergency_close_cycle(
        self, *, cycle: Cycle, stages: Sequence[StageState]
    ) -> None:
        """긴급청산·강제 종료의 원자적 쓰기 — 설계서 11.1절 ⑤⑦, 11.4절 ⑤⑥.

        `close_reason` 이 `EMERGENCY` 이거나 `FORCED` 인 사이클만 받는다.
        정상 종료는 이 문을 쓸 수 없다 — 정상 경로는 `save_stage` 의 가드와
        `close()` 의 보유 0 검사를 통과해야 한다.

        `save_stage` 를 쓰지 않는 이유: `force_sold` 는 전이표를 의도적으로
        우회하는데 `save_stage` 의 가드는 그 표를 참조한다. 우회 플래그를 두면
        가드가 막고 있는 모든 것(체결값 덮어쓰기, 상태 역행)이 그 문으로
        들어온다. 그래서 전용 경로를 두고 입력을 엄격히 검사한다.

        원자적이어야 하는 이유: 절반만 청산된 상태 — 사이클은 CLOSED 인데
        단계가 HOLDING 으로 남거나 그 반대 — 는 어느 경로로도 정리할 수 없다.
        """
        ...

    def set_realized_pnl(self, cycle_id: int, value: int) -> None:
        """사이클 종료 시 `realized_pnl_for_cycle` 의 값을 기록한다.

        `cycle_to_row` 가 이 컬럼을 의도적으로 제외하므로(도메인 `Cycle` 에
        그 필드가 없다) 전용 경로가 필요하다.
        """
        ...

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
    ) -> None:
        """존재하지 않는 `client_ref` 는 `OrderLogNotFound` 를 낸다.

        종결 상태에서의 역행, 이미 기록된 체결값의 덮어쓰기, `req_qty` 를 넘는
        `fill_qty` 는 `OrderLogInvariantError` 를 낸다.

        **`fill_qty` 는 누적값이다, 증분이 아니다.** 매 호출의 `fill_qty` 는
        그 주문이 지금까지 체결한 총 수량이어야 한다(부분체결이 이어질 때도
        마찬가지) — 이전 값에 더할 값이 아니다. **`fill_price` 는 지금까지
        모든 체결의 수량가중평균가다**, 마지막 체결의 가격이 아니다. 이
        약속은 `BrokerPort.get_order`/`OrderStatus.filled_qty` 가 보고하는
        값과 같은 것이어야 한다 — 브로커가 보고하는 그대로 여기 넘기면
        맞아야 한다.

        이 약속이 지켜지지 않으면 `realized_pnl_for_cycle` 이 틀린다 — 그
        메서드는 `fill_price * fill_qty` 를 취득/처분 금액으로 직접 쓴다.
        증분으로 잘못 채우면 총 매수량이 실제보다 커져 원가가 과소평가되고,
        실현손익이 실제보다 크게 나온다 — 이 프로젝트가 이미 겪은 가장 심각한
        결함(+399,200원 대 실제 +19,200원)과 같은 방향·같은 모양의 오류다.
        """
        ...

    def load_pending_orders(self) -> list[PendingOrderRow]:
        """SENDING·ACCEPTED·PARTIAL·UNKNOWN 상태의 주문. 재시작 복구가 쓴다."""
        ...

    def realized_pnl_for_cycle(self, cycle_id: int) -> int:
        """order_log 에서 집계한 실현손익(H5).

        도메인에는 이 값이 없다 — after_sell 이 fill_price·fill_qty 를 비우므로
        단계 상태만으로는 계산할 수 없다. 체결 데이터(fill_price·fill_qty)를
        기준으로 집계한다 — status 가 아니다. status 는 주문의 생애가 어디서
        끝났는지를 말하고, 체결 데이터는 실제로 무엇이 오갔는지를 말한다.
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

    def forced_close_baseline(self, stock_code: str) -> int:
        """이 종목에서 강제 종료된 누적 수량 — 마지막 기준선 초기화 이후만.

        설계서 11.4절: 강제 종료된 수량을 대사 기준에서 제외해야 하고, 그
        제외는 영구적이지 않아야 한다.
        """
        ...

    def reset_forced_close_baseline(
        self, stock_code: str, *, at: datetime
    ) -> None:
        """사용자가 남은 주식을 처리했다고 알린 시점을 기록한다 (설계서 11.4절)."""
        ...

    # ── 보유현황 뷰 ─────────────────────────────────────────────────────
    def holdings(self) -> list[HoldingRow]:
        """설계서 12.3절의 holdings 뷰. 현재가·평가손익은 UI 가 최신 틱과 결합한다."""
        ...
