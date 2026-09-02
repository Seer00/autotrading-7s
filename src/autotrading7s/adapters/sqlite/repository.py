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
from autotrading7s.domain.types import CycleStatus


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
