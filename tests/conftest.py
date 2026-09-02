"""엔진·어댑터 테스트가 공유하는 리포지토리 픽스처.

실제 `SqliteRepository` 를 `tmp_path` 위에 만든다. 가짜 리포지토리를 쓰면
`load_active_cycles` 가 CLOSED 를 제외하는지, `save_stage` 의 가드가 전이를
막는지를 검증할 수 없다 — 그 둘이 바로 이 계획이 의존하는 동작이다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import CloseReason, StageStatus
from autotrading7s.ports.repository import SplitConfig

AT = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)


def _config(code: str, name: str, *, amount: int, limit: int) -> SplitConfig:
    return SplitConfig(
        config_id=None, stock_code=code, stock_name=name, label=None,
        max_stages=7, drop_pct=Decimal("0.05"), target_pct=Decimal("0.05"),
        amount_per_stage=amount, allow_rebuy=True, rebuy_cooldown_sec=60,
        total_limit=limit, status="ACTIVE", created_at=AT, updated_at=AT,
    )


def _new_repo(tmp_path: Path) -> SqliteRepository:
    """`SqliteRepository` 는 경로가 아니라 연결을 받는다 — 환경(모의/실전)을
    스스로 정하지 않는다는 D15 의 귀결이다 (설계서 13.2절).
    """
    conn = connect(tmp_path / "t.db")
    apply_schema(conn)
    return SqliteRepository(conn)


def _seed(repo, *, code, name, amount, limit, fills):
    """단계 1..7 을 만들고 `fills` 의 (price, qty) 로 HOLDING 을 만든다.

    `fills` 보다 뒤의 단계는 WAITING 으로 남는다. 전체 집합을 쓰는 이유는
    `load_stages` 가 불완전한 집합을 거부하기 때문이다(H3).
    """
    config_id = repo.save_config(_config(code, name, amount=amount, limit=limit))
    # create_cycle 이 이미 STARTING 을 반환한다 — cycle.start() 를 다시 부르면
    # STARTING → STARTING 으로 IllegalCycleTransition 이 난다. start() 는
    # 도메인 단독 경로(IDLE → STARTING)의 것이다.
    cyc = repo.create_cycle(config_id, AT)
    config = repo.load_config(config_id)
    ladder = config.to_ladder(anchor_price=10_000)
    cyc = cycle_mod.confirm_anchor(cyc, anchor_price=10_000, ladder=ladder, at=AT)
    repo.save_cycle(cyc)
    for n in range(1, ladder.max_stages + 1):
        st = stage_mod.StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n),
            planned_qty=ladder.planned_qty(n),
        )
        fill = fills[n - 1] if n <= len(fills) else None
        if fill is not None:
            price, qty = fill
            st = stage_mod.to_holding(stage_mod.to_buy_pending(st),
                                      fill_price=price, fill_qty=qty, at=AT)
        repo.save_stage(cyc.cycle_id, st)
    return config_id, cyc


def _sell_off(repo, cycle_id, stage):
    """HOLDING 단계를 매도 완료 상태로 만든다 — **홉마다 저장한다.**

    `save_stage` 의 가드가 도메인 전이표를 참조하므로 두 홉을 합성해 한 번만
    저장하면 거부된다(HOLDING → SOLD 는 없는 전이). 설계서 9절 ④가 발주 전
    커밋을 요구하므로 그것이 옳다 (2A 핸드오버 9).
    """
    pending = stage_mod.to_sell_pending(stage)
    repo.save_stage(cycle_id, pending)
    sold = stage_mod.after_sell(pending, at=AT, allow_rebuy=False)
    repo.save_stage(cycle_id, sold)
    return sold


@pytest.fixture
def repo_two_stocks(tmp_path):
    """005930 에 1,000,000원, 000660 에 600,000원 노출."""
    repo = _new_repo(tmp_path)
    _seed(repo, code="005930", name="삼성전자", amount=500_000,
          limit=99_999_999, fills=[(10_000, 100)])
    _seed(repo, code="000660", name="SK하이닉스", amount=300_000,
          limit=99_999_999, fills=[(6_000, 100)])
    return repo


@pytest.fixture
def repo_with_sold_stage(tmp_path):
    """1단계 950,000원 보유 + 2단계는 매도 완료(노출 아님)."""
    repo = _new_repo(tmp_path)
    _config_id, cyc = _seed(repo, code="005930", name="삼성전자",
                            amount=500_000, limit=99_999_999,
                            fills=[(9_500, 100), (9_000, 100)])
    second = next(s for s in repo.load_stages(cyc.cycle_id) if s.stage_no == 2)
    _sell_off(repo, cyc.cycle_id, second)
    return repo


@pytest.fixture
def repo_with_closed_cycle(tmp_path):
    """CLOSED 사이클만 있는 리포지토리 — load_active_cycles 가 제외해야 한다."""
    repo = _new_repo(tmp_path)
    _config_id, cyc = _seed(repo, code="005930", name="삼성전자",
                            amount=500_000, limit=99_999_999,
                            fills=[(9_500, 100)])
    for stage in repo.load_stages(cyc.cycle_id):
        if stage.status is StageStatus.HOLDING:
            _sell_off(repo, cyc.cycle_id, stage)
    states = repo.load_stages(cyc.cycle_id)
    repo.save_cycle(cycle_mod.close(cyc, reason=CloseReason.NORMAL, at=AT,
                                    states=states))
    return repo


@pytest.fixture
def repo_fresh(tmp_path):
    """체결 없는 RUNNING 사이클 — 단계 7개 전부 WAITING.

    단계금액 1,000,000원 / 앵커 10,000원이므로 1단계 계획수량은 100주다.
    """
    repo = _new_repo(tmp_path)
    _seed(repo, code="005930", name="삼성전자", amount=1_000_000,
          limit=99_999_999, fills=[])
    return repo
