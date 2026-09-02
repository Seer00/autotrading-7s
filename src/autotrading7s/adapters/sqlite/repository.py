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
from datetime import datetime

from autotrading7s.adapters.sqlite.codec import dt_to_text
from autotrading7s.adapters.sqlite.mapping import (
    config_to_row,
    cycle_to_row,
    row_to_config,
    row_to_cycle,
    rows_to_stages,
    stage_to_row,
)
from autotrading7s.ports.repository import SplitConfig
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, OrderPath, Side


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

    def set_config_status(self, config_id: int, status: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE split_config SET status = ?, updated_at = ? WHERE id = ?",
                (status, dt_to_text(datetime.now().astimezone()), config_id),
            )

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
        """`cycle_to_row` 가 다루는 컬럼만 갱신한다.

        `realized_pnl` 은 Task 9(주문 이력·실현손익)가, `forced_close_reason`·
        `forced_close_qty`(D20)는 Plan 2B 의 강제 종료가 채운다 — 여기서는
        건드리지 않는다.
        """
        row = cycle_to_row(cycle)
        assignments = ", ".join(f"{k} = :{k}" for k in row)
        with self._conn:
            self._conn.execute(
                f"UPDATE cycle SET {assignments} WHERE id = :id",
                row | {"id": cycle.cycle_id},
            )

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

    def save_stage(self, cycle_id: int, stage: StageState) -> None:
        row = stage_to_row(cycle_id, stage)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        updates = ", ".join(
            f"{k} = :{k}" for k in row if k not in ("cycle_id", "stage_no")
        )
        with self._conn:
            self._conn.execute(
                f"INSERT INTO stage_state ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(cycle_id, stage_no) DO UPDATE SET {updates}",
                row,
            )

    # ── 주문 이력 ───────────────────────────────────────────────────────
    _PENDING_STATUSES = ("SENDING", "ACCEPTED", "UNKNOWN")

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

    def update_order_log(
        self, *, client_ref: str, status: str, broker_order_id: str | None = None,
        fill_price: int | None = None, fill_qty: int | None = None,
        api_code: str | None = None, api_message: str | None = None,
        settled_at: datetime | None = None,
    ) -> None:
        with self._conn:
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

    def load_pending_orders(self) -> list[dict[str, object]]:
        """SENDING·ACCEPTED·UNKNOWN 상태의 주문. 재시작 복구가 결말을 확인한다."""
        placeholders = ", ".join("?" for _ in self._PENDING_STATUSES)
        rows = self._conn.execute(
            f"SELECT * FROM order_log WHERE status IN ({placeholders}) ORDER BY id",
            self._PENDING_STATUSES,
        ).fetchall()
        return [dict(r) for r in rows]

    def realized_pnl_for_cycle(self, cycle_id: int) -> int:
        """order_log 에서 집계한 실현손익 (H5).

        도메인에는 이 값이 없고 있을 수 없다 — `after_sell` 이 `fill_price` 와
        `fill_qty` 를 비우므로 단계 상태만으로는 계산할 수 없다(Plan 1 최종 리뷰
        handover 7). 그래서 주문 이력이 유일한 근거다.

        체결된 매도 금액 합에서 체결된 매수 금액 합을 뺀다. `PARTIAL` 도 센다 —
        부분 체결된 수량은 실제로 오간 것이다. `path` 는 구분하지 않는다: 긴급청산
        매도도 실현이다.

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
            "WHERE cycle_id = ? AND status IN ('FILLED', 'PARTIAL') "
            "  AND fill_price IS NOT NULL AND fill_qty IS NOT NULL",
            (cycle_id,),
        ).fetchone()
        return int(row["pnl"])
