from __future__ import annotations

from pathlib import Path

import pytest

from autotrading7s.app.settings import EngineSettings, load_settings


def test_defaults_match_the_spec():
    s = EngineSettings(total_limit=10_000_000)
    assert s.pending_timeout_sec == 3          # 설계서 9절
    assert s.reconcile_interval_sec == 300     # 설계서 10.2절 (장중 5분)
    assert s.max_orders_per_minute == 10       # 설계서 6절
    assert s.rebuy_cooldown_sec == 60          # 설계서 5절 규칙 3


def test_total_limit_cannot_be_defaulted():
    """전체 총한도는 사용자가 명시해야 한다 — 이 프로그램의 유일한 구조적
    보호장치이므로(설계서 6절), 기본값이 조용히 적용되는 것은 손절매 없는
    전략에서 무한 물타기를 묵인하는 것이다.

    선언상의 기본값 0 이 __post_init__ 에서 거부되므로, total_limit 을
    지정하지 않은 EngineSettings() 는 만들 수 없다.
    """
    with pytest.raises(ValueError, match="total_limit"):
        EngineSettings()
    with pytest.raises(TypeError, match="total_limit"):
        EngineSettings(total_limit=None)   # type: ignore[arg-type]
    assert EngineSettings(total_limit=10_000_000).total_limit == 10_000_000


def test_rejects_nonpositive_values():
    """각 필드가 자기 이름으로 거부되는지 확인한다.

    total_limit 을 함께 넘기는 이유: 넘기지 않으면 total_limit 의 기본값 0 이
    먼저 걸려서 어떤 kwargs 를 줘도 ValueError 가 난다 — 통과하지만 아무것도
    구별하지 못하는 테스트가 된다.
    """
    for name in ("pending_timeout_sec", "reconcile_interval_sec",
                 "max_orders_per_minute", "rebuy_cooldown_sec"):
        with pytest.raises(ValueError, match=name):
            EngineSettings(**{"total_limit": 1, name: 0})
    with pytest.raises(ValueError, match="total_limit"):
        EngineSettings(total_limit=0)


def test_load_settings_reads_toml(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text(
        "[engine]\n"
        "total_limit = 5000000\n"
        "pending_timeout_sec = 7\n",
        encoding="utf-8",
    )
    s = load_settings(path)
    assert s.total_limit == 5_000_000
    assert s.pending_timeout_sec == 7
    assert s.reconcile_interval_sec == 300     # 없는 항목은 기본값


def test_load_settings_rejects_unknown_keys(tmp_path: Path):
    """오타난 설정 키가 조용히 무시되면 사용자는 한도를 설정했다고 믿는다."""
    path = tmp_path / "settings.toml"
    path.write_text("[engine]\ntotal_limit = 1\ntotal_limitt = 9999999\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="total_limitt"):
        load_settings(path)


def test_load_settings_requires_total_limit(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text("[engine]\npending_timeout_sec = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="total_limit"):
        load_settings(path)
