from __future__ import annotations

import sqlite3

import pytest

from autotrading7s.adapters.sqlite import migrations as migrations_module
from autotrading7s.adapters.sqlite.migrations import (
    SCHEMA_VERSION,
    apply_schema,
    connect,
)

EXPECTED_TABLES = {
    "split_config", "cycle", "stage_state", "order_log",
    "emergency_liquidation_log", "token_session", "reconcile_log",
    "schema_version",
}


@pytest.fixture()
def conn():
    c = connect(":memory:")
    apply_schema(c)
    yield c
    c.close()


def test_applies_every_table(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert {r["name"] for r in rows} == EXPECTED_TABLES


def test_creates_the_holdings_view(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    ).fetchall()
    assert {r["name"] for r in rows} == {"holdings"}


def test_holdings_view_is_queryable_when_empty(conn):
    """뷰가 문법적으로 유효한지 — 빈 상태에서도 실행되어야 한다."""
    assert conn.execute("SELECT * FROM holdings").fetchall() == []


def test_records_the_schema_version(conn):
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == SCHEMA_VERSION


def test_apply_is_idempotent(conn):
    """이미 최신이면 아무것도 하지 않는다 — 매 기동마다 호출해도 안전해야 한다."""
    assert apply_schema(conn) == SCHEMA_VERSION
    assert apply_schema(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    assert rows["n"] == 1


def test_foreign_keys_are_enforced(conn):
    """PRAGMA foreign_keys 가 꺼져 있으면 REFERENCES 가 장식이 된다."""
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage_state "
            "(cycle_id, stage_no, status, trigger_price, planned_qty) "
            "VALUES (999, 1, 'WAITING', 9000, 111)"
        )


def test_stage_state_uniqueness(conn):
    """같은 사이클에 같은 단계번호가 둘 있으면 decide() 가 중복으로 거부한다 —
    그 전에 스키마가 막아야 한다."""
    conn.execute(
        "INSERT INTO split_config "
        "(stock_code, max_stages, drop_pct, target_pct, amount_per_stage, "
        " total_limit, status, created_at, updated_at) "
        "VALUES ('005930', 7, '0.05', '0.05', 1000000, 7000000, 'IDLE', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO cycle (config_id, seq, status, started_at) "
        "VALUES (1, 1, 'STARTING', 'x')"
    )
    conn.execute(
        "INSERT INTO stage_state "
        "(cycle_id, stage_no, status, trigger_price, planned_qty) "
        "VALUES (1, 1, 'WAITING', 9000, 111)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage_state "
            "(cycle_id, stage_no, status, trigger_price, planned_qty) "
            "VALUES (1, 1, 'WAITING', 9000, 111)"
        )


def test_order_log_client_ref_is_unique(conn):
    """client_ref 는 설계서 9절의 멱등성 키다 — 중복이면 UNKNOWN 대조가 무의미해진다."""
    cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(order_log)").fetchall()
    }
    assert "client_ref" in cols
    idx = conn.execute("PRAGMA index_list(order_log)").fetchall()
    assert any(r["unique"] for r in idx)


def test_cycle_carries_the_d20_columns(conn):
    """설계서 D20 — 강제 종료의 증언과 잔량."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cycle)").fetchall()}
    assert {"close_reason", "forced_close_reason", "forced_close_qty"} <= cols


def test_token_session_stores_no_token(conn):
    """설계서 13.1절 — 토큰 원문은 keyring 에 있고 DB 는 감사 목적만.

    두 이름을 블랙리스트하고 부분집합만 확인하면 스펠링 검사에 불과하다 —
    `access_token` 처럼 블랙리스트에 없는 이름의 컬럼이 추가돼도 이 assert
    는 통과한다. 컬럼 집합을 정확히 고정해서, 비밀이 새 컬럼으로 슬쩍
    들어오면 이름과 무관하게 잡히게 한다.
    """
    cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(token_session)").fetchall()
    }
    assert cols == {"id", "env", "app_key_hash", "issued_at", "expires_at"}


def test_apply_schema_recovers_after_crash_between_ddl_and_version_write():
    """DDL 이 끝난 뒤, 버전 기록 전에 죽어도 다음 기동이 DB 를 못 쓰게 만들면 안 된다.

    executescript() 는 자신의 DDL 을 `with conn:` 의 커밋/롤백 범위 밖에서 즉시
    커밋한다. 그래서 스키마가 이미 만들어졌지만 schema_version 행이 아직 없는
    상태로 프로세스가 죽는 크래시 윈도우가 있다 — 여기서 재현한다. 이전에는 다음
    apply_schema() 호출이 `CREATE TABLE` 에서 "table already exists" 로 죽어
    사용자의 유일한 해결책이 DB 파일 삭제(거래 기록 손실)였다. `IF NOT EXISTS`
    가 이를 고친다.
    """
    conn = connect(":memory:")
    schema_sql = migrations_module._SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)  # DDL 만 실행 — 버전 행은 아직 없다

    # 크래시로 반쪽만 적용된 상태를 재확인
    row = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    assert row["n"] == 0

    assert apply_schema(conn) == SCHEMA_VERSION

    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    assert len(rows) == 1
    assert rows[0]["version"] == SCHEMA_VERSION
    conn.close()


def test_apply_schema_refuses_a_real_migration_it_cannot_perform(monkeypatch):
    """0 < current < SCHEMA_VERSION 은 진짜 마이그레이션이 필요한 구간이다.

    이전에는 이 구간에서도 IF NOT EXISTS 스크립트를 그냥 실행하고(전부
    no-op) 새 버전을 도장 찍어 반환했다 — DB 는 옛 모양 그대로인데 새
    버전이라고 우기는 셈이다. SCHEMA_VERSION == 1 인 지금은 current 가 이
    구간(0 과 1 사이의 정수)에 들어올 정수가 없어 재현할 수 없으므로,
    SCHEMA_VERSION 을 임시로 올려 재현한다.
    """
    conn = connect(":memory:")
    assert apply_schema(conn) == 1  # current 는 이제 1
    monkeypatch.setattr(migrations_module, "SCHEMA_VERSION", 2)
    with pytest.raises(RuntimeError, match="migration"):
        apply_schema(conn)
    conn.close()


def _seed_config_and_cycle(conn) -> int:
    """CHECK 위반 재현에 필요한 최소한의 split_config·cycle 행을 만든다."""
    conn.execute(
        "INSERT INTO split_config "
        "(stock_code, max_stages, drop_pct, target_pct, amount_per_stage, "
        " total_limit, status, created_at, updated_at) "
        "VALUES ('005930', 7, '0.05', '0.05', 1000000, 7000000, 'IDLE', 'x', 'x')"
    )
    cursor = conn.execute(
        "INSERT INTO cycle (config_id, seq, status, started_at) "
        "VALUES (1, 1, 'STARTING', 'x')"
    )
    return int(cursor.lastrowid)


def test_stage_state_holding_requires_fill_data(conn):
    """도메인의 StageState 불변식(HOLDING·SELL_PENDING 은 체결값이 있어야
    한다)을 스키마에서도 강제한다 — 이 CHECK 가 없으면 직접 INSERT 가
    체결값 없는 HOLDING 행을 만들 수 있다."""
    cycle_id = _seed_config_and_cycle(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage_state "
            "(cycle_id, stage_no, status, trigger_price, planned_qty, "
            " fill_price, fill_qty) "
            "VALUES (?, 1, 'HOLDING', 9000, 111, NULL, NULL)",
            (cycle_id,),
        )


def test_cycle_forced_close_requires_testimony_and_residual(conn):
    """D20 — close_reason='FORCED' 는 forced_close_reason·forced_close_qty 가
    둘 다 있어야 한다. 이 CHECK 가 없으면 증언도 잔량도 없는 강제 종료 행이
    만들어질 수 있다."""
    conn.execute(
        "INSERT INTO split_config "
        "(stock_code, max_stages, drop_pct, target_pct, amount_per_stage, "
        " total_limit, status, created_at, updated_at) "
        "VALUES ('005930', 7, '0.05', '0.05', 1000000, 7000000, 'IDLE', 'x', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cycle "
            "(config_id, seq, status, close_reason, started_at, closed_at) "
            "VALUES (1, 1, 'CLOSED', 'FORCED', 'x', 'x')"
        )


def test_cycle_seq_is_unique_per_config(conn):
    """UNIQUE(config_id, seq) — 사이클 이력의 순번은 설정별로 중복될 수 없다."""
    conn.execute(
        "INSERT INTO split_config "
        "(stock_code, max_stages, drop_pct, target_pct, amount_per_stage, "
        " total_limit, status, created_at, updated_at) "
        "VALUES ('005930', 7, '0.05', '0.05', 1000000, 7000000, 'IDLE', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO cycle (config_id, seq, status, started_at) "
        "VALUES (1, 1, 'STARTING', 'x')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cycle (config_id, seq, status, started_at) "
            "VALUES (1, 1, 'STARTING', 'x')"
        )


def test_stage_state_fill_price_must_be_positive(conn):
    """CHECK(fill_price IS NULL OR fill_price > 0) — 0 원 체결가는 손상이다."""
    cycle_id = _seed_config_and_cycle(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage_state "
            "(cycle_id, stage_no, status, trigger_price, planned_qty, "
            " fill_price) "
            "VALUES (?, 1, 'WAITING', 9000, 111, 0)",
            (cycle_id,),
        )
