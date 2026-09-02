from __future__ import annotations

import queue
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.commands import SaveConfig
from autotrading7s.app.events import ConfigRejected, ConfigSaved
from autotrading7s.app.settings import EngineSettings
from autotrading7s.app.snapshot import Snapshot
from autotrading7s.engine.orchestrator import Orchestrator

AT = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)
PCT = Decimal("0.05")


def _build(repo, broker):
    clock = FakeClock(current=AT)
    qs = (queue.Queue(), queue.Queue(), queue.Queue())

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    return Orchestrator(
        repo=repo, broker=broker, clock=clock,
        settings=EngineSettings(total_limit=100_000_000),
        command_q=qs[0], priority_q=qs[1], event_q=qs[2], sleep=sleep,
        max_fallback_rounds=1,
    ), qs


def _drain(event_q):
    out = []
    while not event_q.empty():
        out.append(event_q.get_nowait())
    return out


def _new(**over):
    kw = dict(config_id=None, stock_code="035720", stock_name="카카오",
              label="공격형", max_stages=7, drop_pct=PCT, target_pct=PCT,
              amount_per_stage=1_000_000, allow_rebuy=True,
              rebuy_cooldown_sec=60, total_limit=7_000_000)
    kw.update(over)
    return SaveConfig(**kw)


@pytest.mark.asyncio
async def test_save_config_creates_a_new_config(repo_two_stocks):
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new())

    await orch.drain_commands()

    events = _drain(event_q)
    saved = [e for e in events if isinstance(e, ConfigSaved)]
    assert len(saved) == 1
    assert repo_two_stocks.load_config(saved[0].config_id).stock_code == "035720"
    # 스냅샷이 함께 나가야 새 설정이 화면에 보인다
    snaps = [e for e in events if isinstance(e, Snapshot)]
    assert snaps and len(snaps[-1].configs) == 3


@pytest.mark.asyncio
async def test_a_new_config_starts_idle(repo_two_stocks):
    """등록만으로 사이클이 시작되지 않는다 — 시작은 사용자가 [시작]을 누를 때다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new())
    await orch.drain_commands()
    saved = [e for e in _drain(event_q) if isinstance(e, ConfigSaved)][0]
    assert repo_two_stocks.load_config(saved.config_id).status == "IDLE"


@pytest.mark.asyncio
async def test_save_config_rejects_a_domain_invariant_violation(repo_two_stocks):
    """단계 수 2~7 (설계서 3.1절) — Ladder 가 거부하고 엔진이 되돌린다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(max_stages=9))

    await orch.drain_commands()

    rejected = [e for e in _drain(event_q) if isinstance(e, ConfigRejected)]
    assert len(rejected) == 1
    assert "max_stages" in rejected[0].detail
    assert len(repo_two_stocks.list_configs()) == 2


@pytest.mark.asyncio
async def test_save_config_rejects_an_impossible_drop(repo_two_stocks):
    """drop_pct × (단계-1) >= 1 이면 마지막 발동가가 0 이하가 된다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(drop_pct=Decimal("0.2"), max_stages=7))

    await orch.drain_commands()

    rejected = [e for e in _drain(event_q) if isinstance(e, ConfigRejected)]
    assert len(rejected) == 1
    assert "단계" in rejected[0].detail


@pytest.mark.asyncio
async def test_save_config_rejects_values_the_ladder_does_not_check(
    repo_two_stocks,
):
    """`rebuy_cooldown_sec`·`total_limit` 은 Ladder 가 보지 않는다.

    직접 검사하지 않으면 스키마 CHECK 가 IntegrityError 로 거부하고, 그 예외는
    포트 계약에 없어서 사용자에게 이유가 전달되지 않는다.
    """
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(rebuy_cooldown_sec=-1))
    command_q.put(_new(total_limit=-1))

    await orch.drain_commands()

    rejected = [e for e in _drain(event_q) if isinstance(e, ConfigRejected)]
    assert len(rejected) == 2
    assert "rebuy_cooldown_sec" in rejected[0].detail
    assert "total_limit" in rejected[1].detail
    assert len(repo_two_stocks.list_configs()) == 2


@pytest.mark.asyncio
async def test_save_config_updates_an_idle_config(repo_two_stocks):
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(config_id=1, stock_code="005930", stock_name="삼성전자",
                       label="보수형", amount_per_stage=300_000))

    await orch.drain_commands()

    assert [e for e in _drain(event_q) if isinstance(e, ConfigSaved)]
    assert repo_two_stocks.load_config(1).label == "보수형"
    assert repo_two_stocks.load_config(1).amount_per_stage == 300_000


@pytest.mark.asyncio
async def test_save_config_refuses_to_update_an_active_config(repo_two_stocks):
    """저장 한 번으로 진행 중인 사이클을 로드 불가로 만들 수 있으면 안 된다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(config_id=1, stock_code="005930", stock_name="삼성전자",
                       label="바뀜", amount_per_stage=2_000_000))

    await orch.drain_commands()

    rejected = [e for e in _drain(event_q) if isinstance(e, ConfigRejected)]
    assert len(rejected) == 1
    assert "IDLE" in rejected[0].detail
    assert repo_two_stocks.load_config(1).amount_per_stage == 500_000


def test_save_config_carries_typed_values_only():
    """문자열 파싱은 뷰모델의 몫이다 (2B 핸드오버 9).

    명령이 문자열을 받으면 파싱 실패가 엔진 스레드에서 일어나고, 그 오류
    메시지는 입력 위젯 옆이 아니라 로그에 나타난다.
    """
    with pytest.raises(TypeError, match="drop_pct"):
        SaveConfig(config_id=None, stock_code="005930", stock_name=None,
                   label=None, max_stages=7, drop_pct="0.05",  # type: ignore[arg-type]
                   target_pct=PCT, amount_per_stage=1_000_000,
                   allow_rebuy=True, rebuy_cooldown_sec=60,
                   total_limit=7_000_000)
    with pytest.raises(TypeError, match="amount_per_stage"):
        SaveConfig(config_id=None, stock_code="005930", stock_name=None,
                   label=None, max_stages=7, drop_pct=PCT, target_pct=PCT,
                   amount_per_stage="1000000",  # type: ignore[arg-type]
                   allow_rebuy=True, rebuy_cooldown_sec=60,
                   total_limit=7_000_000)
