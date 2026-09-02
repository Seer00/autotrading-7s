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
