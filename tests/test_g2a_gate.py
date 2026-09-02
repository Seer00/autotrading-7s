"""G2a 게이트 — 영속성 계약의 조합 검증.

개별 태스크는 각자 리뷰를 통과했다. 이 파일이 확인하는 것은 그것들의 조합이다 —
도메인 객체가 DB 를 한 바퀴 돌아 돌아왔을 때 G1 이 통과했던 시나리오가 여전히
통과하는가.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import CorruptRowError, row_to_stage
from autotrading7s.ports.repository import SplitConfig
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import (
    close,
    confirm_anchor,
    is_cycle_complete,
)
from autotrading7s.domain.guards import GuardContext, check_buy, check_sell
from autotrading7s.domain.pnl import held_qty, invested_amount
from autotrading7s.domain.rules import BuyStage, SellStage, TriggerParams, decide
from autotrading7s.domain.stage import (
    StageState,
    after_sell,
    to_buy_pending,
    to_holding,
    to_sell_pending,
)
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    OrderPath,
    Side,
    StageStatus,
    Tick,
    TickSource,
)
from autotrading7s.ports.repository import RepositoryPort

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")
CODE = "005930"


@pytest.fixture()
def repo():
    conn = connect(":memory:")
    apply_schema(conn)
    yield SqliteRepository(conn)
    conn.close()


def a_config() -> SplitConfig:
    return SplitConfig(
        config_id=None, stock_code=CODE, stock_name="삼성전자", label="기본",
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=False, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="ACTIVE", created_at=T0, updated_at=T0)


def test_the_repository_satisfies_its_port(repo):
    assert isinstance(repo, RepositoryPort)


def test_the_full_cycle_survives_a_database_round_trip(repo):
    """G1 의 전 사이클 시나리오를 매 결정마다 저장하고 다시 읽어서 돌린다.

    기대값은 G1 과 같다: 하락 3틱에 2·3·4단계가 채워져 보유 433주, 반등 4틱에
    [4, 3, 2, 1] 순으로 매도, 총 주문 7건.
    """
    config_id = repo.save_config(a_config())
    config = repo.load_config(config_id)
    ladder = config.to_ladder(anchor_price=10_000)
    params = TriggerParams(target_pct=config.target_pct,
                           allow_rebuy=config.allow_rebuy,
                           rebuy_cooldown_sec=config.rebuy_cooldown_sec)

    cycle = repo.create_cycle(config_id, started_at=T0)
    for n in range(1, ladder.max_stages + 1):
        repo.save_stage(cycle.cycle_id, StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n),
            planned_qty=ladder.planned_qty(n)))

    # 1단계 체결로 앵커를 확정한다. 설계서 9절 ④·⑥ — BUY_PENDING 을 먼저
    # 저장하고 커밋한 뒤에야 HOLDING 을 저장한다(Fix Round 4). 두 홉을
    # 합성해 한 번만 저장하면 DB 관점에서 WAITING → HOLDING 단일 전이가
    # 되어 도메인 전이표에 없는 도약이 된다 — save_stage 의 전이 합법성
    # 가드가 정확히 그 생략을 잡는다.
    stages = repo.load_stages(cycle.cycle_id)
    first_pending = to_buy_pending(stages[0])
    repo.save_stage(cycle.cycle_id, first_pending)
    first = to_holding(first_pending, fill_price=10_000,
                       fill_qty=ladder.planned_qty(1), at=T0)
    repo.save_stage(cycle.cycle_id, first)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=ladder, at=T0)
    repo.save_cycle(cycle)

    orders = 0
    at = T0

    def step(price: int) -> list[BuyStage | SellStage]:
        """매 틱마다 DB 에서 다시 읽고, 결정을 반영한 뒤 다시 쓴다."""
        nonlocal orders, at
        live_cycle = repo.load_cycle(cycle.cycle_id)
        live_stages = repo.load_stages(cycle.cycle_id)
        decisions = decide(
            tick=Tick(code=CODE, price=price, at=at, source=TickSource.WS),
            cycle=live_cycle, states=live_stages, params=params, now=at,
            market_open=True, stock_code=config.stock_code)
        for decision in decisions:
            ctx = GuardContext(
                stock_invested=invested_amount(live_stages),
                stock_limit=config.total_limit,
                total_invested=invested_amount(live_stages),
                total_limit=21_000_000, orders_last_minute=orders)
            index = decision.stage_no - 1
            # 설계서 9절 ④·⑥ — 각 홉을 따로 저장한다(Fix Round 4). BUY_PENDING·
            # SELL_PENDING 을 먼저 커밋해야 프로세스가 발주와 체결 사이에
            # 죽어도 재시작 복구가 그 주문이 나가 있었다는 사실을 안다 —
            # "잘못 기록된 쪽이 잘못 잊힌 쪽보다 항상 낫다"(설계서 9절 ③).
            # to_holding(to_buy_pending(...)) 처럼 두 홉을 메모리에서 합성해
            # 한 번만 저장하면 그 중간 상태가 DB 에 전혀 나타나지 않는다 —
            # save_stage 의 전이 합법성 가드(Fix Round 4)가 그런 생략을
            # WAITING → HOLDING 같은 도메인에 없는 단일 도약으로 보고 거부
            # 한다. 이 주석을 지우고 "단순화" 하면 그 가드가 다시 열린다.
            if isinstance(decision, BuyStage):
                assert check_buy(decision, ctx).allowed
                pending = to_buy_pending(live_stages[index])
                repo.save_stage(cycle.cycle_id, pending)
                updated = to_holding(pending, fill_price=decision.limit_price,
                                     fill_qty=decision.qty, at=at)
                side = Side.BUY
            else:
                assert check_sell(decision, ctx).allowed
                pending = to_sell_pending(live_stages[index])
                repo.save_stage(cycle.cycle_id, pending)
                updated = after_sell(pending, at=at,
                                     allow_rebuy=params.allow_rebuy)
                side = Side.SELL
            repo.save_stage(cycle.cycle_id, updated)
            ref = f"g2a-{orders}"
            repo.append_order_log(
                client_ref=ref, cycle_id=cycle.cycle_id, stage_state_id=None,
                side=side, order_type="LIMIT", path=OrderPath.TRIGGER,
                req_price=decision.limit_price, req_qty=decision.qty,
                trigger_reason=decision.reason, tick_price=price,
                tick_source="WS", sent_at=at)
            repo.update_order_log(client_ref=ref, status="FILLED",
                                  broker_order_id=f"B{orders}",
                                  fill_price=decision.limit_price,
                                  fill_qty=decision.qty, settled_at=at)
            orders += 1
            live_stages = repo.load_stages(cycle.cycle_id)
        return decisions

    for price in (9_500, 9_000, 8_500):
        assert len(step(price)) == 1

    stages = repo.load_stages(cycle.cycle_id)
    assert held_qty(stages) == 433
    assert [s.status for s in stages[:4]] == [StageStatus.HOLDING] * 4

    sold_order: list[int] = []
    for price in (8_930, 9_450, 9_980, 10_500):
        for decision in step(price):
            assert isinstance(decision, SellStage)
            sold_order.append(decision.stage_no)

    assert sold_order == [4, 3, 2, 1]
    stages = repo.load_stages(cycle.cycle_id)
    assert held_qty(stages) == 0
    assert is_cycle_complete(stages) is True
    assert orders == 7

    closed = close(repo.load_cycle(cycle.cycle_id), reason=CloseReason.NORMAL,
                   at=at, states=stages)
    repo.save_cycle(closed)
    assert repo.load_cycle(cycle.cycle_id).status is CycleStatus.CLOSED
    assert repo.load_active_cycles() == []
    # Task 10 의 커버리지 공백 — holdings 뷰가 SOLD 단계·CLOSED 사이클을
    # 제외하는지 직접 확인한다. seed() 헬퍼는 WAITING/HOLDING 만 만들어서
    # 이 경로를 아무도 밟지 않았다. 이 사이클은 이제 단계가 전부 SOLD 이고
    # 사이클 자체가 CLOSED 이므로, holdings() 는 빈 목록이어야 한다.
    assert repo.holdings() == []


def test_realized_pnl_matches_the_round_trip(repo):
    """H5 — 실현손익이 order_log 집계와 일치해야 한다.

    433주를 사서 전부 팔았으므로 매도금액 합 − 매수금액 합이다.
    """
    config_id = repo.save_config(a_config())
    ladder = repo.load_config(config_id).to_ladder(anchor_price=10_000)
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=ladder, at=T0)
    repo.save_cycle(cycle)

    buys = [(10_000, 100), (9_500, 105), (9_000, 111), (8_500, 117)]
    sells = [(8_930, 117), (9_450, 111), (9_980, 105), (10_500, 100)]
    n = 0
    for price, qty in buys:
        ref = f"buy-{n}"
        repo.append_order_log(
            client_ref=ref, cycle_id=cycle.cycle_id, stage_state_id=None,
            side=Side.BUY, order_type="LIMIT", path=OrderPath.TRIGGER,
            req_price=price, req_qty=qty, trigger_reason="t", tick_price=price,
            tick_source="WS", sent_at=T0)
        repo.update_order_log(client_ref=ref, status="FILLED",
                              fill_price=price, fill_qty=qty, settled_at=T0)
        n += 1
    for price, qty in sells:
        ref = f"sell-{n}"
        repo.append_order_log(
            client_ref=ref, cycle_id=cycle.cycle_id, stage_state_id=None,
            side=Side.SELL, order_type="LIMIT", path=OrderPath.TRIGGER,
            req_price=price, req_qty=qty, trigger_reason="t", tick_price=price,
            tick_source="WS", sent_at=T0)
        repo.update_order_log(client_ref=ref, status="FILLED",
                              fill_price=price, fill_qty=qty, settled_at=T0)
        n += 1

    expected = sum(p * q for p, q in sells) - sum(p * q for p, q in buys)
    assert repo.realized_pnl_for_cycle(cycle.cycle_id) == expected


def test_a_decimal_survives_the_round_trip_exactly(repo):
    """0.1666 이 0.1666 으로 돌아와야 사다리가 같은 발동가를 낸다."""
    config = SplitConfig(
        config_id=None, stock_code="035720", stock_name=None, label="near-limit",
        max_stages=7, drop_pct=Decimal("0.1666"), target_pct=Decimal("0.05"),
        amount_per_stage=1_000_000, allow_rebuy=True, rebuy_cooldown_sec=60,
        total_limit=7_000_000, status="IDLE", created_at=T0, updated_at=T0)
    config_id = repo.save_config(config)
    loaded = repo.load_config(config_id)
    assert loaded.drop_pct == Decimal("0.1666")
    original = config.to_ladder(anchor_price=10_000)
    restored = loaded.to_ladder(anchor_price=10_000)
    assert [restored.trigger_price(n) for n in range(1, 8)] == \
           [original.trigger_price(n) for n in range(1, 8)]


def test_timestamps_stay_aware_so_the_cooldown_still_works(repo):
    """H2 — naive 로 돌아오면 쿨다운 산술이 엔진 틱 루프 안에서 TypeError 를 낸다."""
    config_id = repo.save_config(a_config())
    ladder = repo.load_config(config_id).to_ladder(anchor_price=10_000)
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=ladder, at=T0)
    repo.save_cycle(cycle)
    for n in range(1, 8):
        stage = StageState(stage_no=n, status=StageStatus.WAITING,
                           trigger_price=ladder.trigger_price(n),
                           planned_qty=ladder.planned_qty(n),
                           last_sold_at=T0 if n == 2 else None,
                           rebuy_count=1 if n == 2 else 0)
        repo.save_stage(cycle.cycle_id, stage)

    stages = repo.load_stages(cycle.cycle_id)
    assert stages[1].last_sold_at is not None
    assert stages[1].last_sold_at.tzinfo is not None
    # 쿨다운 산술이 성립한다 — 이것이 실패하면 H2 가 무너진 것이다.
    assert (T0 - stages[1].last_sold_at).total_seconds() == 0


def test_h3_and_h4_hold_at_the_repository_boundary(repo):
    """도메인은 부분 목록을 허용하고 리포지토리는 완전한 것만 준다.

    H3(완전한 집합)과 H4(trigger_price 대조)를 순서대로 확인한다 — 둘 다 같은
    "리포지토리 밖의 손상" 시나리오이므로 한 테스트에 둔다.
    """
    config_id = repo.save_config(a_config())
    ladder = repo.load_config(config_id).to_ladder(anchor_price=10_000)
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=ladder, at=T0)
    repo.save_cycle(cycle)
    for n in range(1, 8):
        repo.save_stage(cycle.cycle_id, StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n),
            planned_qty=ladder.planned_qty(n)))

    assert len(repo.load_stages(cycle.cycle_id)) == 7

    # H3 — 완전성. 4단계 행을 지워 불완전한 집합을 만든다.
    repo._conn.execute(  # noqa: SLF001 — 리포지토리 밖의 손상을 시뮬레이션
        "DELETE FROM stage_state WHERE cycle_id = ? AND stage_no = 4",
        (cycle.cycle_id,))
    repo._conn.commit()
    with pytest.raises(CorruptRowError, match="incomplete"):
        repo.load_stages(cycle.cycle_id)

    # 4단계를 복원해 집합을 다시 완전하게 만든다 — 이제부터는 H4 만 본다.
    repo.save_stage(cycle.cycle_id, StageState(
        stage_no=4, status=StageStatus.WAITING,
        trigger_price=ladder.trigger_price(4),
        planned_qty=ladder.planned_qty(4)))
    assert len(repo.load_stages(cycle.cycle_id)) == 7

    # H4 — trigger_price 대조. 3단계의 저장된 발동가를 사다리 계산과 다르게
    # 손상시킨다. 개수는 여전히 7개이므로 H3 는 통과하지만, ladder_json 과
    # 대조하는 H4 가 이 불일치를 거부해야 한다.
    repo._conn.execute(
        "UPDATE stage_state SET trigger_price = ? "
        "WHERE cycle_id = ? AND stage_no = 3",
        (999_999, cycle.cycle_id))
    repo._conn.commit()
    with pytest.raises(CorruptRowError, match="trigger_price mismatch"):
        repo.load_stages(cycle.cycle_id)


def test_h1_type_error_from_a_caller_bug_is_not_wrapped():
    """H1 의 나머지 절반 — 복원 실패와 호출자 버그를 구분한다.

    위 테스트가 이미 보여준 것은 값 손상이 `CorruptRowError` 로 감싸지는
    절반이다. `CorruptRowError` 는 `DomainInvariantError`(→ `ValueError`)의
    하위이므로 `row_to_stage` 는 `ValueError` 만 잡아 감싼다. 여기서는 저장된
    `trigger_price` 자리에 `int` 가 아닌 값(호출자가 잘못 만든 dict, 즉 DB
    손상이 아니라 프로그래밍 오류)을 넣어, `TypeError` 가 그대로(감싸지지
    않고) 올라오는지 확인한다 — 개발 중에 드러나야 하는 종류의 실패다.
    """
    bad_row = {
        "id": 1, "cycle_id": 1, "stage_no": 1, "status": "WAITING",
        "trigger_price": "9500",  # str — 호출자 버그, DB 손상이 아니다
        "planned_qty": 100, "fill_price": None, "fill_qty": None,
        "bought_at": None, "last_sold_at": None, "rebuy_count": 0,
    }
    with pytest.raises(TypeError):
        row_to_stage(bad_row)


def _find_forbidden_imports(source: str, forbidden: tuple[str, ...]) -> list[str]:
    """`source` 를 파싱해 `forbidden` 패키지를 임포트하는 문장을 모두 찾는다.

    세 가지 형태를 잡는다:

    1. 절대 임포트 — `import autotrading7s.adapters.x` 또는
       `from autotrading7s.adapters import x`. `module` 이나 (`ast.Import`
       의) 각 이름에 "autotrading7s.<banned>" 부분 문자열이 있으면 잡는다.
       `ast.Import` 는 `import os, autotrading7s.adapters.x` 처럼 쉼표로
       여러 모듈을 한 문장에 실을 수 있으므로(Fix Round 3 이전에는
       `node.names[0]` 만 봐서 이 형태를 놓쳤다) `node.names` 전부를 본다.
    2. 상대 임포트, 모듈 세그먼트로 패키지를 지목 — `from ..adapters.sqlite
       import mapping`. `module` 이 "adapters.sqlite" 다(접두어 없음) —
       `level > 0` 이면 첫 세그먼트로 다시 대조한다.
    3. 상대 임포트, 형제 패키지 자체를 이름으로 임포트 — `from .. import
       adapters`. `module` 은 없고(`node.module is None`) 금지된 이름이
       `node.names` 안에 있다(Fix Round 3 이전에는 이 형태가 `module` 도
       `module.split(".")[0]` 도 아니어서 아예 검사를 피해갔다).
    """
    import ast

    offenders: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level
        elif isinstance(node, ast.Import):
            module = ""
            level = 0
        else:
            continue
        names = [alias.name for alias in node.names]
        for banned in forbidden:
            absolute_hit = f"autotrading7s.{banned}" in module or any(
                f"autotrading7s.{banned}" in name for name in names
            )
            relative_module_hit = level > 0 and module.split(".")[0] == banned
            relative_name_hit = level > 0 and not module and banned in names
            if absolute_hit or relative_module_hit or relative_name_hit:
                offenders.append(f"{module or names} (level={level})")
    return offenders


def test_ports_and_adapters_import_only_inward():
    """설계서 7.2절 — 화살표는 항상 안쪽을 향한다.

    `domain/` 은 `tests/test_g1_gate.py` 가 이미 검사한다. 이 테스트는
    `ports/` 와 `adapters/` 가 서로를 잘못 참조하지 않는지 본다.
    """
    import pathlib

    root = pathlib.Path(__file__).parent.parent / "src" / "autotrading7s"
    offenders: list[str] = []

    for layer, forbidden in (("domain", ("ports", "adapters")),
                             ("ports", ("adapters",))):
        for path in (root / layer).rglob("*.py"):
            for hit in _find_forbidden_imports(
                path.read_text(encoding="utf-8"), forbidden
            ):
                offenders.append(f"{path.name}: {hit}")

    assert offenders == [], f"의존 방향 위반: {offenders}"


def test_the_import_checker_catches_a_multi_name_plain_import():
    """`import os, autotrading7s.adapters.x` 처럼 한 문장에 여러 모듈을 실은
    `ast.Import` — `node.names[0]` 만 보면 놓친다."""
    source = "import os, autotrading7s.adapters.x\n"
    assert _find_forbidden_imports(source, ("adapters",)) != []


def test_the_import_checker_catches_a_sibling_relative_import():
    """`from .. import adapters` — `node.module` 이 없고(`None`) 임포트되는
    이름이 `node.names` 안에 있다. `module` 문자열이나
    `module.split(".")[0]` 만 보면 이 형태는 아예 검사를 피해간다."""
    source = "from .. import adapters\n"
    assert _find_forbidden_imports(source, ("adapters",)) != []


def test_the_import_checker_does_not_flag_an_unrelated_relative_import():
    """형제 패키지를 이름으로 임포트하는 형태를 잡는 규칙이 과잉 반응해
    금지되지 않은 이름까지 잡으면 안 된다."""
    source = "from .. import domain\n"
    assert _find_forbidden_imports(source, ("adapters",)) == []
