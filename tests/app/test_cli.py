from __future__ import annotations

from pathlib import Path

import pytest

from autotrading7s import cli


def test_db_paths_are_separated_by_environment():
    """D15 — 모의투자와 실전의 DB 파일이 절대 섞이지 않는다.

    한 파일을 공유하면 모의투자의 체결 기록이 실전 사이클의 목표가 계산에
    섞여 들어갈 수 있다.
    """
    assert cli.db_path_for("mock") == Path("data/mock/autotrading7s.db")
    assert cli.db_path_for("real") == Path("data/real/autotrading7s.db")
    with pytest.raises(ValueError, match="env"):
        cli.db_path_for("prod")


def test_real_environment_without_an_adapter_fails_loudly(tmp_path, capsys):
    """키움 어댑터가 없다는 사실이 조용히 숨어서는 안 된다.

    조용히 시뮬레이션으로 대체하면 사용자가 실전이라고 믿는 채로 가짜
    브로커에 주문을 낸다.
    """
    settings = tmp_path / "settings.toml"
    settings.write_text("[engine]\ntotal_limit = 1000000\n", encoding="utf-8")
    code = cli.main(["--env", "real", "--settings", str(settings),
                     "--db", str(tmp_path / "real.db")])
    assert code != 0
    assert "키움" in capsys.readouterr().err


def test_simulate_runs_headless_and_exits_zero(tmp_path):
    """설계서 14.4절 — GUI 없이 엔진만 돌아야 한다.

    EC2 에서 자동 테스트할 수 있는 경로가 이것뿐이다(설계서 18.1 리스크 7).
    """
    settings = tmp_path / "settings.toml"
    settings.write_text("[engine]\ntotal_limit = 100000000\n",
                        encoding="utf-8")
    db = tmp_path / "cli.db"
    code = cli.main(["--env", "mock", "--settings", str(settings),
                     "--db", str(db), "--simulate", "10000,9500,10100"])
    assert code == 0
    assert db.exists()


def test_settings_are_required(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["--env", "mock"])


def _seeded_db(tmp_path):
    """IDLE 설정 하나가 있는 DB — 표에 행이 하나 나오게 만든다."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
    from autotrading7s.adapters.sqlite.repository import SqliteRepository
    from autotrading7s.ports.repository import SplitConfig

    at = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
    pct = Decimal("0.05")
    db = tmp_path / "cli.db"
    conn = connect(db)
    apply_schema(conn)
    SqliteRepository(conn).save_config(SplitConfig(
        config_id=None, stock_code="005930", stock_name="삼성전자",
        label="기본", max_stages=7, drop_pct=pct, target_pct=pct,
        amount_per_stage=1_000_000, allow_rebuy=False, rebuy_cooldown_sec=60,
        total_limit=7_000_000, status="IDLE", created_at=at, updated_at=at))
    conn.close()
    return db


def test_status_mode_prints_the_holdings_table(tmp_path, capsys):
    """프레젠터 사슬 전체가 EC2 에서 end-to-end 로 돈다 —
    스냅샷 발행 → 프레젠터 소비 → 뷰모델 → 렌더러.

    그 사슬이 Windows 에서 처음 돌면 어디가 틀렸는지 알기 어렵다.
    """
    settings = tmp_path / "settings.toml"
    settings.write_text("[engine]\ntotal_limit = 100000000\n",
                        encoding="utf-8")
    db = _seeded_db(tmp_path)
    code = cli.main(["--env", "mock", "--settings", str(settings),
                     "--db", str(db), "--simulate", "10000,9500", "--status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "보유현황" in out
    assert "삼성전자" in out and "005930" in out
    assert "합계" in out
    assert "총한도" in out
    assert "증권사" in out


def test_status_mode_is_off_by_default(tmp_path, capsys):
    """`--status` 없이는 조용히 돈다 — 상시 가동 프로세스가 로그를 채우면 안 된다."""
    settings = tmp_path / "settings.toml"
    settings.write_text("[engine]\ntotal_limit = 100000000\n",
                        encoding="utf-8")
    db = _seeded_db(tmp_path)
    code = cli.main(["--env", "mock", "--settings", str(settings),
                     "--db", str(db), "--simulate", "10000,9500"])
    assert code == 0
    assert "보유현황" not in capsys.readouterr().out
