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


# ── 배경 보안 리뷰가 지적한 세 건 ───────────────────────────────────────
@pytest.mark.asyncio
async def test_one_failing_command_does_not_kill_the_loop(repo_two_stocks):
    """`unhandled-exception-kills-priority-command-loop`.

    예외가 `drain_commands` 를 빠져나가면 `run()` 을 거쳐 엔진 스레드가 죽고
    **그 시점부터 모든 명령이 영구히 처리되지 않는다** — 설계서 7.1절이
    priority_q 로 보장하려는 긴급 명령의 즉시성이 앞선 일반 명령 하나의
    실패로 무너진다.
    """
    from autotrading7s.app.commands import StartCycle
    from autotrading7s.app.events import CommandFailed

    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)

    boom = StartCycle(config_id=9999)      # 없는 설정 → 리포지토리가 던진다
    command_q.put(boom)
    command_q.put(_new())                  # 뒤에 쌓인 정상 명령

    await orch.drain_commands()            # 예외가 새어나오지 않아야 한다

    events = _drain(event_q)
    failed = [e for e in events if isinstance(e, CommandFailed)]
    assert len(failed) == 1
    assert failed[0].command == "StartCycle"
    # 뒤에 쌓인 명령이 처리됐다
    assert [e for e in events if isinstance(e, ConfigSaved)]
    assert command_q.empty()


@pytest.mark.asyncio
async def test_a_failing_command_does_not_swallow_the_emergency_behind_it(
    repo_two_stocks,
):
    """긴급청산이 앞선 실패에 묻히면 안 된다 — 그것이 이 격리의 요점이다."""
    from autotrading7s.app.commands import EmergencyLiquidate, StartCycle
    from autotrading7s.app.events import CommandFailed, EmergencyResult

    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    orch, (command_q, priority_q, event_q) = _build(repo_two_stocks, broker)
    priority_q.put(StartCycle(config_id=9999))     # priority_q 의 실패
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="긴급", confirmed_text=None))

    await orch.drain_commands()

    events = _drain(event_q)
    assert [e for e in events if isinstance(e, CommandFailed)]
    results = [e for e in events if isinstance(e, EmergencyResult)]
    assert results and results[0].result == "SUCCESS"


@pytest.mark.asyncio
async def test_duplicate_name_is_rejected_with_a_readable_message(
    repo_two_stocks,
):
    """스키마의 UNIQUE(stock_code, label) 가 최종 방어선이지만 그
    IntegrityError 는 사용자에게 "같은 이름의 설정이 이미 있다" 를 말해주지
    않는다 — 그리고 그 예외는 ValueError 도 TypeError 도 아니라 명령 루프의
    격리까지 가야 한다.
    """
    existing = repo_two_stocks.load_config(1)
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(stock_code=existing.stock_code, label=existing.label))

    await orch.drain_commands()

    rejected = [e for e in _drain(event_q) if isinstance(e, ConfigRejected)]
    assert len(rejected) == 1
    assert existing.stock_code in rejected[0].detail
    assert len(repo_two_stocks.list_configs()) == 2


@pytest.mark.asyncio
async def test_renaming_a_config_to_its_own_label_is_fine(repo_two_stocks):
    """자기 이름으로 저장하는 것은 중복이 아니다.

    이 예외를 빠뜨리면 사용자가 이름 말고 다른 값만 고칠 수 없다.
    """
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    existing = repo_two_stocks.load_config(1)
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(config_id=1, stock_code=existing.stock_code,
                       label=existing.label, amount_per_stage=300_000))

    await orch.drain_commands()

    assert [e for e in _drain(event_q) if isinstance(e, ConfigSaved)]
    assert repo_two_stocks.load_config(1).amount_per_stage == 300_000
