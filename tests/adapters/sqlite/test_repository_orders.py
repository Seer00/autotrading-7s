from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import confirm_anchor
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import OrderPath, Side
from autotrading7s.ports.repository import (
    OrderLogInvariantError,
    OrderLogNotFound,
    SplitConfig,
)

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


@pytest.fixture()
def repo_and_cycle():
    conn = connect(":memory:")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    config_id = repo.save_config(SplitConfig(
        config_id=None, stock_code="005930", stock_name=None, label="기본",
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=True, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="IDLE", created_at=T0, updated_at=T0))
    lad = Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=FIVE,
                 max_stages=7, amount_per_stage=1_000_000)
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=lad, at=T0)
    repo.save_cycle(cycle)
    yield repo, cycle.cycle_id
    conn.close()


def an_order(repo, cycle_id, *, side, req_price, req_qty, path=OrderPath.TRIGGER,
             order_type="LIMIT") -> str:
    client_ref = str(uuid4())
    repo.append_order_log(
        client_ref=client_ref, cycle_id=cycle_id, stage_state_id=None, side=side,
        order_type=order_type, path=path, req_price=req_price, req_qty=req_qty,
        trigger_reason="test", tick_price=req_price, tick_source="WS", sent_at=T0)
    return client_ref


def test_append_records_sending_status(repo_and_cycle):
    """설계서 9절 ③ — 발주보다 먼저 기록하고 커밋한다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    pending = repo.load_pending_orders()
    assert len(pending) == 1
    assert pending[0].client_ref == ref
    assert pending[0].status == "SENDING"


def test_duplicate_client_ref_is_refused(repo_and_cycle):
    """client_ref 는 멱등성 키다 — 중복이면 UNKNOWN 대조가 무의미해진다."""
    import sqlite3

    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    with pytest.raises(sqlite3.IntegrityError):
        repo.append_order_log(
            client_ref=ref, cycle_id=cycle_id, stage_state_id=None, side=Side.BUY,
            order_type="LIMIT", path=OrderPath.TRIGGER, req_price=9_500,
            req_qty=105, trigger_reason="dup", tick_price=9_500, tick_source="WS",
            sent_at=T0)


def test_update_moves_the_order_out_of_pending(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    repo.update_order_log(client_ref=ref, status="FILLED", broker_order_id="B1",
                          fill_price=9_480, fill_qty=105, settled_at=T0)
    assert repo.load_pending_orders() == []


def test_unknown_status_stays_pending(repo_and_cycle):
    """설계서 9절 ⑤ — 응답 타임아웃은 UNKNOWN 이며 재시작 복구가 조회로 확인한다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    repo.update_order_log(client_ref=ref, status="UNKNOWN")
    assert [p.status for p in repo.load_pending_orders()] == ["UNKNOWN"]


def test_partial_status_stays_pending(repo_and_cycle):
    """Fix Round 3, Finding 1 — PARTIAL 은 SENDING·ACCEPTED·UNKNOWN 과 함께
    대기(pending) 쪽에 있어야 한다. 105주 요청에 40주만 체결된 주문은 65주가
    여전히 브로커에 살아 있으므로, 재시작 복구가 그 주문을 봐야 한다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    repo.update_order_log(client_ref=ref, status="PARTIAL", fill_price=9_500,
                          fill_qty=40, settled_at=T0)
    pending = repo.load_pending_orders()
    assert [p.status for p in pending] == ["PARTIAL"]
    assert pending[0].fill_qty == 40


def test_pending_and_terminal_statuses_partition_the_schema_vocabulary():
    """`_PENDING_STATUSES` 와 `_TERMINAL_STATUSES` 는 schema.sql 의 order_log
    `status` CHECK 가 규정하는 7개 값을 정확히 둘로 나눠야 한다 — 겹치지도
    않고, 빠지는 값도 없어야 한다(Fix Round 3, Finding 1: PARTIAL 이 둘
    사이에 끼어 load_pending_orders() 가 부분체결 주문을 놓쳤다).

    7개를 이 테스트에 그대로 나열하지 않고 schema.sql 에서 직접 뽑아온다 —
    스키마가 상태를 하나 더 추가하는데 이 분류가 갱신되지 않으면 이 테스트가
    깨져야 한다."""
    import re

    from autotrading7s.adapters.sqlite.migrations import _SCHEMA_PATH

    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"order_log.*?status TEXT NOT NULL CHECK\(status IN\s*\(([^)]+)\)\)",
        schema_sql, re.S,
    )
    assert match is not None, "order_log.status CHECK 를 schema.sql 에서 못 찾음"
    all_statuses = {s.strip().strip("'") for s in match.group(1).split(",")}

    pending = set(SqliteRepository._PENDING_STATUSES)  # noqa: SLF001
    terminal = set(SqliteRepository._TERMINAL_STATUSES)  # noqa: SLF001
    assert pending & terminal == set(), "PENDING·TERMINAL 이 겹친다"
    assert pending | terminal == all_statuses, "분류가 스키마의 상태 전체를 덮지 못한다"


def test_load_pending_orders_decodes_through_the_codec(repo_and_cycle):
    """Fix Round 3, Finding 2 — `dict(row)` 를 그대로 돌려주면 `sent_at` 이
    naive `str` 로, `side`·`path` 가 맨 문자열로 새어나간다. 다른 모든 읽기
    경로가 지키는 H2(tz-aware) 경계를 이 메서드도 지켜야 한다."""
    repo, cycle_id = repo_and_cycle
    an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    pending = repo.load_pending_orders()
    assert len(pending) == 1
    row = pending[0]
    assert isinstance(row.sent_at, datetime)
    assert row.sent_at.tzinfo is not None
    assert row.side is Side.BUY
    assert row.path is OrderPath.TRIGGER


def test_a_trigger_path_market_order_is_refused(repo_and_cycle):
    """설계서 6절 — 자동 트리거 경로는 시장가를 낼 수 없다. 스키마가 막는다."""
    import sqlite3

    repo, cycle_id = repo_and_cycle
    with pytest.raises(sqlite3.IntegrityError):
        an_order(repo, cycle_id, side=Side.SELL, req_price=None, req_qty=100,
                 path=OrderPath.TRIGGER, order_type="MARKET")


def test_an_emergency_path_market_order_is_allowed(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    an_order(repo, cycle_id, side=Side.SELL, req_price=None, req_qty=100,
             path=OrderPath.EMERGENCY, order_type="MARKET")
    assert len(repo.load_pending_orders()) == 1


# ── H5: 실현손익 집계 ─────────────────────────────────────────────────────

def _filled(repo, cycle_id, *, side, price, qty, path=OrderPath.TRIGGER,
            order_type="LIMIT") -> None:
    ref = an_order(repo, cycle_id, side=side, req_price=price, req_qty=qty,
                   path=path, order_type=order_type)
    repo.update_order_log(client_ref=ref, status="FILLED", broker_order_id="B",
                          fill_price=price, fill_qty=qty, settled_at=T0)


def test_realized_pnl_is_zero_with_no_orders(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    assert repo.realized_pnl_for_cycle(cycle_id) == 0


def test_realized_pnl_for_a_completed_round_trip(repo_and_cycle):
    """9,000 에 111주 사서 9,450 에 팔면 111 × 450 = 49,950 원."""
    repo, cycle_id = repo_and_cycle
    _filled(repo, cycle_id, side=Side.BUY, price=9_000, qty=111)
    _filled(repo, cycle_id, side=Side.SELL, price=9_450, qty=111)
    assert repo.realized_pnl_for_cycle(cycle_id) == 111 * 450


def test_realized_pnl_ignores_unfilled_orders(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    _filled(repo, cycle_id, side=Side.BUY, price=9_000, qty=111)
    _filled(repo, cycle_id, side=Side.SELL, price=9_450, qty=111)
    an_order(repo, cycle_id, side=Side.BUY, req_price=8_500, req_qty=117)
    assert repo.realized_pnl_for_cycle(cycle_id) == 111 * 450


def test_realized_pnl_counts_partial_fills(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.SELL, req_price=9_450, req_qty=111)
    repo.update_order_log(client_ref=ref, status="PARTIAL", broker_order_id="B",
                          fill_price=9_450, fill_qty=40, settled_at=T0)
    assert repo.realized_pnl_for_cycle(cycle_id) == 9_450 * 40


def test_realized_pnl_counts_emergency_sells(repo_and_cycle):
    """긴급청산 매도도 실현이다 — path 로 구분하지 않는다."""
    repo, cycle_id = repo_and_cycle
    _filled(repo, cycle_id, side=Side.BUY, price=10_000, qty=100)
    _filled(repo, cycle_id, side=Side.SELL, price=9_340, qty=100,
            path=OrderPath.EMERGENCY, order_type="MARKET")
    assert repo.realized_pnl_for_cycle(cycle_id) == 100 * (9_340 - 10_000)


def test_realized_pnl_is_partial_while_the_cycle_is_open(repo_and_cycle):
    """보유가 남은 사이클의 값은 '지금까지 실현된 손익'이며 최종값이 아니다."""
    repo, cycle_id = repo_and_cycle
    _filled(repo, cycle_id, side=Side.BUY, price=10_000, qty=100)
    _filled(repo, cycle_id, side=Side.BUY, price=9_500, qty=105)
    _filled(repo, cycle_id, side=Side.SELL, price=9_980, qty=105)
    expected = 9_980 * 105 - (10_000 * 100 + 9_500 * 105)
    assert repo.realized_pnl_for_cycle(cycle_id) == expected


def test_realized_pnl_is_scoped_to_the_cycle(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    other = repo.create_cycle(repo.list_configs()[0].config_id, started_at=T0)
    _filled(repo, cycle_id, side=Side.SELL, price=9_450, qty=111)
    _filled(repo, other.cycle_id, side=Side.SELL, price=1_000_000, qty=1)
    assert repo.realized_pnl_for_cycle(cycle_id) == 9_450 * 111


# ── Fix Round 1, Finding 1/2: 집계는 status 가 아니라 체결 데이터를 본다 ──────

def test_realized_pnl_counts_a_partially_filled_buy_that_was_then_canceled(
    repo_and_cycle,
):
    """설계서 200행의 정상 절차: 부분체결 매수는 체결분만으로 확정하고 잔량을
    취소한다. 그 주문은 CANCELED 로 끝나지만 체결분의 취득원가는 실현손익
    계산에 남아야 한다 — 아니면 나중에 그 40주를 팔았을 때 매수 원가 없이
    매도 금액만 잡혀 이익이 380,000원 과대평가된다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    repo.update_order_log(client_ref=ref, status="PARTIAL", broker_order_id="B",
                          fill_price=9_500, fill_qty=40, settled_at=T0)
    repo.update_order_log(client_ref=ref, status="CANCELED")  # 잔량 65주 취소
    _filled(repo, cycle_id, side=Side.SELL, price=9_980, qty=40)
    assert repo.realized_pnl_for_cycle(cycle_id) == 40 * (9_980 - 9_500)


def test_realized_pnl_excludes_rejected_orders_even_with_fill_data(repo_and_cycle):
    """REJECTED 인 행에 체결값이 있다면 그 자체가 손상이다 — 세지 않는다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_000, req_qty=100)
    repo.update_order_log(client_ref=ref, status="REJECTED", fill_price=9_000,
                          fill_qty=100, settled_at=T0)
    assert repo.realized_pnl_for_cycle(cycle_id) == 0


# ── Fix Round 1, Finding 3: update_order_log 는 없는 client_ref 를 거부한다 ──

def test_update_order_log_raises_for_unknown_client_ref(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    with pytest.raises(OrderLogNotFound):
        repo.update_order_log(client_ref="does-not-exist", status="FILLED")


# ── Fix Round 1, Finding 4: 종결 상태에서 역행할 수 없다 ────────────────────

def test_update_order_log_refuses_to_regress_out_of_terminal_status(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=10_000, req_qty=100)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=10_000,
                          fill_qty=100, settled_at=T0)
    with pytest.raises(OrderLogInvariantError):
        repo.update_order_log(client_ref=ref, status="ACCEPTED")


def test_update_order_log_allows_idempotent_reconfirmation_of_terminal_status(
    repo_and_cycle,
):
    """설계서 9절의 UNKNOWN 재조회는 같은 결말의 재확인일 수 있다 — 재시도가
    안전해야 한다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=10_000, req_qty=100)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=10_000,
                          fill_qty=100, settled_at=T0)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=10_000,
                          fill_qty=100, settled_at=T0)  # 재확인 — 예외 없음
    assert repo.realized_pnl_for_cycle(cycle_id) == -10_000 * 100


def test_update_order_log_allows_partial_to_filled(repo_and_cycle):
    """PARTIAL 은 종결이 아니다 — 최종 수량으로 갱신될 수 있다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    repo.update_order_log(client_ref=ref, status="PARTIAL", fill_price=9_500,
                          fill_qty=40, settled_at=T0)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=9_500,
                          fill_qty=105, settled_at=T0)  # 예외 없음
    assert repo.realized_pnl_for_cycle(cycle_id) == -9_500 * 105


def test_update_order_log_allows_partial_to_canceled(repo_and_cycle):
    """PARTIAL 은 종결이 아니다 — 잔량 취소로 끝날 수 있다(설계서 200행)."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    repo.update_order_log(client_ref=ref, status="PARTIAL", fill_price=9_500,
                          fill_qty=40, settled_at=T0)
    repo.update_order_log(client_ref=ref, status="CANCELED")  # 예외 없음
    assert repo.realized_pnl_for_cycle(cycle_id) == -9_500 * 40


# ── Fix Round 1, Finding 5: 종결된 체결값은 다른 값으로 덮어쓸 수 없다 ──────

def test_update_order_log_refuses_to_overwrite_settled_fill(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=10_000, req_qty=100)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=10_000,
                          fill_qty=100, settled_at=T0)
    with pytest.raises(OrderLogInvariantError):
        repo.update_order_log(client_ref=ref, status="FILLED", fill_price=1,
                              fill_qty=1, settled_at=T0)


def test_update_order_log_allows_retry_with_no_fill_values_after_terminal(
    repo_and_cycle,
):
    """None 은 COALESCE 로 기존 값을 유지한다 — 덮어쓰기가 아니다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=10_000, req_qty=100)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=10_000,
                          fill_qty=100, settled_at=T0)
    repo.update_order_log(client_ref=ref, status="FILLED")  # 예외 없음
    assert repo.realized_pnl_for_cycle(cycle_id) == -10_000 * 100


# ── Fix Round 1, Finding 6: fill_qty 는 req_qty 를 넘을 수 없다 ─────────────

def test_update_order_log_refuses_fill_qty_exceeding_req_qty(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=10_000, req_qty=100)
    with pytest.raises(OrderLogInvariantError):
        repo.update_order_log(client_ref=ref, status="FILLED", fill_price=10_000,
                              fill_qty=99_999, settled_at=T0)


def test_update_order_log_allows_fill_qty_equal_to_req_qty(repo_and_cycle):
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=10_000, req_qty=100)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=10_000,
                          fill_qty=100, settled_at=T0)  # 예외 없음
    assert repo.realized_pnl_for_cycle(cycle_id) == -10_000 * 100


# ── Fix Round 3, Finding 11: fill_qty 는 누적값이다, 증분이 아니다 ─────────

def test_fill_qty_is_cumulative_not_incremental(repo_and_cycle):
    """`update_order_log` 의 `fill_qty` 는 그 주문이 지금까지 체결한 총
    수량으로 채워야 한다 — 이 계약이 지켜지지 않고 매 부분체결 이벤트의
    수량을 그대로(증분으로) 넘기면 총 매수량이 실제보다 커져 원가가
    과소평가되고 실현손익이 과대평가된다. 여기서는 PARTIAL(40주) 뒤에
    FILLED(누적 105주)로 갱신한 뒤, "증분으로 읽었다면" 나올 값(40+105=145주
    매수)과 실제(누적, 105주 매수) 답이 다름을 보여, 이 리포지토리가 실제로
    누적 값으로 집계함을 고정한다."""
    repo, cycle_id = repo_and_cycle
    ref = an_order(repo, cycle_id, side=Side.BUY, req_price=9_500, req_qty=105)
    repo.update_order_log(client_ref=ref, status="PARTIAL", fill_price=9_500,
                          fill_qty=40, settled_at=T0)
    repo.update_order_log(client_ref=ref, status="FILLED", fill_price=9_500,
                          fill_qty=105, settled_at=T0)  # 누적 105주 — 증분 아님
    _filled(repo, cycle_id, side=Side.SELL, price=9_980, qty=105)

    actual = repo.realized_pnl_for_cycle(cycle_id)
    naive_incremental = 9_980 * 105 - 9_500 * (40 + 105)  # 잘못된 읽기라면 이 값
    correct_cumulative = 9_980 * 105 - 9_500 * 105

    assert actual != naive_incremental
    assert actual == correct_cumulative
