"""스키마 적용과 버전 추적.

`apply_schema` 는 멱등이다 — 매 기동마다 호출해도 안전해야 하며, 이미 최신이면
아무것도 하지 않는다. 버전이 미래이면(더 새 버전의 프로그램이 만든 DB) 거부한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str | Path) -> sqlite3.Connection:
    """외래키를 켜고 row_factory 를 설정한 연결.

    SQLite 는 외래키가 기본 꺼짐이며, 꺼진 상태에서는 REFERENCES 가 장식이 된다 —
    사이클이 없는 단계 행이 들어갈 수 있고, 그것은 H3 가 막으려는 손상과 같은
    부류다. 매 연결에서 켜야 하며 DB 파일에 저장되는 설정이 아니다.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return 0 if row is None else int(row["version"])


def apply_schema(conn: sqlite3.Connection) -> int:
    """스키마를 적용하고 적용 후 버전을 반환. 멱등."""
    current = _current_version(conn)
    if current == SCHEMA_VERSION:
        return current
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"DB schema version {current} is newer than this program's "
            f"{SCHEMA_VERSION} — refusing to touch it"
        )
    # executescript() 는 실행 전에 보류 중인 트랜잭션을 암묵적으로 커밋하고,
    # 스크립트의 DDL 은 `with conn:` 의 커밋/롤백 범위 밖에서 즉시 반영된다
    # (직접 실험으로 확인함 — 보고서 참고). 그래도 정상 경로에서는 두 statement
    # 를 하나의 `with conn:` 로 묶어 버전 기록의 원자성을 표현한다.
    with conn:  # 전체를 한 트랜잭션으로
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )
    return SCHEMA_VERSION
