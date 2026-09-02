"""SQLite 리포지토리 — `RepositoryPort` 의 구현.

메서드가 도메인 객체를 주고받는다. 변환과 제약 강제는 `mapping` 이 하며, 이 모듈은
SQL 과 트랜잭션 경계만 다룬다.

`load_stages` 는 사이클을 먼저 로드해 사다리를 얻은 뒤 `rows_to_stages` 에 넘긴다.
사이클 없이 단계만 로드하는 경로를 두지 않는다 — 그러면 H4 를 검사할 기준이 없다.

`mapping` 의 변환 함수는 `Mapping[str, Any]`(`.get()` 을 쓴다)를 기대하지만
`connect()` 의 `row_factory` 인 `sqlite3.Row` 는 `.get()` 이 없다 — 그래서 이
모듈은 `mapping` 에 넘기기 전에 `dict(row)` 로 변환한다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from autotrading7s.adapters.sqlite.codec import dt_to_text, text_to_dt
from autotrading7s.adapters.sqlite.mapping import (
    config_to_row,
    cycle_to_row,
    row_to_config,
    row_to_cycle,
    rows_to_stages,
    stage_to_row,
)
from autotrading7s.ports.repository import (
    HoldingRow,
    OrderLogInvariantError,
    OrderLogNotFound,
    PendingOrderRow,
    RowNotFound,
    SplitConfig,
    StageInvariantError,
)
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.stage import _ALLOWED as _STAGE_TRANSITIONS
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    OrderPath,
    Side,
    StageStatus,
)


class SqliteRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ── 설정 ────────────────────────────────────────────────────────────
    def save_config(self, config: SplitConfig) -> int:
        row = config_to_row(config)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        with self._conn:
            cursor = self._conn.execute(
                f"INSERT INTO split_config ({columns}) VALUES ({placeholders})", row
            )
        return int(cursor.lastrowid)

    def load_config(self, config_id: int) -> SplitConfig:
        row = self._conn.execute(
            "SELECT * FROM split_config WHERE id = ?", (config_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no split_config with id {config_id}")
        return row_to_config(dict(row))

    def list_configs(self) -> list[SplitConfig]:
        rows = self._conn.execute(
            "SELECT * FROM split_config ORDER BY id"
        ).fetchall()
        return [row_to_config(dict(r)) for r in rows]

    def set_config_status(self, config_id: int, status: str, *, at: datetime) -> None:
        """없는 `config_id` 는 `RowNotFound` 를 낸다(Fix Round 3).

        `at` 을 호출자가 넘긴다 — 벽시계(`datetime.now()`)를 읽지 않는다.
        `src/` 전체에서 유일했던 `datetime.now()` 호출이 여기 있었다; 다른
        모든 메서드는 시각을 매개변수로 받고, `ports/clock.py` 가 존재하는
        것도 "갭하락이 15:29 에 오면?" 같은 시나리오를 재현 가능하게 하기
        위해서다.
        """
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE split_config SET status = ?, updated_at = ? WHERE id = ?",
                (status, dt_to_text(at), config_id),
            )
            if cursor.rowcount == 0:
                raise RowNotFound(f"no split_config row with id={config_id}")

    # ── 사이클 ──────────────────────────────────────────────────────────
    def create_cycle(self, config_id: int, started_at: datetime) -> Cycle:
        with self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM cycle "
                "WHERE config_id = ?", (config_id,)
            ).fetchone()
            seq = int(row["next_seq"])
            cursor = self._conn.execute(
                "INSERT INTO cycle (config_id, seq, status, started_at) "
                "VALUES (?, ?, ?, ?)",
                (config_id, seq, CycleStatus.STARTING.value, dt_to_text(started_at)),
            )
        return Cycle(
            cycle_id=int(cursor.lastrowid), config_id=config_id, seq=seq,
            status=CycleStatus.STARTING, started_at=started_at,
        )

    def load_cycle(self, cycle_id: int) -> Cycle:
        row = self._conn.execute(
            "SELECT * FROM cycle WHERE id = ?", (cycle_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no cycle with id {cycle_id}")
        return row_to_cycle(dict(row))

    def save_cycle(self, cycle: Cycle) -> None:
        """`cycle_to_row` 가 다루는 컬럼만 갱신한다. 없는 id 는 `RowNotFound`.

        `realized_pnl` 은 Task 9(주문 이력·실현손익)가 채운다.

        **D20 의 `forced_close_reason`·`forced_close_qty` 는 이 메서드로 쓸 수
        없다.** `Cycle` 에 그 두 값을 담을 필드가 없고 `cycle_to_row` 도 그
        컬럼을 다루지 않으므로, `close_reason=FORCED` 인 `Cycle` 을 넘기면
        스키마의 D20 `CHECK` (증언과 잔량이 둘 다 있어야 한다)가 거부한다 —
        `sqlite3.IntegrityError` 로 실패한다. Plan 2B 가 강제 종료 전이를
        설계할 때 도메인 필드와 이 컬럼들의 쓰기 경로를 함께 추가해야 한다
        (Fix Round 3 — 계약을 그 소비자보다 먼저 고정하지 않기 위해 이번
        라운드에서는 일부러 추가하지 않았다).
        """
        row = cycle_to_row(cycle)
        assignments = ", ".join(f"{k} = :{k}" for k in row)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE cycle SET {assignments} WHERE id = :id",
                row | {"id": cycle.cycle_id},
            )
            if cursor.rowcount == 0:
                raise RowNotFound(f"no cycle row with id={cycle.cycle_id}")

    def load_active_cycles(self) -> list[Cycle]:
        rows = self._conn.execute(
            "SELECT * FROM cycle WHERE status != ? ORDER BY id",
            (CycleStatus.CLOSED.value,),
        ).fetchall()
        return [row_to_cycle(dict(r)) for r in rows]

    # ── 단계 ────────────────────────────────────────────────────────────
    def load_stages(self, cycle_id: int) -> list[StageState]:
        cycle = self.load_cycle(cycle_id)
        rows = self._conn.execute(
            "SELECT * FROM stage_state WHERE cycle_id = ? ORDER BY stage_no",
            (cycle_id,),
        ).fetchall()
        return rows_to_stages(
            [dict(r) for r in rows], cycle_id=cycle_id, ladder=cycle.ladder
        )

    def stage_row_id(self, cycle_id: int, stage_no: int) -> int:
        """`stage_state` 행의 id — `order_log.stage_state_id` 를 채우는 데 쓴다.

        사이클로 범위를 좁히는 것이 중요하다. `UNIQUE(cycle_id, stage_no)` 가
        있으므로 두 열이 함께여야 행 하나를 지목하며, `stage_no` 만으로 찾으면
        다른 사이클의 단계에 주문이 붙는다.
        """
        row = self._conn.execute(
            "SELECT id FROM stage_state WHERE cycle_id = ? AND stage_no = ?",
            (cycle_id, stage_no),
        ).fetchone()
        if row is None:
            raise RowNotFound(
                f"no stage_state row for cycle_id={cycle_id} stage_no={stage_no}"
            )
        return int(dict(row)["id"])

    def save_stage(self, cycle_id: int, stage: StageState) -> None:
        """(cycle_id, stage_no) 로 upsert. 없는 행이면 그냥 만든다(초기 저장).

        기존 행이 있으면 세 불변식을 지킨다(Fix Round 4 — `update_order_log`
        와 같은 이유. `StageState` 는 직접 만들 수 있으므로, 도메인의
        전이표는 `to_holding` 등 도우미를 거칠 때만 강제되고 그 도우미를
        건너뛰면 우회된다 — 이 프로젝트 이력에서 가장 흔한 결함이 정확히
        그 우회였다):

        1. **`fill_price` 절대 불변.** 저장된 값이 non-null 이고 새 값도
           non-null 이며 서로 다르면 `StageInvariantError`. 도메인의 다섯
           전이 도우미(`to_holding`·`to_sell_pending`·`cancel_sell`·
           `after_sell`·`cancel_buy`) 중 `fill_price` 를 바꾸는 것은 하나도
           없다 — 처음 쓰거나(`to_holding`), 그대로 두거나, 지운다.
        2. **`fill_qty` 는 `SELL_PENDING → HOLDING` 전이에서만, 그리고
           내려가는 방향으로만 바뀔 수 있다.** `cancel_sell` 이 이 경로다 —
           한국 주식 주문은 당일에만 유효하므로, 부분체결된 매도 주문의
           미체결 잔량이 마감과 함께 취소되면 보유 수량이 줄어든 채
           `HOLDING` 으로 돌아간다(`domain.stage.cancel_sell` 의 표현으로
           "일상적인 경로"). 이 축소는 이전 계획의 결함(마감에 취소된
           매도 잔량의 `fill_qty` 가 갱신되지 않아 과매도로 이어짐)의
           수정이므로 이 메서드가 막으면 안 된다. 그 한 전이·그 방향
           외에는 `fill_price` 와 같은 절대 불변 규칙이 적용된다 — 특히
           **증가는 어느 전이에서도 거부한다**(과매도 방향이며, 보안
           재검토가 처음 지목한 위험이 바로 이 방향이다).
        3. **전이 합법성.** 저장된 상태와 새 상태가 다르면, 그 전이가
           `domain.stage._ALLOWED`(도메인이 이미 가진 표 — 여기서 다시
           베끼면 둘이 어긋날 수 있다)에 있어야 한다. 같은 상태로의
           재저장(매 틱의 정상 흐름)은 항상 허용한다.

        `force_sold`(긴급청산 전용, 전이표를 의도적으로 우회한다)로 만든
        `StageState` 를 이 메서드로 저장하는 경로는 아직 없다 — 그런 저장은
        이 가드에 걸린다. Plan 2B 의 긴급청산 쓰기 경로가 이 메서드를 그대로
        쓸지, 다른 경로를 둘지는 그 설계가 결정할 문제다.
        """
        row = stage_to_row(cycle_id, stage)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        updates = ", ".join(
            f"{k} = :{k}" for k in row if k not in ("cycle_id", "stage_no")
        )
        with self._conn:
            current = self._conn.execute(
                "SELECT status, fill_price, fill_qty FROM stage_state "
                "WHERE cycle_id = ? AND stage_no = ?",
                (cycle_id, stage.stage_no),
            ).fetchone()
            if current is not None:
                current_status = StageStatus(current["status"])
                if stage.status is not current_status:
                    allowed = _STAGE_TRANSITIONS.get(current_status, frozenset())
                    if stage.status not in allowed:
                        raise StageInvariantError(
                            f"stage {stage.stage_no} of cycle {cycle_id}: "
                            f"{current_status.value} → {stage.status.value} "
                            "는 허용되지 않는 전이"
                        )

                stored_price = current["fill_price"]
                incoming_price = stage.fill_price
                if (stored_price is not None and incoming_price is not None
                        and stored_price != incoming_price):
                    raise StageInvariantError(
                        f"stage {stage.stage_no} of cycle {cycle_id}: "
                        f"fill_price already {stored_price!r}; refusing to "
                        f"overwrite with {incoming_price!r}"
                    )

                stored_qty = current["fill_qty"]
                incoming_qty = stage.fill_qty
                if (stored_qty is not None and incoming_qty is not None
                        and stored_qty != incoming_qty):
                    # cancel_sell 의 잔량 취소 — 당일 유효한 매도 주문의
                    # 미체결 잔량이 마감과 함께 취소되면 SELL_PENDING 에서
                    # HOLDING 으로 돌아가되 보유 수량이 줄어든다. 이 한
                    # 전이·이 방향만 예외다. 증가는(어느 전이에서든) 과매도
                    # 방향이므로 항상 거부한다.
                    is_the_sell_cancel_shrink = (
                        current_status is StageStatus.SELL_PENDING
                        and stage.status is StageStatus.HOLDING
                        and 0 < incoming_qty < stored_qty
                    )
                    if not is_the_sell_cancel_shrink:
                        raise StageInvariantError(
                            f"stage {stage.stage_no} of cycle {cycle_id}: "
                            f"fill_qty already {stored_qty!r}; refusing to "
                            f"overwrite with {incoming_qty!r}"
                        )

            self._conn.execute(
                f"INSERT INTO stage_state ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(cycle_id, stage_no) DO UPDATE SET {updates}",
                row,
            )

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
        들어온다. 그래서 전용 경로를 두고 **입력을 엄격히 검사한다.**

        원자적이어야 하는 이유: 절반만 청산된 상태 — 사이클은 CLOSED 인데
        단계가 HOLDING 으로 남거나 그 반대 — 는 어느 경로로도 정리할 수 없다.

        검사가 `with self._conn:` **앞에** 있는 이유: 부분 실행 자체가 없어야
        한다. 트랜잭션 안에서 검사하면 롤백에 의존하게 된다.
        """
        if cycle.close_reason not in (CloseReason.EMERGENCY, CloseReason.FORCED):
            raise ValueError(
                f"emergency_close_cycle requires close_reason EMERGENCY or "
                f"FORCED, got {cycle.close_reason} — 정상 종료는 save_stage 의 "
                f"가드를 통과해야 한다"
            )
        not_sold = [s.stage_no for s in stages
                    if s.status is not StageStatus.SOLD]
        if not_sold:
            raise StageInvariantError(
                f"emergency_close_cycle requires every stage to be SOLD; "
                f"stages {not_sold} are not"
            )
        # 완전성은 **사이클이 가진 단계 수**와 비교해야 한다. 넘겨받은 목록의
        # 길이와 비교하면 연속성만 확인하게 되고, 7단계 사이클에 1~3 만 쓰는
        # 것이 통과한다 — 그러면 이후 `load_stages` 가 H3 로 그 사이클을
        # 로드하지 못해 사용자가 손댈 수 없는 상태가 된다.
        if cycle.ladder is not None:
            total = cycle.ladder.max_stages
        else:
            # 긴급청산은 앵커가 생기기 전(STARTING)에도 시작할 수 있으므로
            # 사다리가 없을 수 있다 (설계서 11.1절). 그때는 저장된 행 수가
            # 유일한 기준이다.
            row = self._conn.execute(
                "SELECT count(*) AS n FROM stage_state WHERE cycle_id = ?",
                (cycle.cycle_id,),
            ).fetchone()
            total = int(dict(row)["n"])
        expected = set(range(1, total + 1))
        if {s.stage_no for s in stages} != expected:
            raise StageInvariantError(
                f"emergency_close_cycle requires the complete stage set "
                f"1..{total}, got {sorted(s.stage_no for s in stages)}"
            )
        row = cycle_to_row(cycle)
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE cycle SET status = :status, "
                " close_reason = :close_reason, closed_at = :closed_at, "
                " forced_close_reason = :forced_close_reason, "
                " forced_close_qty = :forced_close_qty "
                "WHERE id = :id",
                row | {"id": cycle.cycle_id},
            )
            if cursor.rowcount == 0:
                raise RowNotFound(f"no cycle row with id={cycle.cycle_id}")
            for stage in stages:
                self._conn.execute(
                    "UPDATE stage_state SET status = :status, "
                    " fill_price = :fill_price, fill_qty = :fill_qty, "
                    " bought_at = :bought_at, last_sold_at = :last_sold_at, "
                    " rebuy_count = :rebuy_count "
                    "WHERE cycle_id = :cycle_id AND stage_no = :stage_no",
                    stage_to_row(cycle.cycle_id, stage),
                )

    def set_realized_pnl(self, cycle_id: int, value: int) -> None:
        """사이클 종료 시 엔진이 `realized_pnl_for_cycle` 의 값을 기록한다.

        `cycle_to_row` 가 이 컬럼을 의도적으로 제외하므로(도메인 `Cycle` 에
        그 필드가 없다) 전용 경로가 필요하다.
        """
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE cycle SET realized_pnl = ? WHERE id = ?",
                (value, cycle_id),
            )
            if cursor.rowcount == 0:
                raise RowNotFound(f"no cycle with id {cycle_id}")

    # ── 주문 이력 ───────────────────────────────────────────────────────
    # PARTIAL 은 대기(pending) 쪽에 있어야 한다 — 부분체결 후 잔량이 여전히
    # 브로커에서 살아있다. 이걸 빼면 재시작 복구가 그 주문을 못 보고, 실제로
    # 진행 중인 주문이 유령처럼 사라진다(Fix Round 3 재현: PARTIAL·40주 체결·
    # 105주 요청 후 load_pending_orders() 가 빈 목록을 반환했다).
    _PENDING_STATUSES = ("SENDING", "ACCEPTED", "PARTIAL", "UNKNOWN")

    def append_order_log(
        self, *, client_ref: str, cycle_id: int, stage_state_id: int | None,
        side: Side, order_type: str, path: OrderPath, req_price: int | None,
        req_qty: int, trigger_reason: str, tick_price: int | None,
        tick_source: str | None, sent_at: datetime,
    ) -> int:
        """status=SENDING 으로 기록한다. 설계서 9절 ③ — 발주보다 먼저 커밋한다.

        순서를 뒤집으면 발주와 기록 사이에 프로세스가 죽었을 때 브로커에는 주문이
        있는데 우리는 모르는 고아 주문이 생기고, 다음 실행에서 중복 발주가 된다.
        """
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO order_log (client_ref, cycle_id, stage_state_id, "
                " side, order_type, path, req_price, req_qty, status, "
                " trigger_reason, tick_price, tick_source, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SENDING', ?, ?, ?, ?)",
                (client_ref, cycle_id, stage_state_id, side.value, order_type,
                 path.value, req_price, req_qty, trigger_reason, tick_price,
                 tick_source, dt_to_text(sent_at)),
            )
        return int(cursor.lastrowid)

    # 종결 상태 — 이 상태에서 다른 상태로 되돌아갈 수 없다(Fix Round 1 Finding 4).
    # PARTIAL 은 종결이 아니다: 설계서 9절이 규정한 정상 흐름
    # (PARTIAL → FILLED, PARTIAL → CANCELED, 특히 부분체결 매수의 잔량 취소)
    # 이 계속 동작해야 한다.
    _TERMINAL_STATUSES = ("FILLED", "CANCELED", "REJECTED")

    def update_order_log(
        self, *, client_ref: str, status: str, broker_order_id: str | None = None,
        fill_price: int | None = None, fill_qty: int | None = None,
        api_code: str | None = None, api_message: str | None = None,
        settled_at: datetime | None = None,
    ) -> None:
        """`client_ref` 로 갱신하고 사전조건 하나와 불변식 세 가지를 지킨다
        (Fix Round 1).

        0. **존재(사전조건).** 없는 `client_ref` 는 `OrderLogNotFound` 를
           낸다 — 조용히 아무 일도 하지 않으면 호출자는 브로커 응답이 기록됐다고
           믿지만 DB 는 영영 다른 상태로 남는다.

        1. **종결 상태 불역행.** 이미 `FILLED`·`CANCELED`·`REJECTED` 인 행을
           다른 상태로 되돌릴 수 없다 — 늦거나 중복된 브로커 응답이 실제 체결을
           지우면 실현손익이 조용히 틀려진다. **같은 상태로의 재확인(멱등
           재시도)은 허용한다** — 설계서 9절의 UNKNOWN 재조회 절차와 Plan 2B 의
           대사 로직이 안전하게 재시도할 수 있어야 한다. `PARTIAL` 에서는 이
           제약이 적용되지 않는다 — `PARTIAL → FILLED`·`PARTIAL → CANCELED`
           둘 다 정상 흐름이다.

        2. **체결값 불변.** 종결 상태에 이미 기록된 `fill_price`·`fill_qty` 를
           다른 값으로 덮어쓸 수 없다 — 재전송된 다른 체결 데이터가 실현손익을
           조용히 바꾸는 것을 막는다. 같은 값의 재확인과 `None`(값 유지, 기존
           `COALESCE` 동작)은 허용한다. `PARTIAL` 은 아직 누적 중이므로(예:
           `PARTIAL → FILLED` 가 최종 수량으로 갱신) 이 제약이 적용되지 않는다.

        3. **수량 상한.** `fill_qty`(신규 값이 없으면 기존 값)가 `req_qty` 를
           넘으면 `OrderLogInvariantError` 를 낸다 — 브로커 데이터의 손상이며
           조용히 받아들이면 실현손익이 틀려진다.

        세 검사 모두 같은 `with self._conn:` 블록 안에서 현재 행을 읽어
        판단한다. **이 확인-후-갱신이 안전한 것은 SQLite 가 트랜잭션을
        직렬화해서가 아니다** — Python `sqlite3` 는 `SELECT` 앞에서는 암묵적
        트랜잭션을 열지 않는다(`BEGIN` 은 DML 앞에서만 열린다), 그래서 이
        읽기는 쓰기 잠금 밖에서 실행된다. 안전한 이유는 이 아키텍처의 두
        전제다: (1) 설계서 7.1절(D7) — 단일 프로세스이고 GUI(Tkinter 메인
        스레드)는 큐(`command_q`·`priority_q`·`event_q`)로만 엔진과
        통신하며 DB 를 직접 건드리지 않는다. 모든 order_log 쓰기는 엔진
        스레드의 단일 연결(`self._conn`)에서만 일어나므로 두 번째 쓰기
        연결이 없다. (2) 이 메서드는 `await` 지점이 없는 동기 메서드다 —
        엔진 스레드의 다섯 개 동시 asyncio 태스크(명령 소비·시세 수신·트리거
        평가·미체결 감시·잔고 대사)는 하나의 이벤트 루프에서 협력적으로
        돌아가므로, 양보 지점이 없는 메서드 내부로는 끼어들 수 없다.

        **이 두 전제가 사라지면 이 코드는 더 이상 안전하지 않다.** 두 번째
        쓰기 연결이 생기면(예: 다른 프로세스나 스레드가 같은 DB 파일에 쓰면)
        이 검사는 더 이상 원자적이지 않다 — 그때는 가드를 `UPDATE` 문 자신의
        `WHERE` 절로 옮기거나, 트랜잭션을 `BEGIN IMMEDIATE` 로 열어 쓰기
        잠금을 읽기 시점부터 잡아야 한다.
        """
        with self._conn:
            row = self._conn.execute(
                "SELECT status, fill_price, fill_qty, req_qty FROM order_log "
                "WHERE client_ref = ?",
                (client_ref,),
            ).fetchone()
            if row is None:
                raise OrderLogNotFound(
                    f"no order_log row with client_ref={client_ref!r}"
                )
            current = dict(row)

            if current["status"] in self._TERMINAL_STATUSES:
                if status != current["status"]:
                    raise OrderLogInvariantError(
                        f"order {client_ref!r} is already terminal "
                        f"({current['status']!r}); refusing to move it to "
                        f"{status!r}"
                    )
                if (fill_price is not None and current["fill_price"] is not None
                        and fill_price != current["fill_price"]):
                    raise OrderLogInvariantError(
                        f"order {client_ref!r} already settled with "
                        f"fill_price={current['fill_price']!r}; refusing to "
                        f"overwrite with {fill_price!r}"
                    )
                if (fill_qty is not None and current["fill_qty"] is not None
                        and fill_qty != current["fill_qty"]):
                    raise OrderLogInvariantError(
                        f"order {client_ref!r} already settled with "
                        f"fill_qty={current['fill_qty']!r}; refusing to "
                        f"overwrite with {fill_qty!r}"
                    )

            effective_qty = fill_qty if fill_qty is not None else current["fill_qty"]
            if effective_qty is not None and effective_qty > current["req_qty"]:
                raise OrderLogInvariantError(
                    f"order {client_ref!r} fill_qty {effective_qty} exceeds "
                    f"req_qty {current['req_qty']}"
                )

            self._conn.execute(
                "UPDATE order_log SET status = ?, "
                " broker_order_id = COALESCE(?, broker_order_id), "
                " fill_price = COALESCE(?, fill_price), "
                " fill_qty = COALESCE(?, fill_qty), "
                " api_code = COALESCE(?, api_code), "
                " api_message = COALESCE(?, api_message), "
                " settled_at = COALESCE(?, settled_at) "
                "WHERE client_ref = ?",
                (status, broker_order_id, fill_price, fill_qty, api_code,
                 api_message,
                 None if settled_at is None else dt_to_text(settled_at),
                 client_ref),
            )

    def load_pending_orders(self) -> list[PendingOrderRow]:
        """SENDING·ACCEPTED·PARTIAL·UNKNOWN 상태의 주문. 재시작 복구가 결말을
        확인한다.

        `dict(row)` 를 그대로 돌려주지 않는다(Fix Round 3) — `sent_at` 이
        `str` 로, `side`·`path` 가 맨 문자열로 새어나가면 다른 모든 읽기
        경로가 지키는 H2(tz-aware) 경계를 이 메서드만 건너뛴다. `status` 는
        `str` 로 남긴다 — 대응하는 도메인 enum 이 없다.
        """
        placeholders = ", ".join("?" for _ in self._PENDING_STATUSES)
        rows = self._conn.execute(
            f"SELECT * FROM order_log WHERE status IN ({placeholders}) ORDER BY id",
            self._PENDING_STATUSES,
        ).fetchall()
        return [
            PendingOrderRow(
                order_log_id=r["id"],
                client_ref=r["client_ref"],
                broker_order_id=r["broker_order_id"],
                cycle_id=r["cycle_id"],
                stage_state_id=r["stage_state_id"],
                side=Side(r["side"]),
                path=OrderPath(r["path"]),
                req_price=r["req_price"],
                req_qty=r["req_qty"],
                fill_price=r["fill_price"],
                fill_qty=r["fill_qty"],
                status=r["status"],
                sent_at=text_to_dt(r["sent_at"]),
            )
            for r in rows
        ]

    def realized_pnl_for_cycle(self, cycle_id: int) -> int:
        """order_log 에서 집계한 실현손익 (H5).

        도메인에는 이 값이 없고 있을 수 없다 — `after_sell` 이 `fill_price` 와
        `fill_qty` 를 비우므로 단계 상태만으로는 계산할 수 없다(Plan 1 최종 리뷰
        handover 7). 그래서 주문 이력이 유일한 근거다.

        체결된 매도 금액 합에서 체결된 매수 금액 합을 뺀다. `path` 는 구분하지
        않는다: 긴급청산 매도도 실현이다.

        **집계 기준은 체결 데이터이지 status 가 아니다(Fix Round 1 Finding 1).**
        `status` 는 주문의 생애가 어디서 끝났는지를 말하고, 체결 데이터
        (`fill_price`·`fill_qty`)는 실제로 무엇이 오갔는지를 말한다. 설계서
        200행이 규정하는 정상 절차 — 부분체결 매수는 체결 수량만으로 확정하고
        잔량을 취소한다 — 를 따르면 그 주문은 `CANCELED` 로 끝나면서도 실제
        취득원가를 나타내는 체결 데이터를 갖는다. `status IN ('FILLED',
        'PARTIAL')` 로만 걸렀다면 그 취득원가가 조용히 사라지고, 나중에 그
        체결분을 팔면 매수 원가 없이 매도 금액만 실현손익에 더해져 이익이
        실제보다 커 보인다. 그래서 이 조건은 체결이 실제로 있었는지
        (`fill_price`·`fill_qty` 가 NULL 이 아니고 `fill_qty > 0`)만 본다.

        `REJECTED` 는 예외적으로 제외한다 — 거부된 주문은 체결될 수 없으므로,
        `REJECTED` 행에 체결값이 있다면 그 자체가 데이터 손상이다. 거부된
        돈은 세지 않는다.

        수수료와 세금은 포함하지 않는다. 설계서 1.3절이 세금 계산 자동화를
        범위에서 배제했다.

        **보유가 남은 사이클에도 값을 낸다.** 그때 이 값은 "지금까지 실현된 손익"
        이며 최종값이 아니다. 사이클 종료 시점에 이 값을 `cycle.realized_pnl` 에
        기록하는 것은 호출자(Plan 2B 의 엔진)의 몫이다.
        """
        row = self._conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN side = 'SELL' "
            "                         THEN fill_price * fill_qty ELSE 0 END), 0) "
            "     - COALESCE(SUM(CASE WHEN side = 'BUY' "
            "                         THEN fill_price * fill_qty ELSE 0 END), 0) "
            "       AS pnl "
            "FROM order_log "
            "WHERE cycle_id = ? "
            "  AND fill_price IS NOT NULL AND fill_qty IS NOT NULL "
            "  AND fill_qty > 0 AND status != 'REJECTED'",
            (cycle_id,),
        ).fetchone()
        return int(row["pnl"])

    # ── 이력 로그 ───────────────────────────────────────────────────────
    def append_emergency_log(
        self, *, scope: str, stock_code: str | None, cycle_id: int | None,
        requested_at: datetime, reason: str | None, qty_before: int | None,
        qty_after: int | None, canceled_orders: int | None, result: str,
        detail_json: str | None, completed_at: datetime | None,
    ) -> int:
        """긴급청산 이력. 설계서 11.1절 ⑥ 과 D20 의 강제 종료(result=FORCED_CLOSE)."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO emergency_liquidation_log (scope, stock_code, "
                " cycle_id, requested_at, reason, qty_before, qty_after, "
                " canceled_orders, result, detail_json, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (scope, stock_code, cycle_id, dt_to_text(requested_at), reason,
                 qty_before, qty_after, canceled_orders, result, detail_json,
                 None if completed_at is None else dt_to_text(completed_at)),
            )
        return int(cursor.lastrowid)

    def append_reconcile_log(
        self, *, checked_at: datetime, stock_code: str, internal_qty: int,
        broker_qty: int, verdict: str, action_taken: str | None,
    ) -> int:
        """대사 이력. 설계서 10.2절 — 일치는 로그 없음이 원칙이지만, 이력
        테이블에는 남겨 사후에 대사가 실제로 돌았는지 확인할 수 있게 한다."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO reconcile_log (checked_at, stock_code, "
                " internal_qty, broker_qty, verdict, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (dt_to_text(checked_at), stock_code, internal_qty, broker_qty,
                 verdict, action_taken),
            )
        return int(cursor.lastrowid)

    # ── 보유현황 뷰 ─────────────────────────────────────────────────────
    def holdings(self) -> list[HoldingRow]:
        """설계서 12.3절의 뷰를 읽어 HoldingRow 로 변환한다.

        현재가와 평가손익률은 없다 — 실시간 값이므로 UI 가 최신 틱과 결합해
        `domain/pnl.py` 의 순수 함수로 계산한다.

        **`avg_price` 는 표시용이다.** 뷰는 SQL 의 정수 나눗셈으로 계산하므로
        절사이고, `domain/pnl.py` 의 `avg_price` 는 half-up 반올림이다 —
        투입금액을 수량으로 나눈 소수부가 0.5 이상이면 두 값이 1원 갈린다.
        UI 는 이 값을 목록 표시에 쓰되, 손익 계산에는 반드시 `domain/pnl.py`
        의 함수를 써야 한다.
        """
        rows = self._conn.execute(
            "SELECT * FROM holdings ORDER BY stock_code, label"
        ).fetchall()
        return [
            HoldingRow(
                stock_code=r["stock_code"],
                stock_name=r["stock_name"],
                label=r["label"],
                cycle_id=r["cycle_id"],
                total_qty=int(r["total_qty"]),
                avg_price=int(r["avg_price"]),
                holding_stages=int(r["holding_stages"]),
                max_stages=int(r["max_stages"]),
                cycle_status=CycleStatus(r["cycle_status"]),
            )
            for r in rows
        ]
