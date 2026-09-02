# AutoTrading 7s — 엔진 + G2 게이트 (Plan 2B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 도메인 코어(Plan 1)와 영속성 계층(Plan 2A) 위에 실행 엔진을 얹어, 시뮬레이션 브로커로 전 사이클과 12건의 실패 경로를 검증한다 (설계서 15.2절 G2).

**Architecture:** `engine/` 은 `ports/` 와 `domain/` 만 의존하는 순수 조립층이다. 시간·브로커·리포지토리가 전부 주입되므로 테스트가 실제로 잠들지 않는다. `app/` 은 GUI 와 엔진 사이의 메시지 계약과 스레드 브리지만 담당하며, `cli.py` 가 GUI 없이 같은 엔진을 띄운다.

**Tech Stack:** Python 3.12, `asyncio`, `sqlite3`, `tomllib` (전부 표준 라이브러리). 테스트는 `pytest` + `pytest-asyncio` (`asyncio_mode = "strict"` — 모든 async 테스트에 `@pytest.mark.asyncio` 가 필요하다).

**Spec:** `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`

**선행 기록 (읽지 않아도 되지만 근거가 여기 있다):**
- `docs/superpowers/records/2026-09-02-plan2a-handover-to-2b.md` — 이 계획이 해소해야 하는 9건
- `docs/superpowers/records/2026-09-01-plan1-global-constraints.md` — 승계하는 전역 제약
- `docs/superpowers/records/2026-09-02-plan2a-defect-patterns.md` — 리뷰가 찾을 결함 유형 5가지

---

## Global Constraints

Plan 1·2A 의 제약을 **전부 승계한다.** 아래는 그중 이 계획에서 실제로 위반될 수 있는 것과 이 계획에서 새로 추가되는 것이다.

- **Python 3.12** 이상. `from __future__ import annotations` 를 모든 모듈의 docstring 직후 첫 import 로 둔다.
- 파이썬 실행은 반드시 **`.venv/bin/python`** (3.12.13). 시스템 `python3` 는 3.9 이며 `slots=True` 에서 실패한다. 테스트는 `.venv/bin/python -m pytest`.
- **`domain/` 은 표준 라이브러리 외 어떤 것도 import 하지 않는다.**
- **`engine/` 과 `app/` 은 `adapters/` 를 import 하지 않는다.** 의존 방향은 `domain ← ports ← {adapters, engine} ← app`. 어댑터는 생성자로 주입된다. 테스트만 `adapters/` 를 import 한다.
- **금액·가격은 원 단위 `int`, 비율만 `Decimal`.** 금액 계산에 `float` 금지.
- **자동 트리거 경로는 시장가를 표현할 수 없다.** `LimitOrderRequest.price` 는 필수다. 시장가는 `MarketSellRequest`(긴급청산 전용, `reason` 필수)뿐이다.
- **`decide()` 에 하락 조건 매도 분기를 두지 않는다.** 자동 손절매 배제.
- **앱키·시크릿·접근토큰을 DB 에 평문 저장하지 않는다.** 이 계획은 인증을 다루지 않으므로 위반 경로 자체가 없어야 한다 — `engine/`·`app/` 어디에도 자격증명 문자열을 두지 않는다.
- **도메인의 모든 `datetime` 은 tz-aware.** 엔진이 만드는 시각도 전부 tz-aware 여야 한다 (`ClockPort.now()` 를 쓰고 `datetime.now()` 를 직접 부르지 않는다).
- **`fill_qty` 는 누적, `fill_price` 는 수량가중평균.** 브로커가 `OrderStatus` 로 보고하는 값을 그대로 `update_order_log` 에 넘긴다. 증분으로 다루면 실현손익이 부풀려진다 (Plan 2A 최악의 결함과 같은 방향).
- **`order_log` 쓰기는 엔진 스레드의 단일 연결에서만.** 두 번째 쓰기 연결을 만들지 않는다 (2A 핸드오버 3).
- **엔진 코드에 넓은 `except ValueError` 를 두지 않는다.** `CorruptRowError` 가 `ValueError` 의 하위이므로 DB 손상을 삼킨다 (2A 핸드오버 7).
- **실시간 대기에 `asyncio.sleep` 을 직접 부르지 않는다.** 주입된 `sleep` 을 쓴다 — 테스트가 실제로 잠들면 12건의 시나리오를 돌릴 수 없다.
- **주문 빈도 제한의 "지금" 은 틱의 시각(`tick.at`)이다.** 빈도는 시장 시간 기준으로 세는 것이 맞고, 시계를 쓰면 시세 스크립트만으로는 창이 미끄러지지 않아 11번째 주문부터 전부 막힌다. 그 밖의 시각(주문 기록, 이벤트, 대사 주기)은 `ClockPort.now()` 를 쓴다.
- 커밋 메시지는 한국어 본문 + Conventional Commits 접두어. `git add` 는 브리프가 지정한 경로만. **`git add -A` 금지.**
- 브랜치는 `feat/engine`.

### 트리거 판정 규칙 (설계서 5절 — 도메인이 이미 강제한다)

| 규칙 | 내용 |
|---|---|
| 규칙 1 | 한 틱에서 매도를 매수보다 먼저 평가. 매도가 하나라도 있으면 그 틱은 매도만 집행 |
| 규칙 2 | 한 틱에 매수는 1단계씩만, 낮은 번호부터 |
| 규칙 3 | 재매수 쿨다운 (기본 60초) |
| 규칙 4 | 장 운영시간 밖에서는 어떤 결정도 내리지 않음 |
| 규칙 5 | PENDING 상태 단계는 판정 대상에서 제외 |

엔진은 이 규칙을 **재구현하지 않는다.** `rules.decide()` 를 부르고 그 결과를 집행할 뿐이다. 엔진 안에 "이 단계가 PENDING 이면 건너뛴다" 같은 코드가 생기면 그것은 규칙의 중복이며 리뷰 대상이다.

### 설계서 9절 주문 실행 파이프라인 (이 계획의 심장)

```
① decide() → BuyStage(3)
② guards 검사 (총한도·빈도·장중)   실패 시 로그만 남기고 종료
③ order_log INSERT  status=SENDING, client_ref=uuid, trigger_reason 기록
④ stage_state UPDATE  WAITING → BUY_PENDING          ← 여기서 커밋
⑤ broker.place_limit_order()
    ├─ 성공        → order_log: ACCEPTED, broker_order_id 저장
    ├─ 명시적 거부 → order_log: REJECTED, stage → WAITING 복구
    └─ 타임아웃 / 네트워크 오류
          → order_log: UNKNOWN
          → 재발주 금지 (D12)
          → list_orders_today(code)로 client_ref 대조하여 접수 여부 확인
              ├─ 접수됨 → ACCEPTED로 정정, 체결 대기 계속
              └─ 미접수 → stage → WAITING 복구
⑥ 체결 대기
    ├─ 전량체결        → stage → HOLDING (fill_price, fill_qty)
    ├─ 3초 후 부분체결 → 잔량 취소, 체결분으로 HOLDING 확정
    └─ 3초 후 미체결   → 취소 → WAITING (다음 틱에 재시도)
```

**③④의 순서는 타협 불가다.** 발주보다 먼저 기록하고 커밋한다. 근거(설계서 9절): *"잘못 기록된 쪽이 잘못 잊힌 쪽보다 항상 낫다."* 2A 의 `save_stage` 가드가 전이표를 참조하므로, 두 홉을 합성해 한 번만 저장하면 거부된다 — 이것은 버그가 아니라 이 순서의 강제다.

**⑤의 UNKNOWN 분기가 이 시스템에서 가장 중요한 부분이다.** 응답이 없으면 재발주가 아니라 조회로 사실을 확인한다.

---

## 이 계획이 해소하는 핸드오버

| # | 출처 | 내용 | 해소 태스크 |
|---|---|---|---|
| P1-1 | Plan 1 | 긴급청산은 `guards.check_sell` 을 거쳐서는 안 된다 (`max_orders_per_minute=0` 이 유일한 탈출구를 막는다) | 2, 7 |
| P1-2 | Plan 1 | 한 틱이 여러 매도를 낼 수 있으므로 결정 사이에 guard 컨텍스트를 증가시켜야 한다 | 2, 10 |
| P1-3 | Plan 1 | `Balance.qty_of` 가 없는 종목에 0 을 반환한다. 긴급청산은 "응답에 없음"과 "보유 0"을 구분해야 한다 | 7 |
| P1-5 | Plan 1 | `is_cycle_complete([])` 가 `DomainInvariantError` 를 던진다. 엔진이 흡수해야 한다 | 9 |
| 2A-1 | Plan 2A | **D20 강제 종료의 쓰기 경로가 통째로 없다** | 6 |
| 2A-2 | Plan 2A | `cycle.realized_pnl` 을 쓸 포트 메서드가 없다 | 6 |
| 2A-4 | Plan 2A | **`FakeBroker` 는 거부할 줄 모른다** — 이 더블로 투입한도를 검증하면 아무것도 검증하지 않는 것이다 | 3 |
| 2A-6 | Plan 2A | `fill_qty` 누적 / `fill_price` 가중평균 의미론 | 5 |
| 2A-7 | Plan 2A | `load_stages` 는 fail-closed 이고 복구 API 가 없다. `CorruptRowError` 에 크래시 루프보다 나은 답이 필요하다 | 9 |
| 2A-9 | Plan 2A | `save_stage` 가드가 설계서 9절의 홉별 커밋을 강제한다 | 4, 5 |

2A 핸드오버 3(단일 쓰기 연결), 5(마이그레이션 경로), 8(`token_session` 접근자)은 이 계획의 범위 밖이다. 3 은 전역 제약으로 승계하고, 5 와 8 은 Plan 3(키움 어댑터·인증)의 몫이다.

---

## File Structure

```
src/autotrading7s/
├── app/
│   ├── __init__.py
│   ├── commands.py       GUI → 엔진 명령. PriorityCommand 가 priority_q 자격을 타입으로 표현
│   ├── events.py         엔진 → GUI 이벤트
│   ├── settings.py       EngineSettings (settings.toml 로딩, tomllib)
│   └── engine_thread.py  스레드 브리지 — 큐 소유, 기동·종료
├── engine/
│   ├── __init__.py
│   ├── guards.py         GuardContext 조립 + 분당 주문 카운터 (상태 있음)
│   ├── executor.py       설계서 9절 파이프라인 — 주문 1건의 생애
│   ├── emergency.py      설계서 11절 긴급청산 + D20 강제 종료
│   ├── reconciler.py     설계서 10.2절 대사
│   ├── recovery.py       설계서 10.1절 재시작 복구
│   └── orchestrator.py   asyncio 태스크 조립, 큐 소비, 틱 루프
└── cli.py                headless 기동
```

`engine/` 을 6개 파일로 나눈 기준은 **누가 그것을 호출하는가**다. `executor` 는 틱 루프가 부르고, `emergency` 는 `priority_q` 가 부르고, `reconciler` 는 5분 타이머가 부르고, `recovery` 는 기동 시 한 번 불린다. 호출자가 다르면 파일이 다르다 — 그래야 한 태스크가 한 파일을 소유하고 리뷰 단위가 깨끗해진다.

테스트:
```
tests/app/test_commands.py  test_events.py  test_settings.py  test_engine_thread.py
tests/engine/test_guards.py  test_executor_send.py  test_executor_fill.py
             test_emergency.py  test_force_close.py  test_reconciler.py
             test_recovery.py  test_orchestrator.py
tests/domain/test_cycle_force_close.py
tests/adapters/test_fake_broker_validate.py
tests/adapters/test_repository_force_close.py
tests/test_g2_gate.py
```

---

## Task 1: 큐 메시지 계약과 엔진 설정

**이 태스크를 맨 앞에 두는 이유:** Plan 4(GUI)가 의존하는 것은 이 계약 하나뿐이다. 이것이 커밋되는 순간 GUI 작업을 병행할 수 있다. 반대로 이것을 마지막에 두면 Plan 4 는 Plan 2B 전체를 기다린다.

**Files:**
- Create: `src/autotrading7s/app/__init__.py`, `src/autotrading7s/app/commands.py`, `src/autotrading7s/app/events.py`, `src/autotrading7s/app/settings.py`
- Test: `tests/app/__init__.py`, `tests/app/test_commands.py`, `tests/app/test_events.py`, `tests/app/test_settings.py`

**Interfaces:**
- Consumes: `autotrading7s.domain.types` 의 `CloseReason`, `TickSource`
- Produces: 아래 명령·이벤트 타입 전부, `PRIORITY_COMMANDS`, `EngineSettings`, `load_settings`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 명령 계약**

`tests/app/test_commands.py`:

```python
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from autotrading7s.app.commands import (
    Command,
    EmergencyLiquidate,
    ForceClose,
    PauseCycle,
    PriorityCommand,
    ResetReconcileBaseline,
    ResumeCycle,
    Shutdown,
    StartCycle,
    StopCycle,
)


def test_all_commands_are_frozen_dataclasses():
    for cls in (StartCycle, PauseCycle, ResumeCycle, StopCycle, EmergencyLiquidate,
                ForceClose, ResetReconcileBaseline, Shutdown):
        assert dataclasses.is_dataclass(cls), cls
        assert cls.__dataclass_params__.frozen, cls


def test_only_emergency_commands_are_priority():
    """priority_q 자격이 타입으로 표현된다 — 설계서 7.1절.

    긴급 기능의 즉시성을 주석이 아니라 타입이 보장해야 한다. 오케스트레이터가
    priority_q 에서 꺼낸 것이 PriorityCommand 인지 단정할 수 있어야 한다.
    """
    assert issubclass(EmergencyLiquidate, PriorityCommand)
    assert issubclass(ForceClose, PriorityCommand)
    assert not issubclass(StartCycle, PriorityCommand)
    assert not issubclass(StopCycle, PriorityCommand)
    # PriorityCommand 는 Command 의 하위여야 명령 소비 태스크가 하나로 다룬다
    assert issubclass(PriorityCommand, Command)


def test_force_close_requires_nonempty_reason():
    """D20 — 증언 기록을 타입이 강제한다 (설계서 11.4절 설계 제약)."""
    with pytest.raises(ValueError, match="reason"):
        ForceClose(config_id=1, reason="", confirmed_text="강제종료")
    with pytest.raises(ValueError, match="reason"):
        ForceClose(config_id=1, reason="   ", confirmed_text="강제종료")


def test_force_close_requires_exact_confirmation_text():
    """설계서 11.4절 — `강제종료` 를 직접 입력해야 한다."""
    with pytest.raises(ValueError, match="강제종료"):
        ForceClose(config_id=1, reason="거래정지", confirmed_text="네")
    cmd = ForceClose(config_id=1, reason="거래정지", confirmed_text="강제종료")
    assert cmd.reason == "거래정지"


def test_emergency_liquidate_all_scope_has_no_config_id():
    """전체 청산은 종목을 지정하지 않는다 (설계서 11.2절)."""
    cmd = EmergencyLiquidate(scope="ALL", config_id=None, reason=None,
                             confirmed_text="전체청산")
    assert cmd.scope == "ALL"
    with pytest.raises(ValueError, match="config_id"):
        EmergencyLiquidate(scope="SINGLE", config_id=None, reason=None,
                           confirmed_text=None)
    with pytest.raises(ValueError, match="전체청산"):
        EmergencyLiquidate(scope="ALL", config_id=None, reason=None,
                           confirmed_text=None)


def test_emergency_liquidate_rejects_unknown_scope():
    with pytest.raises(ValueError, match="scope"):
        EmergencyLiquidate(scope="EVERYTHING", config_id=None, reason=None,
                           confirmed_text="전체청산")


def test_start_cycle_carries_config_id_only():
    """앵커 확정은 엔진이 첫 틱에서 한다 — GUI 가 가격을 정하지 않는다."""
    cmd = StartCycle(config_id=7)
    assert dataclasses.asdict(cmd) == {"config_id": 7}


def test_reset_reconcile_baseline_targets_a_stock():
    """2A 핸드오버 7 / 설계서 11.4절 — 강제 종료 기준선 초기화."""
    cmd = ResetReconcileBaseline(stock_code="005930")
    assert cmd.stock_code == "005930"


def test_shutdown_is_a_plain_command():
    assert isinstance(Shutdown(), Command)
    assert not isinstance(Shutdown(), PriorityCommand)
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/app/test_commands.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.app'`

- [ ] **Step 3: 실패하는 테스트를 쓴다 — 이벤트 계약**

`tests/app/test_events.py`:

```python
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from autotrading7s.app.events import (
    CycleClosed,
    CycleLoadFailed,
    EmergencyResult,
    Event,
    EngineStopped,
    GuardBlocked,
    OrderRejected,
    OrderUnknown,
    QuoteFallback,
    ReconcileMismatch,
    StageFilled,
    TickUpdate,
)
from autotrading7s.domain.types import CloseReason, TickSource

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)

ALL_EVENTS = (
    StageFilled, CycleClosed, CycleLoadFailed, ReconcileMismatch, QuoteFallback,
    OrderRejected, OrderUnknown, EmergencyResult, GuardBlocked, TickUpdate,
    EngineStopped,
)


def test_all_events_are_frozen_and_subclass_event():
    for cls in ALL_EVENTS:
        assert dataclasses.is_dataclass(cls), cls
        assert cls.__dataclass_params__.frozen, cls
        assert issubclass(cls, Event), cls


def test_every_event_carries_a_tz_aware_timestamp():
    """naive 시각이 GUI 로 새면 화면의 시각 표시가 조용히 틀린다.

    도메인 전체가 tz-aware 이므로 경계에서도 같은 규칙을 강제한다.
    """
    naive = datetime(2026, 9, 2, 10, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        TickUpdate(stock_code="005930", price=10_000, source=TickSource.WS, at=naive)


def test_stage_filled_reports_cumulative_fill():
    ev = StageFilled(config_id=1, cycle_id=1, stage_no=3, side="BUY",
                     fill_price=9_500, fill_qty=105, at=AT)
    assert ev.fill_qty == 105


def test_cycle_closed_carries_reason_and_realized_pnl():
    ev = CycleClosed(config_id=1, cycle_id=1, reason=CloseReason.NORMAL,
                     realized_pnl=19_200, at=AT)
    assert ev.reason is CloseReason.NORMAL
    assert ev.realized_pnl == 19_200


def test_reconcile_mismatch_names_the_verdict():
    """설계서 10.2절 — 세 판정 중 하나."""
    ev = ReconcileMismatch(stock_code="005930", internal_qty=433, broker_qty=400,
                           verdict="INTERNAL_MORE", action_taken="PAUSED", at=AT)
    assert ev.verdict == "INTERNAL_MORE"
    with pytest.raises(ValueError, match="verdict"):
        ReconcileMismatch(stock_code="005930", internal_qty=1, broker_qty=1,
                          verdict="WHATEVER", action_taken=None, at=AT)


def test_quote_fallback_says_which_direction():
    """설계서 8.4절 — 폴백 구간을 로깅해야 하므로 진입·복귀가 구분돼야 한다."""
    assert QuoteFallback(stock_codes=("005930",), active=True, at=AT).active is True
    assert QuoteFallback(stock_codes=("005930",), active=False, at=AT).active is False


def test_order_unknown_is_distinct_from_order_rejected():
    """D12 — UNKNOWN 은 재발주 금지 상태이고 REJECTED 는 복구 완료 상태다.

    두 개를 한 이벤트로 합치면 GUI 가 "확인 중" 과 "실패" 를 같은 색으로
    보여주게 되고, 사용자가 개입할 시점을 알 수 없다.
    """
    assert OrderUnknown is not OrderRejected
    unknown = OrderUnknown(config_id=1, cycle_id=1, stage_no=3,
                           client_ref="abc", at=AT)
    rejected = OrderRejected(config_id=1, cycle_id=1, stage_no=3,
                             api_code="40510", api_message="거부", at=AT)
    assert unknown.client_ref == "abc"
    assert rejected.api_code == "40510"


def test_cycle_load_failed_carries_the_corruption_message():
    """2A 핸드오버 7 — 손상된 행 하나가 사이클을 로드 불가로 만든다.

    엔진이 크래시하는 대신 이 이벤트로 사용자에게 나갈 길을 준다.
    """
    ev = CycleLoadFailed(config_id=1, cycle_id=4,
                         detail="trigger_price mismatch in stage_state (id=9)",
                         action_taken="PAUSED", at=AT)
    assert "stage_state" in ev.detail


def test_emergency_result_covers_the_five_schema_results():
    """emergency_liquidation_log.result 의 CHECK 와 같은 집합이어야 한다."""
    from autotrading7s.app.events import EMERGENCY_RESULTS
    assert EMERGENCY_RESULTS == frozenset(
        {"SUCCESS", "PARTIAL", "FAILED", "REJECTED_CLOSED_MARKET", "FORCED_CLOSE"}
    )
    with pytest.raises(ValueError, match="result"):
        EmergencyResult(scope="SINGLE", stock_code="005930", result="MAYBE",
                        qty_before=40, qty_after=0, canceled_orders=1,
                        detail=None, at=AT)


def test_guard_blocked_carries_the_domain_reason_verbatim():
    """가드 거부 이유는 도메인이 만든 문자열을 그대로 전달한다.

    엔진이 문구를 다시 쓰면 한도 숫자가 두 곳에 생기고 어긋난다.
    """
    ev = GuardBlocked(config_id=1, stage_no=4, side="BUY",
                      reason="종목 총한도 초과: 누적 1,000,000 + 예상 500,000 > 한도 1,200,000",
                      at=AT)
    assert "총한도" in ev.reason
```

- [ ] **Step 4: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/app/test_events.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 5: 실패하는 테스트를 쓴다 — 설정**

`tests/app/test_settings.py`:

```python
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
```

- [ ] **Step 6: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/app -q`
Expected: FAIL — 세 파일 모두 `ModuleNotFoundError`

- [ ] **Step 7: 구현한다**

`src/autotrading7s/app/__init__.py` 는 빈 파일이다. `tests/app/__init__.py` 도 빈 파일이다.

`src/autotrading7s/app/commands.py`:

```python
"""GUI → 엔진 명령 — 설계서 7.1절.

GUI 와 엔진은 메시지로만 대화한다. 이 경계 덕분에 향후 프로세스 분리는 큐를
소켓으로 교체하는 작업으로 축소된다.

`PriorityCommand` 가 이 모듈의 핵심이다. 설계서 7.1절은 `priority_q` 가 긴급
기능의 즉시성을 **구조적으로** 보장한다고 규정하는데, 어떤 명령이 그 큐에 들어갈
자격이 있는지가 주석에만 있으면 구조가 아니다. 타입으로 표현하면 오케스트레이터가
`isinstance` 로 단정할 수 있고, 새 명령을 추가하는 사람이 우선순위를 의식적으로
선택하게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

_EMERGENCY_SCOPES = frozenset({"SINGLE", "ALL"})
FORCE_CLOSE_CONFIRMATION = "강제종료"
LIQUIDATE_ALL_CONFIRMATION = "전체청산"


class Command:
    """모든 명령의 기반. 명령 소비 태스크가 하나의 타입으로 다룬다."""


class PriorityCommand(Command):
    """`priority_q` 에 들어갈 자격이 있는 명령 — 긴급청산 계열뿐이다."""


@dataclass(frozen=True, slots=True)
class StartCycle(Command):
    """사이클 시작. 앵커 가격은 엔진이 첫 틱에서 확정한다."""
    config_id: int


@dataclass(frozen=True, slots=True)
class PauseCycle(Command):
    config_id: int


@dataclass(frozen=True, slots=True)
class ResumeCycle(Command):
    config_id: int


@dataclass(frozen=True, slots=True)
class StopCycle(Command):
    """자동 트리거 정지. 사이클 종료는 보유 0 도달로만 일어난다 (D5)."""
    config_id: int


@dataclass(frozen=True, slots=True)
class ResetReconcileBaseline(Command):
    """강제 종료된 수량의 대사 기준선을 초기화한다 (설계서 11.4절)."""
    stock_code: str


@dataclass(frozen=True, slots=True)
class Shutdown(Command):
    """엔진 정상 종료."""


@dataclass(frozen=True, slots=True)
class EmergencyLiquidate(PriorityCommand):
    """긴급청산 — 설계서 11절.

    `scope="ALL"` 은 종목을 지정하지 않고 전체를 순차 청산하며, 설계서 11.2절에
    따라 `전체청산` 텍스트 입력을 요구한다.
    """
    scope: str
    config_id: int | None
    reason: str | None
    confirmed_text: str | None

    def __post_init__(self) -> None:
        if self.scope not in _EMERGENCY_SCOPES:
            raise ValueError(
                f"scope must be one of {sorted(_EMERGENCY_SCOPES)}: {self.scope!r}"
            )
        if self.scope == "SINGLE" and self.config_id is None:
            raise ValueError("config_id is required when scope is SINGLE")
        if self.scope == "ALL":
            if self.config_id is not None:
                raise ValueError("config_id must be None when scope is ALL")
            if self.confirmed_text != LIQUIDATE_ALL_CONFIRMATION:
                raise ValueError(
                    f"scope=ALL requires confirmed_text == "
                    f"{LIQUIDATE_ALL_CONFIRMATION!r} (설계서 11.2절)"
                )


@dataclass(frozen=True, slots=True)
class ForceClose(PriorityCommand):
    """D20 강제 종료 — 설계서 11.4절.

    `reason` 이 필수인 것은 `MarketSellRequest.reason` 과 같은 발상이다. 타입이
    강제하면 증언 기록을 빼먹을 수 없다.
    """
    config_id: int
    reason: str
    confirmed_text: str

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise ValueError("reason must be a non-empty statement (설계서 11.4절)")
        if self.confirmed_text != FORCE_CLOSE_CONFIRMATION:
            raise ValueError(
                f"confirmed_text must be {FORCE_CLOSE_CONFIRMATION!r} (설계서 11.4절)"
            )


PRIORITY_COMMANDS: frozenset[type[Command]] = frozenset(
    {EmergencyLiquidate, ForceClose}
)
```

`src/autotrading7s/app/events.py`:

```python
"""엔진 → GUI 이벤트 — 설계서 7.1절.

GUI 는 DB 를 건드리지 않고 이 이벤트만 소비한다(설계서 14.4절). 그래서 화면에
필요한 것이 전부 이벤트에 실려 있어야 하며, 모든 이벤트는 tz-aware 시각을
가진다 — naive 가 새면 화면의 시각 표시가 조용히 틀린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from autotrading7s.domain.types import CloseReason, TickSource

EMERGENCY_RESULTS: frozenset[str] = frozenset(
    {"SUCCESS", "PARTIAL", "FAILED", "REJECTED_CLOSED_MARKET", "FORCED_CLOSE"}
)
RECONCILE_VERDICTS: frozenset[str] = frozenset(
    {"MATCH", "INTERNAL_LESS", "INTERNAL_MORE"}
)


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError(f"event timestamp must be tz-aware: {at!r}")


class Event:
    """모든 이벤트의 기반. `event_q` 가 하나의 타입으로 다룬다."""


@dataclass(frozen=True, slots=True)
class TickUpdate(Event):
    stock_code: str
    price: int
    source: TickSource
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class StageFilled(Event):
    """`fill_qty` 는 누적, `fill_price` 는 수량가중평균이다 (2A 핸드오버 6)."""
    config_id: int
    cycle_id: int
    stage_no: int
    side: str
    fill_price: int
    fill_qty: int
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class CycleClosed(Event):
    config_id: int
    cycle_id: int
    reason: CloseReason
    realized_pnl: int
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class CycleLoadFailed(Event):
    """복원 실패 — 2A 핸드오버 7. 크래시 대신 사용자에게 나갈 길을 준다."""
    config_id: int | None
    cycle_id: int
    detail: str
    action_taken: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class ReconcileMismatch(Event):
    stock_code: str
    internal_qty: int
    broker_qty: int
    verdict: str
    action_taken: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)
        if self.verdict not in RECONCILE_VERDICTS:
            raise ValueError(
                f"verdict must be one of {sorted(RECONCILE_VERDICTS)}: {self.verdict!r}"
            )


@dataclass(frozen=True, slots=True)
class QuoteFallback(Event):
    """설계서 8.4절 — 폴백 구간을 로깅해야 하므로 진입과 복귀를 구분한다."""
    stock_codes: tuple[str, ...]
    active: bool
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderRejected(Event):
    """명시적 거부 — 단계는 이미 WAITING 으로 복구되었다."""
    config_id: int
    cycle_id: int
    stage_no: int
    api_code: str | None
    api_message: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class OrderUnknown(Event):
    """D12 — 응답 유실. 재발주하지 않고 조회로 확인하는 중이다."""
    config_id: int
    cycle_id: int
    stage_no: int
    client_ref: str
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class EmergencyResult(Event):
    scope: str
    stock_code: str | None
    result: str
    qty_before: int | None
    qty_after: int | None
    canceled_orders: int | None
    detail: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)
        if self.result not in EMERGENCY_RESULTS:
            raise ValueError(
                f"result must be one of {sorted(EMERGENCY_RESULTS)}: {self.result!r}"
            )


@dataclass(frozen=True, slots=True)
class GuardBlocked(Event):
    """가드가 만든 이유 문자열을 그대로 옮긴다 — 한도 숫자를 다시 쓰지 않는다."""
    config_id: int
    stage_no: int
    side: str
    reason: str
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class EngineStopped(Event):
    detail: str | None
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)
```

`src/autotrading7s/app/settings.py`:

```python
"""엔진 설정 — 설계서 9절·10.2절의 조정 가능한 값들.

`total_limit` 에 기본값을 두지 않는 것이 이 모듈의 유일한 설계 결정이다. 손절매가
없는 전략에서 전체 총한도는 프로그램이 제공하는 유일한 구조적 보호장치이므로
(설계서 6절), 기본값이 조용히 적용되는 것은 무한 물타기를 묵인하는 것이다.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EngineSettings:
    total_limit: int = 0
    pending_timeout_sec: int = 3
    reconcile_interval_sec: int = 300
    max_orders_per_minute: int = 10
    rebuy_cooldown_sec: int = 60

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field.name} must be int, not {type(value).__name__}")
            if value <= 0:
                raise ValueError(f"{field.name} must be positive: {value}")


def load_settings(path: Path) -> EngineSettings:
    """`settings.toml` 의 `[engine]` 절을 읽는다.

    알 수 없는 키를 거부하는 이유: 오타난 설정 키가 조용히 무시되면 사용자는
    한도를 설정했다고 믿은 채로 기본값이 아닌 것으로 돌게 된다.
    """
    with path.open("rb") as fp:
        data = tomllib.load(fp)
    section = data.get("engine", {})
    known = {f.name for f in fields(EngineSettings)}
    unknown = sorted(set(section) - known)
    if unknown:
        raise ValueError(f"unknown settings keys in [engine]: {unknown}")
    if "total_limit" not in section:
        raise ValueError("total_limit is required in [engine] — 설계서 6절")
    return EngineSettings(**section)
```

`total_limit` 의 선언상 기본값 `0` 은 `__post_init__` 이 즉시 거부한다. 즉 "기본값이 없다"를 `dataclass` 의 필드 순서 제약(기본값 있는 필드 뒤에 없는 필드를 둘 수 없다) 안에서 표현한 것이며, 실질적으로 `total_limit` 은 필수 인자다.

- [ ] **Step 8: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/app -q`
Expected: PASS. 경고 없음.

- [ ] **Step 9: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — 기존 672건 + 이번 태스크가 모두 통과.

- [ ] **Step 10: 커밋**

```bash
git add src/autotrading7s/app tests/app
git commit -m "$(printf 'feat: GUI-엔진 큐 메시지 계약과 엔진 설정\n\n설계서 7.1절. PriorityCommand 가 priority_q 자격을 타입으로 표현한다 —\n긴급 기능의 즉시성을 주석이 아니라 구조가 보장해야 하므로 오케스트레이터가\nisinstance 로 단정할 수 있어야 한다.\n\nD20 의 증언 기록을 ForceClose.reason 필수로 강제했다. MarketSellRequest.reason\n과 같은 발상이다.\n\nOrderUnknown 과 OrderRejected 를 분리했다 — 합치면 GUI 가 "확인 중" 과 "실패" 를\n같은 색으로 보여주고 사용자가 개입 시점을 알 수 없다.\n\nEngineSettings.total_limit 에 기본값을 두지 않았다. 손절매 없는 전략에서 전체\n총한도는 유일한 구조적 보호장치이므로 조용히 적용되는 기본값은 무한 물타기의\n묵인이다.')"
```

**이 커밋 이후 Plan 4(GUI)를 병행 착수할 수 있다.**

---

## Task 2: 안전장치 조립기 (`engine/guards.py`)

**배경:** `domain/guards.py` 의 `check_buy`·`check_sell` 은 **상태 없는 술어**다. `GuardContext` 를 누가 어떻게 채우는지가 정해지지 않아 Plan 1 이 두 건을 핸드오버했다 — 긴급청산이 `check_sell` 을 거치면 안 되는 것(P1-1)과, 한 틱이 여러 매도를 낼 때 결정 사이에 카운터를 증가시켜야 하는 것(P1-2). 이 태스크가 그 책임을 한 곳에 모은다.

**Ruling (계획 작성 시 확정):** 설계서 9절 ②는 가드 검사 항목을 "총한도·빈도·장중"으로 적었지만, **장중 판정은 이 모듈에 두지 않는다.** 규칙 4(장 운영시간 밖 무동작)는 이미 `rules.decide(market_open=...)` 이 강제하며, 같은 판정을 두 곳에 두면 두 판정이 어긋날 수 있다. 가드는 한도와 빈도만 본다. 틀렸을 경우 비용: 없음에 가깝다 — `decide()` 가 장외에서 빈 리스트를 반환하므로 가드까지 도달하는 결정 자체가 없다.

**Files:**
- Create: `src/autotrading7s/engine/__init__.py`, `src/autotrading7s/engine/guards.py`
- Test: `tests/engine/__init__.py`, `tests/engine/test_guards.py`

**Interfaces:**
- Consumes: `domain.guards`(`GuardContext`, `GuardVerdict`, `check_buy`, `check_sell`), `domain.pnl.invested_amount`, `domain.rules`(`BuyStage`, `SellStage`), `ports.repository.RepositoryPort`, `app.settings.EngineSettings`
- Produces:
  - `OrderRateWindow(window_sec: int = 60)` — `.record(at: datetime)`, `.count(now: datetime) -> int`
  - `Exposure` — `per_stock: dict[str, int]`, `total: int`
  - `compute_exposure(repo: RepositoryPort) -> Exposure`
  - `GuardGate(repo: RepositoryPort, settings: EngineSettings)` — `.check_buy(decision, *, stock_code, stock_limit, now) -> GuardVerdict`, `.check_sell(decision, *, now) -> GuardVerdict`, `.record_order(at) -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/engine/test_guards.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.domain.types import CycleStatus, StageStatus
from autotrading7s.engine.guards import (
    Exposure,
    GuardGate,
    OrderRateWindow,
    compute_exposure,
)

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


# ── 분당 주문 카운터 ────────────────────────────────────────────────────
def test_rate_window_counts_orders_inside_the_window():
    w = OrderRateWindow()
    for i in range(3):
        w.record(NOW + timedelta(seconds=i))
    assert w.count(NOW + timedelta(seconds=3)) == 3


def test_rate_window_drops_orders_exactly_at_the_boundary():
    """60초 전 주문은 '지난 1분'에 포함되지 않는다.

    경계를 명시하는 이유: 포함하면 max_orders_per_minute 가 실질적으로 1건
    좁아지고, 그 1건이 매도라면 손절 없는 전략에서 탈출이 한 틱 늦어진다.
    """
    w = OrderRateWindow()
    w.record(NOW)
    assert w.count(NOW + timedelta(seconds=59, milliseconds=999)) == 1
    assert w.count(NOW + timedelta(seconds=60)) == 0


def test_rate_window_rejects_naive_datetime():
    w = OrderRateWindow()
    with pytest.raises(ValueError, match="tz-aware"):
        w.record(datetime(2026, 9, 2, 10, 0))


# ── 노출금액 집계 ───────────────────────────────────────────────────────
def test_compute_exposure_sums_holding_cost_across_active_cycles(repo_two_stocks):
    """활성 사이클 전부의 보유 원가를 종목별로, 그리고 전체로 집계한다."""
    exposure = compute_exposure(repo_two_stocks)
    assert exposure.per_stock == {"005930": 1_000_000, "000660": 600_000}
    assert exposure.total == 1_600_000


def test_compute_exposure_excludes_sold_stages(repo_with_sold_stage):
    """매도 완료된 단계는 자본이 회수됐으므로 노출이 아니다.

    한도는 '동시 노출' 을 제한하는 장치다 — 누적 지출을 제한하는 것이라면
    재매수가 허용된 설정에서 한도가 영구적으로 소진된다.
    """
    exposure = compute_exposure(repo_with_sold_stage)
    assert exposure.per_stock == {"005930": 950_000}


def test_compute_exposure_ignores_closed_cycles(repo_with_closed_cycle):
    exposure = compute_exposure(repo_with_closed_cycle)
    assert exposure.total == 0
    assert exposure.per_stock == {}


# ── 가드 판정 ───────────────────────────────────────────────────────────
def test_check_buy_allows_exactly_at_the_limit(repo_two_stocks):
    """누적 + 예상 == 한도 는 허용된다 (도메인 check_buy 의 경계와 같다)."""
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=1_700_000))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    verdict = gate.check_buy(decision, stock_code="005930",
                             stock_limit=1_100_000, now=NOW)
    assert verdict.allowed is True


def test_check_buy_blocks_one_won_over_the_total_limit(repo_two_stocks):
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=1_699_999))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    verdict = gate.check_buy(decision, stock_code="005930",
                             stock_limit=99_999_999, now=NOW)
    assert verdict.allowed is False
    assert "전체 총한도" in verdict.reason


def test_check_buy_uses_the_right_stocks_exposure(repo_two_stocks):
    """종목별 한도는 그 종목의 노출만 봐야 한다.

    per_stock 조회에서 종목 코드를 잘못 쓰면 다른 종목의 노출로 판정하게
    되고, 한도가 조용히 어긋난다 — 이 프로그램의 유일한 보호장치가 틀린
    숫자로 동작한다는 뜻이다.
    """
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    # 000660 의 노출은 600,000 이므로 700,000 한도 안에 들어간다
    assert gate.check_buy(decision, stock_code="000660",
                          stock_limit=700_000, now=NOW).allowed is True
    # 005930 의 노출은 1,000,000 이므로 같은 한도에서 막힌다
    blocked = gate.check_buy(decision, stock_code="005930",
                             stock_limit=700_000, now=NOW)
    assert blocked.allowed is False
    assert "종목 총한도" in blocked.reason


def test_unknown_stock_has_zero_exposure(repo_two_stocks):
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999))
    decision = BuyStage(stage_no=1, limit_price=10_000, qty=10, reason="r")
    assert gate.check_buy(decision, stock_code="035720",
                          stock_limit=100_000, now=NOW).allowed is True


def test_record_order_shrinks_the_budget_within_one_tick(repo_two_stocks):
    """Plan 1 핸드오버 2 — 한 틱이 여러 매도를 낼 수 있다.

    check_sell 은 상태 없는 술어이므로, 결정과 결정 사이에 record_order 를
    부르지 않으면 분당 3건 제한에서 한 틱에 7건이 나간다. 그 7건은 실제로
    브로커에 도달하고 호출 제한에 걸려 일부가 조용히 실패한다.
    """
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999,
                                                     max_orders_per_minute=2))
    sells = [SellStage(stage_no=n, limit_price=10_000, qty=10, reason="r")
             for n in (4, 3, 2)]
    results = []
    for s in sells:
        verdict = gate.check_sell(s, now=NOW)
        results.append(verdict.allowed)
        if verdict.allowed:
            gate.record_order(NOW)
    assert results == [True, True, False]


def test_recorded_orders_expire_after_the_window(repo_two_stocks):
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=99_999_999,
                                                     max_orders_per_minute=1))
    decision = SellStage(stage_no=4, limit_price=10_000, qty=10, reason="r")
    gate.record_order(NOW)
    assert gate.check_sell(decision, now=NOW + timedelta(seconds=30)).allowed is False
    assert gate.check_sell(decision, now=NOW + timedelta(seconds=60)).allowed is True


def test_verdict_reason_comes_from_the_domain_verbatim(repo_two_stocks):
    """가드 이유 문자열을 엔진이 다시 쓰지 않는다.

    다시 쓰면 한도 숫자의 서식이 두 곳에 생기고, GuardBlocked 이벤트로 화면에
    나가는 문구가 도메인 테스트가 고정한 것과 달라진다.
    """
    gate = GuardGate(repo_two_stocks, EngineSettings(total_limit=1))
    decision = BuyStage(stage_no=2, limit_price=10_000, qty=10, reason="r")
    verdict = gate.check_buy(decision, stock_code="005930",
                             stock_limit=99_999_999, now=NOW)
    assert verdict.reason.startswith("전체 총한도 초과: 누적 1,600,000")
```

**픽스처.** `tests/engine/conftest.py` 를 만들고 세 픽스처를 둔다. 리포지토리는 실제 `SqliteRepository` 를 `tmp_path` 위에 만든다 — 가짜 리포지토리를 쓰면 `load_active_cycles` 가 CLOSED 를 제외하는지를 검증할 수 없다.

```python
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
    """단계 1..len(fills) 를 만들고 fills 의 (price, qty) 로 HOLDING 을 만든다.

    fills 의 원소가 None 이면 그 단계는 SOLD 로 둔다.
    """
    config_id = repo.save_config(_config(code, name, amount=amount, limit=limit))
    # create_cycle 이 이미 STARTING 을 반환한다 — cycle.start() 를 다시 부르면
    # STARTING → STARTING 으로 IllegalCycleTransition 이 난다.
    cyc = repo.create_cycle(config_id, AT)
    config = repo.load_config(config_id)
    ladder = config.to_ladder(anchor_price=10_000)
    cyc = cycle_mod.confirm_anchor(cyc, anchor_price=10_000, ladder=ladder, at=AT)
    repo.save_cycle(cyc)
    for n, fill in enumerate(fills, start=1):
        st = stage_mod.StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n), planned_qty=ladder.planned_qty(n),
        )
        if fill is not None:
            price, qty = fill
            st = stage_mod.to_holding(stage_mod.to_buy_pending(st),
                                      fill_price=price, fill_qty=qty, at=AT)
        repo.save_stage(cyc.cycle_id, st)
    # 사다리 전체 단계 집합이 있어야 load_stages 가 통과한다 (H3)
    for n in range(len(fills) + 1, ladder.max_stages + 1):
        repo.save_stage(cyc.cycle_id, stage_mod.StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n),
            planned_qty=ladder.planned_qty(n),
        ))
    return config_id, cyc


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
    config_id, cyc = _seed(repo, code="005930", name="삼성전자", amount=500_000,
                           limit=99_999_999, fills=[(9_500, 100), (9_000, 100)])
    stages = repo.load_stages(cyc.cycle_id)
    second = next(s for s in stages if s.stage_no == 2)
    second = stage_mod.after_sell(stage_mod.to_sell_pending(second),
                                  at=AT, allow_rebuy=False)
    repo.save_stage(cyc.cycle_id, second)
    return repo


@pytest.fixture
def repo_with_closed_cycle(tmp_path):
    """CLOSED 사이클만 있는 리포지토리 — load_active_cycles 가 제외해야 한다."""
    repo = _new_repo(tmp_path)
    config_id, cyc = _seed(repo, code="005930", name="삼성전자", amount=500_000,
                           limit=99_999_999, fills=[(9_500, 100)])
    stages = repo.load_stages(cyc.cycle_id)
    sold = []
    for s in stages:
        if s.status is StageStatus.HOLDING:
            s = stage_mod.after_sell(stage_mod.to_sell_pending(s),
                                     at=AT, allow_rebuy=False)
        sold.append(s)
        repo.save_stage(cyc.cycle_id, s)
    repo.save_cycle(cycle_mod.close(cyc, reason=CloseReason.NORMAL,
                                    at=AT, states=sold))
    return repo
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/engine/test_guards.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.engine'`

- [ ] **Step 3: 구현한다**

`src/autotrading7s/engine/__init__.py` 는 빈 파일이다.

`src/autotrading7s/engine/guards.py`:

```python
"""안전장치 조립 — 설계서 6절·9절 ②.

`domain/guards.py` 의 판정은 상태 없는 술어다. 이 모듈은 그 술어에 컨텍스트를
공급하는 책임만 진다: 노출금액을 리포지토리에서 집계하고, 분당 주문 수를 센다.

**긴급청산은 이 모듈을 거치지 않는다.** `max_orders_per_minute=0` 이 매도를
막게 되고, 그것은 손절 없는 전략의 유일한 탈출구에 레이트 리미터를 거는 것이다
(Plan 1 핸드오버 1). `engine/emergency.py` 는 이 모듈을 import 하지 않으며,
그 사실을 테스트가 고정한다.

`compute_exposure` 는 `load_stages` 를 부르므로 `CorruptRowError` 가 올라올 수
있다. 여기서 잡지 않는다 — 손상된 사이클을 어떻게 처리할지는 `recovery` 와
`orchestrator` 의 정책이며, 노출 집계가 0 을 반환하며 조용히 넘어가면 한도가
사라진다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain import pnl
from autotrading7s.domain.guards import (
    GuardContext,
    GuardVerdict,
    check_buy,
    check_sell,
)
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.ports.repository import RepositoryPort


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError(f"timestamp must be tz-aware: {at!r}")


class OrderRateWindow:
    """분당 주문 수를 세는 슬라이딩 윈도우.

    경계는 `now - at < window` 다 — 정확히 60초 전의 주문은 '지난 1분'에 들지
    않는다. 포함하면 허용 건수가 실질적으로 1건 좁아진다.
    """

    def __init__(self, window_sec: int = 60) -> None:
        self._window = timedelta(seconds=window_sec)
        self._stamps: deque[datetime] = deque()

    def record(self, at: datetime) -> None:
        _require_aware(at)
        self._stamps.append(at)

    def count(self, now: datetime) -> int:
        _require_aware(now)
        cutoff = now - self._window
        while self._stamps and self._stamps[0] <= cutoff:
            self._stamps.popleft()
        return len(self._stamps)


@dataclass(frozen=True, slots=True)
class Exposure:
    """종목별·전체 보유 원가. 한도 판정의 '누적' 쪽 값이다."""
    per_stock: dict[str, int] = field(default_factory=dict)
    total: int = 0


def compute_exposure(repo: RepositoryPort) -> Exposure:
    """활성 사이클 전부의 보유 원가를 집계한다.

    `pnl.invested_amount` 를 쓰므로 매도 완료된 단계는 빠진다 — 한도가
    제한하는 것은 동시 노출이며, 누적 지출을 제한하는 것이라면 재매수가
    허용된 설정에서 한도가 영구적으로 소진된다.
    """
    per_stock: dict[str, int] = {}
    for cyc in repo.load_active_cycles():
        stages = repo.load_stages(cyc.cycle_id)
        amount = pnl.invested_amount(stages)
        if amount == 0:
            continue
        code = repo.load_config(cyc.config_id).stock_code
        per_stock[code] = per_stock.get(code, 0) + amount
    return Exposure(per_stock=per_stock, total=sum(per_stock.values()))


class GuardGate:
    """가드 판정의 단일 진입점. 상태를 갖는 이유는 분당 주문 수뿐이다."""

    def __init__(self, repo: RepositoryPort, settings: EngineSettings) -> None:
        self._repo = repo
        self._settings = settings
        self._window = OrderRateWindow()

    def record_order(self, at: datetime) -> None:
        """발주를 시도한 시점에 부른다.

        한 틱이 여러 매도를 낼 수 있으므로 결정과 결정 사이에 불러야 한다
        (Plan 1 핸드오버 2).
        """
        self._window.record(at)

    def check_buy(
        self, decision: BuyStage, *, stock_code: str, stock_limit: int,
        now: datetime,
    ) -> GuardVerdict:
        exposure = compute_exposure(self._repo)
        ctx = GuardContext(
            stock_invested=exposure.per_stock.get(stock_code, 0),
            stock_limit=stock_limit,
            total_invested=exposure.total,
            total_limit=self._settings.total_limit,
            orders_last_minute=self._window.count(now),
            max_orders_per_minute=self._settings.max_orders_per_minute,
        )
        return check_buy(decision, ctx)

    def check_sell(self, decision: SellStage, *, now: datetime) -> GuardVerdict:
        """매도는 포지션을 줄이는 방향이므로 한도와 무관하다.

        그래도 노출을 집계하는 것은 낭비이므로 0 을 넣는다 — 도메인
        `check_sell` 이 빈도만 보기 때문에 결과가 같고, DB 왕복이 사라진다.
        """
        ctx = GuardContext(
            stock_invested=0, stock_limit=self._settings.total_limit,
            total_invested=0, total_limit=self._settings.total_limit,
            orders_last_minute=self._window.count(now),
            max_orders_per_minute=self._settings.max_orders_per_minute,
        )
        return check_sell(decision, ctx)
```

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/engine/test_guards.py -q`
Expected: PASS

- [ ] **Step 5: 커버리지 대상에 새 패키지를 추가한다**

`pyproject.toml` 의 `[tool.coverage.run]` `source` 에 `"autotrading7s.engine"` 과 `"autotrading7s.app"` 을 추가한다. 추가하지 않으면 새 코드가 커버리지 집계에서 빠져 `fail_under = 95` 가 아무것도 지키지 않는다.

```toml
[tool.coverage.run]
source = [
  "autotrading7s.domain",
  "autotrading7s.adapters",
  "autotrading7s.ports",
  "autotrading7s.engine",
  "autotrading7s.app",
]
```

- [ ] **Step 6: 전체 회귀와 커버리지를 확인한다**

Run: `.venv/bin/python -m pytest -q --cov --cov-report=term-missing`
Expected: PASS, 커버리지 95% 이상.

- [ ] **Step 7: 커밋**

```bash
git add src/autotrading7s/engine tests/engine pyproject.toml
git commit -m "$(printf 'feat: 안전장치 조립기 — 노출 집계와 분당 주문 카운터\n\n설계서 6절·9절 ②. domain/guards 의 판정은 상태 없는 술어이므로 컨텍스트를\n채우는 책임이 정해져 있지 않았다(Plan 1 핸드오버 1·2). 그 책임을 GuardGate 로\n모았다.\n\nrecord_order 를 결정 사이에 부르지 않으면 분당 3건 제한에서 한 틱에 7건이\n나가고, 그 7건은 실제로 브로커에 도달해 호출 제한에 걸려 일부가 조용히\n실패한다. 테스트가 이 경로를 고정한다.\n\n노출은 보유 원가 기준이다 — 누적 지출로 세면 재매수가 허용된 설정에서 한도가\n영구 소진된다. 매도 완료 단계가 빠지는 것을 테스트로 고정했다.\n\n장중 판정을 여기 두지 않았다. 규칙 4 는 rules.decide(market_open=) 이 이미\n강제하며, 같은 판정이 두 곳에 있으면 어긋난다.')"
```

---

## Task 3: 시뮬레이션 브로커의 거부 모드

**배경 (2A 핸드오버 4).** `FakeBroker` 는 예수금 검사도 보유 검사도 하지 않는다. `_cash` 가 조용히 음수가 되고, 보유 0 인 종목의 매도가 현금을 늘린다. **그러므로 이 더블로 투입한도와 긴급청산을 검증하면 아무것도 검증하지 않는 것이다** — 한도를 넘겨 매수하거나 없는 포지션을 매도하는 엔진 버그가 모든 테스트를 통과한다. Task 4 이후가 이 더블 위에 서므로 여기서 먼저 고친다.

**Ruling (계획 작성 시 확정): 검증은 기본 꺼짐(`validate_account=False`).** 2A 의 기존 테스트 다수가 예수금을 초과하는 매수를 전제하고 있고, 기본값을 켜면 그것들이 이 태스크의 진짜 변화와 섞여 실패한다. 새 시나리오가 명시적으로 켠다. 틀렸을 경우 비용: 켜는 것을 잊은 시나리오가 여전히 아무것도 검증하지 못한다 — 그래서 **G2 게이트(Task 12)가 `validate_account=True` 를 쓰는지 게이트 자신이 단정한다.**

**Ruling: `_should_fail` 을 검증보다 먼저 본다.** `FailMode` 는 전송 계층(응답 유실·네트워크)을 모델링하고 계좌 검증은 거래소 계층이다. 순서를 뒤집으면 2A 가 정교하게 고친 `fail_after` 의 의미("실패할 수 있었던 호출 N번")가 깨진다. 틀렸을 경우 비용: "거부될 주문에 타임아웃이 겹친 경우" 를 시뮬레이션할 수 없다 — 필요한 시나리오가 아니다.

**Files:**
- Modify: `src/autotrading7s/adapters/fake/broker.py`
- Test: `tests/adapters/test_fake_broker_validate.py`

**Interfaces:**
- Produces: `FakeBroker(..., validate_account: bool = False, holdings: dict[str, tuple[int, int]] | None = None)`. 거부는 기존 `BrokerRejected(code, message)` 로 낸다 — 새 예외 타입을 만들지 않는다. 코드는 `"40940"`(예수금 부족), `"40950"`(보유수량 부족).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/adapters/test_fake_broker_validate.py`:

```python
from __future__ import annotations

import uuid

import pytest

from autotrading7s.adapters.fake.broker import (
    BrokerRejected,
    BrokerTimeout,
    FailMode,
    FakeBroker,
    FillMode,
)
from autotrading7s.domain.types import LimitOrderRequest, MarketSellRequest, Side


def _buy(qty: int, price: int) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.BUY, qty=qty, price=price,
                             client_ref=uuid.uuid4())


def _sell(qty: int, price: int) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.SELL, qty=qty, price=price,
                             client_ref=uuid.uuid4())


@pytest.mark.asyncio
async def test_validation_is_off_by_default_and_cash_still_goes_negative():
    """2A 의 동작을 그대로 고정한다 — 기본값을 바꾸지 않았음을 증명한다."""
    broker = FakeBroker([10_000], cash=1_000)
    await broker.place_limit_order(_buy(qty=100, price=10_000))
    balance = await broker.get_balance()
    assert balance.cash < 0


@pytest.mark.asyncio
async def test_rejects_buy_beyond_cash_when_validating():
    broker = FakeBroker([10_000], cash=999_999, validate_account=True)
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert exc.value.code == "40940"
    assert "예수금" in exc.value.message


@pytest.mark.asyncio
async def test_rejected_order_is_not_registered():
    """미접수가 확실해야 한다 — 설계서 9절 ⑤의 '명시적 거부' 분기.

    등록해두면 재시작 복구가 당일 주문 조회에서 그것을 찾아내고, 실제로는
    없는 주문을 근거로 단계 상태를 정정한다.
    """
    broker = FakeBroker([10_000], cash=1, validate_account=True)
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert await broker.list_orders_today("005930") == []


@pytest.mark.asyncio
async def test_allows_buy_exactly_at_cash():
    broker = FakeBroker([10_000], cash=1_000_000, validate_account=True)
    ack = await broker.place_limit_order(_buy(qty=100, price=10_000))
    assert ack.broker_order_id
    assert (await broker.get_balance()).cash == 0


@pytest.mark.asyncio
async def test_rejects_sell_beyond_position_when_validating():
    broker = FakeBroker([10_000], validate_account=True)
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_limit_order(_sell(qty=1, price=10_000))
    assert exc.value.code == "40950"
    assert "보유수량" in exc.value.message


@pytest.mark.asyncio
async def test_sell_of_exactly_the_position_succeeds():
    broker = FakeBroker([10_000], validate_account=True)
    await broker.place_limit_order(_buy(qty=100, price=10_000))
    ack = await broker.place_limit_order(_sell(qty=100, price=10_000))
    assert ack.broker_order_id
    assert (await broker.get_balance()).qty_of("005930") == 0


@pytest.mark.asyncio
async def test_market_sell_is_validated_too():
    """긴급청산 경로도 없는 포지션을 팔 수 없다.

    이것이 검증되지 않으면 설계서 11.1절 ③(실계좌 수량으로 팔기)이 지켜지는지
    를 이 더블로 확인할 수 없다.
    """
    broker = FakeBroker([10_000], validate_account=True)
    req = MarketSellRequest(code="005930", qty=40, client_ref=uuid.uuid4(),
                            reason="긴급청산")
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_market_sell(req)
    assert exc.value.code == "40950"


@pytest.mark.asyncio
async def test_preexisting_holdings_can_be_sold():
    """엔진이 모르는 포지션 — 대사 불일치와 긴급청산 시나리오의 출발점."""
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (40, 400_000)})
    assert (await broker.get_balance()).qty_of("005930") == 40
    req = MarketSellRequest(code="005930", qty=40, client_ref=uuid.uuid4(),
                            reason="긴급청산")
    ack = await broker.place_market_sell(req)
    assert ack.broker_order_id
    assert (await broker.get_balance()).qty_of("005930") == 0


@pytest.mark.asyncio
async def test_transport_failure_wins_over_validation():
    """FailMode 는 거래소보다 앞단이다 — 타임아웃은 등록한 뒤 던진다.

    순서가 뒤집히면 fail_after 의 의미("실패할 수 있었던 호출 N번")가 깨진다.
    """
    broker = FakeBroker([10_000], cash=1, validate_account=True,
                        fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(_buy(qty=100, price=10_000))
    # 타임아웃은 등록한다 — 설계서 9절 ⑤의 "접수됨" 분기를 만들기 위해
    assert len(await broker.list_orders_today("005930")) == 1


@pytest.mark.asyncio
async def test_validation_does_not_change_partial_fill_behaviour():
    """PARTIAL 모드에서 검증 기준은 요청 수량이다, 체결 수량이 아니다.

    체결 수량으로 검증하면 부분체결로 조금씩 팔아 없는 포지션을 비울 수 있다.
    """
    broker = FakeBroker([10_000], validate_account=True,
                        fill_mode=FillMode.PARTIAL,
                        holdings={"005930": (10, 100_000)})
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(_sell(qty=100, price=10_000))
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/test_fake_broker_validate.py -q`
Expected: FAIL — `TypeError: FakeBroker.__init__() got an unexpected keyword argument 'validate_account'`

- [ ] **Step 3: 구현한다**

`FakeBroker.__init__` 에 두 인자를 추가한다.

```python
        validate_account: bool = False,
        holdings: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        ...
        self._validate_account = validate_account
        self._positions: dict[str, tuple[int, int]] = dict(holdings or {})
```

`_accept` 에서 `_should_fail` 블록 **다음에** 검증을 넣는다.

```python
        # DISCONNECT 는 시세 스트림 전용이다 — 주문 경로를 막지 않는다. 설계서
        # 8.4절: WS 가 끊겨도 REST 폴링으로 전환해 트리거 판정과 발주는 계속된다.
        if self._validate_account:
            self._validate(code, side, qty, price)
        return OrderAck(...)
```

```python
    def _validate(
        self, code: str, side: Side, qty: int, price: int | None
    ) -> None:
        """거래소 계층의 거부. `validate_account=True` 일 때만 동작한다.

        `FailMode` 뒤에 오는 이유: FailMode 는 전송 계층(응답 유실)을
        모델링하고 이것은 거래소 계층이다. 순서를 뒤집으면 `fail_after` 의
        의미가 깨진다.

        매도 검증은 **요청 수량** 기준이다. 체결 수량으로 검증하면 부분체결로
        조금씩 팔아 없는 포지션을 비울 수 있다.
        """
        if side is Side.BUY:
            # 매수는 지정가만 존재한다 — 자동 트리거 경로에 시장가가 없다.
            assert price is not None
            need = price * qty
            if need > self._cash:
                raise BrokerRejected(
                    "40940",
                    f"예수금 부족: 필요 {need:,} > 보유 {self._cash:,}",
                )
            return
        held_qty, _ = self._positions.get(code, (0, 0))
        if qty > held_qty:
            raise BrokerRejected(
                "40950",
                f"보유수량 부족: 요청 {qty:,} > 보유 {held_qty:,}",
            )
```

**클래스 독스트링을 고친다.** 지금 독스트링은 "브로커 쪽 검증을 모델링하지 않는다" 로 시작하며 Fix Round 3 에서 의도적으로 미룬다고 적혀 있다. 이제 사실이 아니므로 다음으로 대체한다.

```python
    """`BrokerPort` 의 시뮬레이션 구현.

    두 계층의 실패를 따로 흉내낸다. `FailMode` 는 **전송 계층**(응답 유실,
    명시적 거부, 스트림 끊김)이고, `validate_account=True` 는 **거래소
    계층**(예수금 부족, 보유수량 부족)이다. 전송이 먼저 판정된다 — 실제 순서가
    그렇고, `fail_after` 가 "실패할 수 있었던 호출 N번"이라는 의미를 유지한다.

    **`validate_account` 는 기본 꺼짐이다.** 켜지 않으면 `_cash` 가 조용히
    음수가 되고 보유 0 인 종목의 매도가 현금을 늘린다. 총투입 한도 캡이 이
    프로그램의 유일한 구조적 보호장치이므로(설계서 6절), **한도나 긴급청산을
    검증하는 테스트는 반드시 켜야 한다** — 끄고 돌리면 한도를 넘겨 매수하거나
    없는 포지션을 매도하는 엔진 버그가 전부 통과한다.
    """
```

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters -q`
Expected: PASS — 새 테스트와 기존 어댑터 테스트 전부. 기존 테스트가 깨지면 기본값이 켜져 있다는 뜻이다.

- [ ] **Step 5: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/adapters/fake/broker.py tests/adapters/test_fake_broker_validate.py
git commit -m "$(printf 'feat: 시뮬레이션 브로커에 계좌 검증 거부 모드\n\n2A 핸드오버 4. 이 더블은 예수금도 보유도 검사하지 않아서, 한도를 넘겨\n매수하거나 없는 포지션을 매도하는 엔진 버그가 모든 테스트를 통과했다.\n총투입 한도가 손절 없는 전략의 유일한 구조적 보호장치이므로 그 검증이\n무의미했다는 뜻이다.\n\n두 계층을 분리했다. FailMode 는 전송 계층(응답 유실)이고 validate_account 는\n거래소 계층(예수금·보유)이다. 전송이 먼저 판정되어 fail_after 의 의미가\n유지된다.\n\n기본값은 꺼짐이다 — 기존 시나리오 다수가 예수금 초과 매수를 전제하므로, 켜면\n이번 변화와 섞여 실패한다. G2 게이트가 켜져 있는지를 게이트 자신이 단정한다.\n\n매도 검증은 요청 수량 기준이다. 체결 수량으로 하면 부분체결로 조금씩 팔아\n없는 포지션을 비울 수 있다.')"
```

---

## Task 4: 주문 발주 파이프라인 (설계서 9절 ③④⑤)

**이 태스크가 이 계획의 심장이다.** ⑤의 UNKNOWN 분기가 중복 주문을 막는 유일한 장치이고, ③④의 순서가 고아 주문을 막는 유일한 장치다.

**Ruling (계획 작성 시 확정): 브로커 예외를 포트로 올린다.** 지금 `BrokerTimeout`·`BrokerRejected`·`BrokerDisconnected` 는 `adapters/fake/broker.py` 에 있다. 엔진은 `adapters/` 를 import 할 수 없으므로(전역 제약) 그대로 두면 엔진이 UNKNOWN 분기를 **타입으로 구분할 수 없다** — 결국 `except Exception` 을 쓰게 되고, 그것은 DB 손상과 프로그래밍 오류까지 "응답 유실" 로 취급한다는 뜻이다. 예외는 포트 계약의 일부다: 세 예외를 `ports/broker.py` 로 옮기고 `BrokerError` 공통 상위를 둔다. `adapters/fake/broker.py` 는 그것을 import 해서 재수출하므로 기존 테스트의 import 경로가 그대로 산다. Plan 3 의 키움 어댑터도 같은 예외를 던져야 한다는 것이 이 결정의 이득이다. 틀렸을 경우 비용: 없음 — 포트는 이미 `domain.types` 를 참조하므로 예외 선언이 의존 방향을 바꾸지 않는다.

**Ruling: 미접수로 확인된 UNKNOWN 주문의 종결 상태는 `CANCELED` 다.** `REJECTED` 는 브로커의 명시적 거부(그리고 `api_code`)를 위해 남긴다. 두 경로는 사용자에게 다르게 보여야 한다 — 하나는 브로커가 판단한 것이고 하나는 도달하지 않은 것이다. `CANCELED` 는 "체결 없이 사라진 주문" 이라는 뜻이고 `realized_pnl_for_cycle` 은 체결 데이터로 집계하므로 어느 쪽이든 손익에 영향이 없다. 틀렸을 경우 비용: 이력 화면의 분류 하나.

**Files:**
- Modify: `src/autotrading7s/ports/broker.py` (예외 3종 + `BrokerError`), `src/autotrading7s/adapters/fake/broker.py` (재수출), `src/autotrading7s/ports/repository.py` (`stage_row_id`), `src/autotrading7s/adapters/sqlite/repository.py` (`stage_row_id`)
- Create: `src/autotrading7s/engine/executor.py`
- Test: `tests/engine/test_executor_send.py`, `tests/ports/test_broker_errors.py`, `tests/adapters/test_repository_stage_row_id.py`

**Interfaces:**
- Produces:
  - `ports.broker`: `BrokerError`, `BrokerTimeout(BrokerError)`, `BrokerRejected(BrokerError)` (`.code`, `.message`), `BrokerDisconnected(BrokerError)`
  - `RepositoryPort.stage_row_id(cycle_id: int, stage_no: int) -> int` — 없으면 `RowNotFound`
  - `engine.executor.SendOutcome` — `status: str`, `client_ref: str`, `broker_order_id: str | None`, `stage: StageState`
  - `engine.executor.SEND_STATUSES = frozenset({"ACCEPTED", "REJECTED", "UNKNOWN_ACCEPTED", "UNKNOWN_NOT_SENT"})`
  - `engine.executor.Executor(*, repo, broker, clock, emit)` — `.send(cycle, config, stage, decision, tick) -> SendOutcome` (async)
- Consumes: Task 1 의 `events`, Task 2 는 쓰지 않는다 (가드 판정은 호출자가 이미 통과시킨 뒤에 부른다)

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 포트 예외 계약**

`tests/ports/test_broker_errors.py`:

```python
from __future__ import annotations

import asyncio

from autotrading7s.adapters.fake import broker as fake_broker
from autotrading7s.ports.broker import (
    BrokerDisconnected,
    BrokerError,
    BrokerRejected,
    BrokerTimeout,
)


def test_three_transport_errors_share_one_base():
    """엔진이 `except BrokerError` 하나로 전송 실패를 잡을 수 있어야 한다.

    공통 상위가 없으면 엔진이 `except Exception` 을 쓰게 되고, 그것은 DB 손상
    (CorruptRowError)과 프로그래밍 오류까지 '응답 유실' 로 취급하는 것이다.
    """
    for cls in (BrokerTimeout, BrokerRejected, BrokerDisconnected):
        assert issubclass(cls, BrokerError), cls
    assert issubclass(BrokerError, Exception)


def test_broker_timeout_does_not_inherit_builtin_timeout():
    """2A 의 결정을 그대로 유지한다.

    `asyncio.TimeoutError is TimeoutError` 이므로, 상속하면 엔진의
    `except BrokerTimeout` 이 asyncio 자체의 취소·대기 타임아웃까지 삼킨다 —
    그러면 "브로커 응답 유실" 이 아닌 것을 UNKNOWN 으로 기록하고 재발주 금지
    상태에 들어간다.
    """
    assert not issubclass(BrokerTimeout, TimeoutError)
    assert BrokerTimeout is not asyncio.TimeoutError


def test_broker_rejected_carries_api_code_and_message():
    exc = BrokerRejected("40510", "거래정지")
    assert exc.code == "40510"
    assert exc.message == "거래정지"
    assert "40510" in str(exc)


def test_fake_adapter_reexports_the_port_exceptions():
    """기존 테스트의 import 경로가 살아 있어야 하고, 같은 타입이어야 한다.

    같은 이름의 별개 클래스가 두 곳에 생기면 엔진의 except 절이 어댑터가 던진
    예외를 놓친다 — 조용히 UNKNOWN 분기가 죽는다.
    """
    assert fake_broker.BrokerTimeout is BrokerTimeout
    assert fake_broker.BrokerRejected is BrokerRejected
    assert fake_broker.BrokerDisconnected is BrokerDisconnected
```

- [ ] **Step 2: 실패하는 테스트를 쓴다 — `stage_row_id`**

`tests/adapters/test_repository_stage_row_id.py`:

```python
from __future__ import annotations

import pytest

from autotrading7s.ports.repository import RepositoryPort, RowNotFound


def test_stage_row_id_returns_the_row_id(repo_two_stocks):
    """order_log.stage_state_id 를 채우려면 이 id 가 필요하다.

    없으면 재시작 복구가 미체결 주문을 어느 단계의 것인지 알 수 없고, 설계서
    10.1절 2단계('체결됨 → HOLDING 으로 정정')를 수행할 방법이 사라진다.
    """
    cycles = repo_two_stocks.load_active_cycles()
    cyc = cycles[0]
    ids = {n: repo_two_stocks.stage_row_id(cyc.cycle_id, n) for n in range(1, 8)}
    assert len(set(ids.values())) == 7          # 7개가 서로 다른 행
    assert all(isinstance(v, int) for v in ids.values())


def test_stage_row_id_raises_for_a_missing_stage(repo_two_stocks):
    cyc = repo_two_stocks.load_active_cycles()[0]
    with pytest.raises(RowNotFound, match="stage_state"):
        repo_two_stocks.stage_row_id(cyc.cycle_id, 99)


def test_stage_row_id_is_scoped_to_the_cycle(repo_two_stocks):
    """다른 사이클의 같은 단계 번호를 반환하면 주문이 엉뚱한 단계에 붙는다."""
    a, b = repo_two_stocks.load_active_cycles()[:2]
    assert (repo_two_stocks.stage_row_id(a.cycle_id, 1)
            != repo_two_stocks.stage_row_id(b.cycle_id, 1))


def test_port_declares_stage_row_id():
    assert "stage_row_id" in RepositoryPort.__protocol_attrs__
```

`tests/adapters/` 에서 `repo_two_stocks` 를 쓰려면 픽스처가 보여야 한다. **Task 2 가 만든 `tests/engine/conftest.py` 의 픽스처를 `tests/conftest.py` 로 올린다** — 두 디렉터리가 같은 시드를 쓰므로 복제하면 어긋난다.

- [ ] **Step 3: 실패하는 테스트를 쓴다 — 발주 파이프라인**

`tests/engine/test_executor_send.py`:

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FailMode, FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import Event, OrderRejected, OrderUnknown
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.domain.types import StageStatus, Tick, TickSource
from autotrading7s.engine.executor import Executor

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _tick(price: int) -> Tick:
    return Tick(code="005930", price=price, at=AT, source=TickSource.WS)


def _executor(repo, broker):
    events: list[Event] = []
    ex = Executor(repo=repo, broker=broker, clock=FakeClock(current=AT),
                  emit=events.append)
    return ex, events


def _waiting_stage(repo, cycle_id, stage_no=2):
    return next(s for s in repo.load_stages(cycle_id) if s.stage_no == stage_no)


@pytest.mark.asyncio
async def test_records_the_order_before_placing_it(repo_two_stocks):
    """설계서 9절 ③④ — 발주보다 먼저 기록하고 커밋한다.

    'SENDING 행이 존재한 시점' 을 직접 관측하려면 브로커를 발주 시점에
    멈춰야 한다. place_limit_order 를 감싼 스파이가 그때 DB 를 읽는다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], validate_account=True, cash=100_000_000)
    seen: dict[str, object] = {}
    original = broker.place_limit_order

    async def spy(req):
        rows = repo_two_stocks.load_pending_orders()
        seen["pending_at_place_time"] = [(r.status, r.req_qty) for r in rows]
        stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)
        seen["stage_status_at_place_time"] = stage.status
        return await original(req)

    broker.place_limit_order = spy            # type: ignore[method-assign]
    ex, _ = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)
    decision = BuyStage(stage_no=2, limit_price=9_500, qty=52, reason="테스트")

    await ex.send(cycle=cyc, config=config, stage=stage,
                  decision=decision, tick=_tick(9_500))

    assert seen["pending_at_place_time"] == [("SENDING", 52)]
    assert seen["stage_status_at_place_time"] is StageStatus.BUY_PENDING


@pytest.mark.asyncio
async def test_accepted_order_records_broker_id_and_leaves_stage_pending(repo_two_stocks):
    """⑤ 성공 — 체결 반영은 Task 5 의 몫이므로 단계는 BUY_PENDING 에 머문다."""
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "ACCEPTED"
    assert outcome.broker_order_id == "FAKE-1"
    assert outcome.stage.status is StageStatus.BUY_PENDING
    rows = repo_two_stocks.load_pending_orders()
    assert [(r.status, r.broker_order_id) for r in rows] == [("ACCEPTED", "FAKE-1")]
    assert events == []


@pytest.mark.asyncio
async def test_explicit_rejection_restores_the_stage_to_waiting(repo_two_stocks):
    """⑤ 명시적 거부 — 단계를 WAITING 으로 복구하고 이벤트를 낸다."""
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fail_mode=FailMode.REJECT)
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "REJECTED"
    assert outcome.stage.status is StageStatus.WAITING
    reloaded = _waiting_stage(repo_two_stocks, cyc.cycle_id)
    assert reloaded.status is StageStatus.WAITING
    assert repo_two_stocks.load_pending_orders() == []      # 종결됐으므로 빠진다
    assert [type(e) for e in events] == [OrderRejected]
    assert events[0].api_code == "40510"


@pytest.mark.asyncio
async def test_timeout_confirms_acceptance_by_query_and_does_not_resend(repo_two_stocks):
    """⑤ UNKNOWN — 접수됨. **이 시스템에서 가장 중요한 분기다.**

    FakeBroker 의 TIMEOUT 은 주문을 등록한 뒤 던진다. 재발주하면 같은 단계를
    두 번 사게 되므로, 유일하게 안전한 행동은 조회로 사실을 확인하는 것이다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        fail_mode=FailMode.TIMEOUT)
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "UNKNOWN_ACCEPTED"
    assert outcome.stage.status is StageStatus.BUY_PENDING
    # 주문은 정확히 하나여야 한다 — 재발주가 없었다는 직접 증거
    assert len(await broker.list_orders_today("005930")) == 1
    rows = repo_two_stocks.load_pending_orders()
    assert [r.status for r in rows] == ["ACCEPTED"]
    assert [type(e) for e in events] == [OrderUnknown]


@pytest.mark.asyncio
async def test_timeout_with_no_trace_restores_the_stage(repo_two_stocks):
    """⑤ UNKNOWN — 미접수. 조회에 흔적이 없으면 WAITING 으로 복구한다.

    등록 없이 타임아웃을 던지는 브로커를 만들어야 한다. FakeBroker 의 TIMEOUT
    은 등록하므로, place_limit_order 를 등록 없이 던지는 스텁으로 교체한다.
    """
    from autotrading7s.ports.broker import BrokerTimeout

    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)

    async def lost(req):
        raise BrokerTimeout("no response (never reached the broker)")

    broker.place_limit_order = lost           # type: ignore[method-assign]
    ex, events = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=2, limit_price=9_500,
                                              qty=52, reason="r"),
                            tick=_tick(9_500))

    assert outcome.status == "UNKNOWN_NOT_SENT"
    assert outcome.stage.status is StageStatus.WAITING
    assert _waiting_stage(repo_two_stocks, cyc.cycle_id).status is StageStatus.WAITING
    # 미접수는 CANCELED 로 종결한다 — REJECTED 는 브로커의 명시적 판단용이다
    assert repo_two_stocks.load_pending_orders() == []
    assert [type(e) for e in events] == [OrderUnknown]


@pytest.mark.asyncio
async def test_sell_send_restores_holding_with_the_same_qty_on_rejection(repo_two_stocks):
    """매도 발주 실패는 보유 수량을 건드리지 않아야 한다.

    cancel_sell 은 remaining_qty 를 요구한다. 발주 자체가 실패했으면 체결이
    0 이므로 원래 fill_qty 를 그대로 넘겨야 하며, 잘못 넘기면 보유가 조용히
    줄어든다 — 그 줄어든 수량이 이후 모든 목표가 계산의 근거가 된다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    holding = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.status is StageStatus.HOLDING)
    broker = FakeBroker([10_500], fail_mode=FailMode.REJECT)
    ex, events = _executor(repo_two_stocks, broker)

    outcome = await ex.send(
        cycle=cyc, config=config, stage=holding,
        decision=SellStage(stage_no=holding.stage_no, limit_price=10_500,
                           qty=holding.fill_qty, reason="r"),
        tick=_tick(10_500),
    )

    assert outcome.status == "REJECTED"
    assert outcome.stage.status is StageStatus.HOLDING
    assert outcome.stage.fill_qty == holding.fill_qty
    assert outcome.stage.fill_price == holding.fill_price
    assert [type(e) for e in events] == [OrderRejected]


@pytest.mark.asyncio
async def test_order_log_links_to_the_stage_row(repo_two_stocks):
    """재시작 복구가 주문을 단계로 되돌릴 수 있어야 한다 (설계서 10.1절 2)."""
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, _ = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    await ex.send(cycle=cyc, config=config, stage=stage,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=_tick(9_500))

    row = repo_two_stocks.load_pending_orders()[0]
    assert row.stage_state_id == repo_two_stocks.stage_row_id(cyc.cycle_id, 2)


@pytest.mark.asyncio
async def test_trigger_path_records_the_tick_that_caused_it(repo_two_stocks):
    """설계서 12.1절 order_log 의 tick_price·tick_source·trigger_reason.

    사후에 "왜 이 주문이 나갔는가" 를 답할 수 있어야 한다. 트리거 이유는
    도메인이 만든 문자열을 그대로 저장한다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, _ = _executor(repo_two_stocks, broker)
    stage = _waiting_stage(repo_two_stocks, cyc.cycle_id)

    await ex.send(cycle=cyc, config=config, stage=stage,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="2단계 발동가 9,500 도달"),
                  tick=Tick(code="005930", price=9_480, at=AT,
                            source=TickSource.REST_POLL))

    row = repo_two_stocks._conn.execute(
        "SELECT trigger_reason, tick_price, tick_source, path, order_type "
        "FROM order_log"
    ).fetchone()
    assert dict(row) == {
        "trigger_reason": "2단계 발동가 9,500 도달",
        "tick_price": 9_480,
        "tick_source": "REST_POLL",
        "path": "TRIGGER",
        "order_type": "LIMIT",
    }


@pytest.mark.asyncio
async def test_executor_never_places_a_market_order(repo_two_stocks):
    """자동 트리거 경로는 시장가를 표현할 수 없다 (설계서 8.2절).

    executor 모듈이 MarketSellRequest 를 참조하지 않는 것으로 확인한다 —
    참조가 없으면 그 경로가 존재할 수 없다.
    """
    import inspect

    from autotrading7s.engine import executor as mod

    source = inspect.getsource(mod)
    assert "MarketSellRequest" not in source
    assert "place_market_sell" not in source
```

- [ ] **Step 4: 세 테스트 파일이 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/ports/test_broker_errors.py tests/adapters/test_repository_stage_row_id.py tests/engine/test_executor_send.py -q`
Expected: FAIL — `ImportError` (포트 예외 없음), `AttributeError: stage_row_id`, `ModuleNotFoundError: engine.executor`

- [ ] **Step 5: 포트에 예외를 옮긴다**

`src/autotrading7s/ports/broker.py` 에 추가한다 (Protocol 선언 **앞에** 둔다).

```python
class BrokerError(Exception):
    """브로커 전송 계층의 실패 — 어댑터가 던지고 엔진이 분기한다.

    예외를 포트에 두는 이유: 엔진은 `adapters/` 를 import 할 수 없으므로
    (설계서 7.2절 의존 규칙), 예외가 어댑터에만 있으면 엔진은 UNKNOWN 분기를
    타입으로 구분할 수 없고 결국 `except Exception` 을 쓰게 된다. 그것은 DB
    손상(`CorruptRowError`)과 프로그래밍 오류까지 '응답 유실' 로 취급한다는
    뜻이다. 어떤 실패를 어떤 이름으로 던지는지는 포트 계약의 일부다.
    """


class BrokerTimeout(BrokerError):
    """응답이 오지 않았다 — 접수 여부를 알 수 없다.

    `TimeoutError` 를 상속하지 **않는다.** `asyncio.TimeoutError is
    TimeoutError` 이므로 상속하면 엔진의 `except BrokerTimeout` 이 asyncio
    자체의 대기 타임아웃까지 삼키고, 브로커와 무관한 일을 UNKNOWN 으로 기록해
    재발주 금지 상태에 들어간다.
    """


class BrokerRejected(BrokerError):
    """브로커가 명시적으로 거부했다 — 미접수가 확실하다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class BrokerDisconnected(BrokerError):
    """실시간 시세 스트림이 끊겼다. 주문 경로는 막히지 않는다 (설계서 8.4절)."""
```

`src/autotrading7s/adapters/fake/broker.py` 에서 세 예외 정의를 지우고 재수출한다.

```python
from autotrading7s.ports.broker import (       # noqa: F401 — 재수출
    BrokerDisconnected,
    BrokerError,
    BrokerRejected,
    BrokerTimeout,
)
```

**같은 이유로 `CorruptRowError` 도 `ports/repository.py` 로 옮긴다.** 지금 그것은 `adapters/sqlite/mapping.py` 에 있고, `engine/` 은 `adapters/` 를 import 할 수 없으므로 복원 실패를 타입으로 구분할 수 없다 — 그러면 엔진이 `except ValueError` 를 쓰게 되고, 그것이 2A 핸드오버 7 이 명시적으로 경고한 실패("넓은 `except ValueError` 를 두면 DB 손상을 삼킨다")다. `mapping.py` 는 그것을 `ports/repository.py` 에서 import 해 재수출하므로 2A 의 테스트가 쓰는 import 경로가 그대로 산다. 계층 관계도 그대로다 — `CorruptRowError` 는 `DomainInvariantError` 의 하위여야 하고, `ports` 는 이미 `domain` 을 참조한다.

```python
# ports/repository.py
from autotrading7s.domain.errors import DomainInvariantError


class CorruptRowError(DomainInvariantError):
    """저장된 행에서 도메인 객체를 복원할 수 없다 — 테이블과 rowid 를 지목한다.

    호출자 버그(`TypeError`)와 구분되며, 엔진이 이것을 잡아 그 사이클만
    격리한다. 예외를 포트에 두는 이유는 브로커 예외와 같다: `engine/` 은
    `adapters/` 를 알지 못하므로, 예외가 어댑터에만 있으면 엔진은 넓은
    `except ValueError` 를 쓰게 되고 그것은 DB 손상을 삼킨다.
    """
```

`tests/ports/test_broker_errors.py` 에 다음을 추가한다.

```python
def test_corrupt_row_error_lives_in_the_port_and_is_reexported():
    from autotrading7s.adapters.sqlite import mapping
    from autotrading7s.domain.errors import DomainInvariantError
    from autotrading7s.ports.repository import CorruptRowError

    assert mapping.CorruptRowError is CorruptRowError
    assert issubclass(CorruptRowError, DomainInvariantError)
    assert issubclass(CorruptRowError, ValueError)
```

- [ ] **Step 6: `stage_row_id` 를 추가한다**

`RepositoryPort` 에 (단계 메서드 그룹 안에):

```python
    def stage_row_id(self, cycle_id: int, stage_no: int) -> int:
        """`stage_state` 행의 id. 없으면 `RowNotFound`.

        `order_log.stage_state_id` 를 채우기 위해 필요하다. 이 연결이 없으면
        재시작 복구가 미체결 주문이 어느 단계의 것인지 알 수 없고, 설계서
        10.1절 2단계('체결됨 → HOLDING 으로 정정')를 수행할 방법이 없다.
        """
        ...
```

`SqliteRepository` 에:

```python
    def stage_row_id(self, cycle_id: int, stage_no: int) -> int:
        row = self._conn.execute(
            "SELECT id FROM stage_state WHERE cycle_id = ? AND stage_no = ?",
            (cycle_id, stage_no),
        ).fetchone()
        if row is None:
            raise RowNotFound(
                f"no stage_state row for cycle_id={cycle_id} stage_no={stage_no}"
            )
        return int(dict(row)["id"])
```

- [ ] **Step 7: `engine/executor.py` 를 구현한다**

```python
"""주문 실행 파이프라인 — 설계서 9절.

이 모듈은 주문 **한 건의 생애**를 담당한다. 어느 단계를 살지 팔지는
`rules.decide()` 가 정하고, 가드는 호출자가 이미 통과시킨 뒤에 여기로 온다.

순서가 이 모듈의 전부다:

    ③ order_log INSERT (SENDING)
    ④ stage_state UPDATE → PENDING          ← 여기서 커밋
    ⑤ broker.place_limit_order()

**발주보다 먼저 기록하고 커밋한다.** 발주 후에 기록하면 그 사이에 프로세스가
죽었을 때 '브로커에는 주문이 있는데 우리는 모르는' 고아 주문이 생기고 다음
실행에서 중복 발주로 이어진다. 반대 순서의 최악은 '우리는 보냈다고 기록했는데
실제로는 없는' 상태인데, 이건 조회로 안전하게 정정할 수 있다. 설계서 9절:
**잘못 기록된 쪽이 잘못 잊힌 쪽보다 항상 낫다.**

**⑤의 UNKNOWN 분기가 이 시스템에서 가장 중요한 부분이다.** 응답이 오지 않았다면
서버에 도달하지 못했거나 도달했지만 응답만 유실됐다. 여기서 재발주하면 같은
단계를 두 번 산다. 유일하게 안전한 행동은 `list_orders_today` 로 사실을 확인하는
것이다 (D12).

두 홉(④와 체결 반영)을 합성해 한 번만 저장하는 것은 `save_stage` 가드가
거부한다. 그것은 버그가 아니라 이 순서의 강제다 (2A 핸드오버 9).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from autotrading7s.app.events import Event, OrderRejected, OrderUnknown
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import (
    LimitOrderRequest,
    OrderPath,
    Side,
    StageStatus,
    Tick,
)
from autotrading7s.ports.broker import (
    BrokerPort,
    BrokerRejected,
    BrokerTimeout,
)
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import RepositoryPort, SplitConfig

SEND_STATUSES = frozenset(
    {"ACCEPTED", "REJECTED", "UNKNOWN_ACCEPTED", "UNKNOWN_NOT_SENT"}
)


@dataclass(frozen=True, slots=True)
class SendOutcome:
    status: str
    client_ref: str
    broker_order_id: str | None
    stage: StageState

    def __post_init__(self) -> None:
        if self.status not in SEND_STATUSES:
            raise ValueError(f"unknown send status: {self.status!r}")


class Executor:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: "object",
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    async def send(
        self, *, cycle: Cycle, config: SplitConfig, stage: StageState,
        decision: BuyStage | SellStage, tick: Tick,
    ) -> SendOutcome:
        is_buy = isinstance(decision, BuyStage)
        side = Side.BUY if is_buy else Side.SELL
        client_ref = uuid.uuid4()
        now = self._clock.now()

        # ③ 기록 먼저
        self._repo.append_order_log(
            client_ref=str(client_ref), cycle_id=cycle.cycle_id,
            stage_state_id=self._repo.stage_row_id(cycle.cycle_id,
                                                   stage.stage_no),
            side=side, order_type="LIMIT", path=OrderPath.TRIGGER,
            req_price=decision.limit_price, req_qty=decision.qty,
            trigger_reason=decision.reason, tick_price=tick.price,
            tick_source=tick.source.value, sent_at=now,
        )

        # ④ 단계를 PENDING 으로 — 여기서 커밋된다
        pending = (stage_mod.to_buy_pending(stage) if is_buy
                   else stage_mod.to_sell_pending(stage))
        self._repo.save_stage(cycle.cycle_id, pending)

        # ⑤ 발주
        req = LimitOrderRequest(
            code=config.stock_code, side=side, qty=decision.qty,
            price=decision.limit_price, client_ref=client_ref,
        )
        try:
            ack = await self._broker.place_limit_order(req)
        except BrokerRejected as exc:
            self._repo.update_order_log(
                client_ref=str(client_ref), status="REJECTED",
                api_code=exc.code, api_message=exc.message,
                settled_at=self._clock.now(),
            )
            restored = self._restore(cycle, stage, pending, is_buy)
            self._emit(OrderRejected(
                config_id=config.config_id, cycle_id=cycle.cycle_id,
                stage_no=stage.stage_no, api_code=exc.code,
                api_message=exc.message, at=self._clock.now(),
            ))
            return SendOutcome("REJECTED", str(client_ref), None, restored)
        except BrokerTimeout:
            return await self._resolve_unknown(
                cycle=cycle, config=config, stage=stage, pending=pending,
                client_ref=client_ref, is_buy=is_buy,
            )

        self._repo.update_order_log(
            client_ref=str(client_ref), status="ACCEPTED",
            broker_order_id=ack.broker_order_id,
        )
        return SendOutcome("ACCEPTED", str(client_ref), ack.broker_order_id,
                           pending)

    async def _resolve_unknown(
        self, *, cycle: Cycle, config: SplitConfig, stage: StageState,
        pending: StageState, client_ref: uuid.UUID, is_buy: bool,
    ) -> SendOutcome:
        """D12 — 재발주 금지. 조회로 접수 여부를 확인한다."""
        self._repo.update_order_log(
            client_ref=str(client_ref), status="UNKNOWN",
            api_message="응답 유실 — 당일 주문 조회로 확인 중",
        )
        self._emit(OrderUnknown(
            config_id=config.config_id, cycle_id=cycle.cycle_id,
            stage_no=stage.stage_no, client_ref=str(client_ref),
            at=self._clock.now(),
        ))
        orders = await self._broker.list_orders_today(config.stock_code)
        found = next((o for o in orders if o.client_ref == client_ref), None)
        if found is not None:
            self._repo.update_order_log(
                client_ref=str(client_ref), status="ACCEPTED",
                broker_order_id=found.broker_order_id,
            )
            return SendOutcome("UNKNOWN_ACCEPTED", str(client_ref),
                               found.broker_order_id, pending)
        # 미접수 확인 — CANCELED 로 종결한다. REJECTED 는 브로커의 명시적
        # 판단(그리고 api_code)을 위해 남긴다.
        self._repo.update_order_log(
            client_ref=str(client_ref), status="CANCELED",
            api_message="응답 유실 후 당일 주문 조회에서 미접수 확인",
            settled_at=self._clock.now(),
        )
        restored = self._restore(cycle, stage, pending, is_buy)
        return SendOutcome("UNKNOWN_NOT_SENT", str(client_ref), None, restored)

    def _restore(
        self, cycle: Cycle, original: StageState, pending: StageState,
        is_buy: bool,
    ) -> StageState:
        """발주 실패 후 단계를 원래 상태로 되돌린다.

        매도의 경우 `cancel_sell` 이 `remaining_qty` 를 요구한다. 발주 자체가
        실패했으므로 체결은 0 이고 원래 `fill_qty` 를 그대로 넘긴다 — 잘못
        넘기면 보유가 조용히 줄고, 그 수량이 이후 모든 목표가 계산의 근거가
        된다.
        """
        if is_buy:
            restored = stage_mod.cancel_buy(pending)
        else:
            restored = stage_mod.cancel_sell(pending,
                                             remaining_qty=original.fill_qty)
        self._repo.save_stage(cycle.cycle_id, restored)
        return restored
```

`emit` 의 타입은 `Callable[[Event], None]` 이다 — `from collections.abc import Callable` 을 쓰고 `emit: Callable[[Event], None]` 로 선언한다. 위 코드의 `"object"` 를 그렇게 고친다.

- [ ] **Step 8: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/ports tests/adapters tests/engine -q`
Expected: PASS

- [ ] **Step 9: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — 예외를 옮긴 것이 기존 어댑터 테스트를 깨지 않아야 한다.

- [ ] **Step 10: 커밋**

```bash
git add src/autotrading7s/ports src/autotrading7s/adapters src/autotrading7s/engine/executor.py tests
git commit -m "$(printf 'feat: 주문 발주 파이프라인 — 설계서 9절 ③④⑤\n\n기록·커밋이 발주보다 먼저 온다. 발주 후에 기록하면 그 사이에 죽었을 때 고아\n주문이 생기고 다음 실행에서 중복 발주가 된다. 반대 순서의 최악은 조회로\n정정할 수 있다 — 잘못 기록된 쪽이 잘못 잊힌 쪽보다 항상 낫다.\n\nUNKNOWN 분기에서 재발주하지 않고 list_orders_today 로 client_ref 를 대조한다\n(D12). 접수됨이면 ACCEPTED 로 정정하고 미접수면 단계를 복구한다. 주문이 정확히\n하나인 것을 테스트가 직접 확인한다.\n\n브로커 예외를 ports/broker.py 로 올렸다. 엔진은 adapters 를 import 할 수 없어서\n예외가 어댑터에만 있으면 except Exception 을 쓰게 되고, 그것은 DB 손상과\n프로그래밍 오류까지 응답 유실로 취급하는 것이다. BrokerTimeout 이 TimeoutError\n를 상속하지 않는 2A 의 결정은 그대로 유지했다.\n\nstage_row_id 를 포트에 추가했다. order_log.stage_state_id 를 채울 방법이 없으면\n재시작 복구가 미체결 주문을 어느 단계의 것인지 알 수 없다.')"
```

---

## Task 5: 체결 감시와 미체결 타임아웃 (설계서 9절 ⑥)

**부분체결의 비대칭이 이 태스크의 핵심이다.** 매수 부분체결은 체결분으로 **보유를 만들고**, 매도 부분체결은 체결분만큼 **보유를 줄인다**. 두 경로가 같은 규칙("체결분 확정, 잔량 취소")에서 나오지만 도착하는 상태가 다르다.

**Ruling: `cancel_order` 실패는 `STILL_OPEN` 으로 남긴다.** 취소가 실패하면 브로커에 주문이 살아 있으므로 단계를 PENDING 에 두는 것이 사실에 맞다. 다음 폴에서 재시도되고, 그 사이 규칙 5 가 이 단계를 판정에서 제외하므로 중복 발주는 없다. 무한 재시도의 가능성은 대사(Task 8)와 화면이 드러낸다. 틀렸을 경우 비용: 브로커가 취소를 계속 거부하면 그 단계가 장 마감까지 묶인다 — 한국 주식 주문은 당일에만 유효하므로 다음 날 자동 해소된다.

**Files:**
- Modify: `src/autotrading7s/engine/executor.py`, `tests/conftest.py`
- Test: `tests/engine/test_executor_fill.py`

**Interfaces:**
- Consumes: Task 4 의 `Executor`
- Produces:
  - `FILL_ACTIONS = frozenset({"FILLED", "PARTIAL_CONFIRMED", "CANCELED_UNFILLED", "STILL_OPEN", "GONE"})`
  - `FillOutcome` — `action: str`, `stage: StageState`, `filled_qty: int`, `filled_price: int | None`
  - `Executor.poll_fill(*, cycle, config, stage, client_ref, broker_order_id, sent_at, timeout_sec) -> FillOutcome` (async)

- [ ] **Step 1: 픽스처를 추가한다**

`tests/conftest.py` 에 (Task 4 에서 여기로 옮긴 픽스처들 옆에):

```python
@pytest.fixture
def repo_fresh(tmp_path):
    """체결 없는 RUNNING 사이클 — 단계 7개 전부 WAITING.

    단계금액 1,000,000원 / 앵커 10,000원이므로 1단계 계획수량은 100주다.
    """
    repo = _new_repo(tmp_path)
    _seed(repo, code="005930", name="삼성전자", amount=1_000_000,
          limit=99_999_999, fills=[])
    return repo
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/engine/test_executor_fill.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import Event, StageFilled
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.domain.types import StageStatus, Tick, TickSource
from autotrading7s.engine.executor import Executor

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _tick(price: int) -> Tick:
    return Tick(code="005930", price=price, at=AT, source=TickSource.WS)


def _make(repo, broker):
    clock = FakeClock(current=AT)
    events: list[Event] = []
    return Executor(repo=repo, broker=broker, clock=clock,
                    emit=events.append), clock, events


async def _buy_leg(repo, broker, ex, *, qty=100, price=10_000, stage_no=1):
    cyc = repo.load_active_cycles()[0]
    config = repo.load_config(cyc.config_id)
    stage = next(s for s in repo.load_stages(cyc.cycle_id)
                 if s.stage_no == stage_no)
    outcome = await ex.send(cycle=cyc, config=config, stage=stage,
                            decision=BuyStage(stage_no=stage_no,
                                              limit_price=price, qty=qty,
                                              reason="매수"),
                            tick=_tick(price))
    return cyc, config, outcome


@pytest.mark.asyncio
async def test_full_fill_moves_the_stage_to_holding(repo_fresh):
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "FILLED"
    assert out.stage.status is StageStatus.HOLDING
    assert (out.stage.fill_price, out.stage.fill_qty) == (10_000, 100)
    assert repo_fresh.load_stages(cyc.cycle_id)[0].fill_qty == 100
    assert [type(e) for e in events] == [StageFilled]
    assert events[0].fill_qty == 100


@pytest.mark.asyncio
async def test_unfilled_order_stays_open_before_the_timeout(repo_fresh):
    """3초가 지나기 전에는 취소하지 않는다 — 유동성이 낮으면 곧 체결된다."""
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)
    clock.advance(2.9)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "STILL_OPEN"
    assert out.stage.status is StageStatus.BUY_PENDING
    assert events == []


@pytest.mark.asyncio
async def test_unfilled_order_is_canceled_at_the_timeout(repo_fresh):
    """⑥ 3초 후 미체결 → 취소 → WAITING (다음 틱에 재시도)."""
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)
    clock.advance(3.0)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "CANCELED_UNFILLED"
    assert out.stage.status is StageStatus.WAITING
    assert repo_fresh.load_pending_orders() == []
    assert events == []


@pytest.mark.asyncio
async def test_partial_buy_confirms_the_filled_portion(repo_fresh):
    """설계서 200행 — 매수 부분체결은 체결 수량만으로 HOLDING 을 확정하고
    잔량 주문을 취소한다.

    보유가 계획수량보다 적게 생기는 것이 정상이다. 계획수량으로 확정하면
    사지 않은 주식을 보유로 기록하게 되고, 목표가 매도에서 과다매도가 된다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.PARTIAL,
                        partial_ratio=__import__("decimal").Decimal("0.4"),
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)
    clock.advance(3.0)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "PARTIAL_CONFIRMED"
    assert out.stage.status is StageStatus.HOLDING
    assert out.stage.fill_qty == 40           # 100주 요청, 40주 체결
    assert out.stage.fill_price == 10_000
    assert repo_fresh.load_pending_orders() == []


@pytest.mark.asyncio
async def test_partial_sell_returns_the_remainder_to_holding(repo_fresh):
    """매도 부분체결의 비대칭 — 체결분만 매도로 처리하고 잔량은 보유로 복귀.

    한국 주식 주문은 당일에만 유효하므로 부분체결 매도의 잔량이 취소되면
    보유가 줄어드는 것이 일상적 경로다 (cancel_sell 의 존재 이유).
    """
    from decimal import Decimal

    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)
    filled = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                                client_ref=sent.client_ref,
                                broker_order_id=sent.broker_order_id,
                                sent_at=AT, timeout_sec=3)
    assert filled.stage.fill_qty == 100

    broker._fill_mode = FillMode.PARTIAL      # 매도만 부분체결로 바꾼다
    broker._partial_ratio = Decimal("0.4")
    sell = await ex.send(cycle=cyc, config=config, stage=filled.stage,
                         decision=SellStage(stage_no=1, limit_price=10_500,
                                            qty=100, reason="매도"),
                         tick=_tick(10_500))
    clock.advance(3.0)
    out = await ex.poll_fill(cycle=cyc, config=config, stage=sell.stage,
                             client_ref=sell.client_ref,
                             broker_order_id=sell.broker_order_id,
                             sent_at=clock.now() - timedelta(seconds=3),
                             timeout_sec=3)

    assert out.action == "PARTIAL_CONFIRMED"
    assert out.stage.status is StageStatus.HOLDING
    assert out.stage.fill_qty == 60           # 100 − 40
    assert out.stage.fill_price == 10_000     # 취득원가는 불변


@pytest.mark.asyncio
async def test_realized_pnl_is_exact_across_a_partial_sell(repo_fresh):
    """부분체결 매도 후 잔량을 다시 팔면 실현손익이 정확히 맞아야 한다.

    100주를 10,000원에 사서 10,500원에 40주 + 60주로 나눠 팔면 정확히
    100 × 500 = 50,000원이다. 이 값이 틀리는 방식이 두 가지 있고 둘 다
    이 프로젝트가 이미 겪었다: (1) fill_qty 를 증분으로 기록하면 매수량이
    부풀려져 원가가 과소평가된다, (2) 잔량 취소로 생긴 CANCELED 행의 체결
    데이터가 집계에서 빠지면 매도금액이 통째로 사라진다. 후자가 Plan 2A 의
    최악의 결함이었다 (보고 +399,200 / 진짜 +19,200).
    """
    from decimal import Decimal

    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)
    held = (await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                               client_ref=sent.client_ref,
                               broker_order_id=sent.broker_order_id,
                               sent_at=AT, timeout_sec=3)).stage

    broker._fill_mode = FillMode.PARTIAL
    broker._partial_ratio = Decimal("0.4")
    first = await ex.send(cycle=cyc, config=config, stage=held,
                          decision=SellStage(stage_no=1, limit_price=10_500,
                                             qty=100, reason="매도"),
                          tick=_tick(10_500))
    clock.advance(3.0)
    after_partial = await ex.poll_fill(
        cycle=cyc, config=config, stage=first.stage,
        client_ref=first.client_ref, broker_order_id=first.broker_order_id,
        sent_at=clock.now() - timedelta(seconds=3), timeout_sec=3)
    assert after_partial.stage.fill_qty == 60

    broker._fill_mode = FillMode.INSTANT
    second = await ex.send(cycle=cyc, config=config,
                           stage=after_partial.stage,
                           decision=SellStage(stage_no=1, limit_price=10_500,
                                              qty=60, reason="잔량 매도"),
                           tick=_tick(10_500))
    done = await ex.poll_fill(cycle=cyc, config=config, stage=second.stage,
                              client_ref=second.client_ref,
                              broker_order_id=second.broker_order_id,
                              sent_at=clock.now(), timeout_sec=3)

    assert done.action == "FILLED"
    assert repo_fresh.realized_pnl_for_cycle(cyc.cycle_id) == 50_000


@pytest.mark.asyncio
async def test_full_sell_respects_allow_rebuy(repo_fresh):
    """전량 매도 후 목적지는 설정이 정한다 — allow_rebuy 면 WAITING, 아니면 SOLD."""
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)
    held = (await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                               client_ref=sent.client_ref,
                               broker_order_id=sent.broker_order_id,
                               sent_at=AT, timeout_sec=3)).stage
    assert config.allow_rebuy is True

    sell = await ex.send(cycle=cyc, config=config, stage=held,
                         decision=SellStage(stage_no=1, limit_price=10_500,
                                            qty=100, reason="매도"),
                         tick=_tick(10_500))
    out = await ex.poll_fill(cycle=cyc, config=config, stage=sell.stage,
                             client_ref=sell.client_ref,
                             broker_order_id=sell.broker_order_id,
                             sent_at=clock.now(), timeout_sec=3)

    assert out.stage.status is StageStatus.WAITING
    assert out.stage.rebuy_count == 1
    assert out.stage.fill_qty is None


@pytest.mark.asyncio
async def test_cancel_failure_keeps_the_stage_pending(repo_fresh):
    """취소가 실패하면 브로커에 주문이 살아 있으므로 PENDING 이 사실이다."""
    from autotrading7s.ports.broker import BrokerRejected

    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex, clock, events = _make(repo_fresh, broker)
    cyc, config, sent = await _buy_leg(repo_fresh, broker, ex)

    async def refuse(broker_order_id):
        raise BrokerRejected("40560", "취소 불가")

    broker.cancel_order = refuse              # type: ignore[method-assign]
    clock.advance(5.0)

    out = await ex.poll_fill(cycle=cyc, config=config, stage=sent.stage,
                             client_ref=sent.client_ref,
                             broker_order_id=sent.broker_order_id,
                             sent_at=AT, timeout_sec=3)

    assert out.action == "STILL_OPEN"
    assert out.stage.status is StageStatus.BUY_PENDING
    assert [r.status for r in repo_fresh.load_pending_orders()] == ["ACCEPTED"]
```

- [ ] **Step 3: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/engine/test_executor_fill.py -q`
Expected: FAIL — `AttributeError: 'Executor' object has no attribute 'poll_fill'`

- [ ] **Step 4: 구현한다**

`engine/executor.py` 에 추가한다.

```python
FILL_ACTIONS = frozenset(
    {"FILLED", "PARTIAL_CONFIRMED", "CANCELED_UNFILLED", "STILL_OPEN", "GONE"}
)

_ORDER_LOG_STATUS = {
    FillState.OPEN: "ACCEPTED",
    FillState.PARTIAL: "PARTIAL",
    FillState.FILLED: "FILLED",
    FillState.CANCELED: "CANCELED",
    FillState.REJECTED: "REJECTED",
}


@dataclass(frozen=True, slots=True)
class FillOutcome:
    action: str
    stage: StageState
    filled_qty: int
    filled_price: int | None

    def __post_init__(self) -> None:
        if self.action not in FILL_ACTIONS:
            raise ValueError(f"unknown fill action: {self.action!r}")
```

```python
    async def poll_fill(
        self, *, cycle: Cycle, config: SplitConfig, stage: StageState,
        client_ref: str, broker_order_id: str, sent_at: datetime,
        timeout_sec: int,
    ) -> FillOutcome:
        """설계서 9절 ⑥ — 체결 대기와 3초 타임아웃.

        `stage` 는 PENDING 상태여야 한다. 매수/매도는 그 상태에서 읽는다 —
        인자로 따로 받으면 두 정보가 어긋날 수 있다.

        브로커가 보고하는 `filled_qty` 는 **누적**, `filled_price` 는
        **수량가중평균**이며 그대로 `update_order_log` 에 넘긴다 (2A 핸드오버
        6). 증분으로 다루면 취득원가가 과소 계상되어 사용자에게 보고되는
        이익이 부풀려진다.
        """
        if stage.status is StageStatus.BUY_PENDING:
            is_buy = True
        elif stage.status is StageStatus.SELL_PENDING:
            is_buy = False
        else:
            raise ValueError(
                f"poll_fill requires a pending stage, got {stage.status}"
            )

        status = await self._broker.get_order(broker_order_id)
        now = self._clock.now()

        if status.state is FillState.REJECTED:
            self._repo.update_order_log(
                client_ref=client_ref, status="REJECTED",
                api_code=status.api_code, api_message=status.api_message,
                settled_at=now,
            )
            restored = self._restore(cycle, stage, stage, is_buy)
            self._emit(OrderRejected(
                config_id=config.config_id, cycle_id=cycle.cycle_id,
                stage_no=stage.stage_no, api_code=status.api_code,
                api_message=status.api_message, at=now,
            ))
            return FillOutcome("GONE", restored, 0, None)

        if status.state is FillState.FILLED:
            return self._apply_fill(
                cycle=cycle, config=config, stage=stage, status=status,
                client_ref=client_ref, is_buy=is_buy, now=now,
                action="FILLED", terminal_status="FILLED",
            )

        if status.filled_qty > 0:
            # 누적 체결을 기록한다. settled_at 을 넣지 않는 이유: PARTIAL 은
            # 아직 종결이 아니고, 종결로 기록하면 2A 의 체결값 불변 가드가
            # 이후의 정상 갱신(PARTIAL → FILLED)을 거부한다.
            self._repo.update_order_log(
                client_ref=client_ref, status="PARTIAL",
                fill_price=status.filled_price, fill_qty=status.filled_qty,
            )

        if now - sent_at < timedelta(seconds=timeout_sec):
            return FillOutcome("STILL_OPEN", stage, status.filled_qty,
                               status.filled_price)

        # 타임아웃 — 잔량을 취소한다
        try:
            await self._broker.cancel_order(broker_order_id)
        except BrokerError:
            # 취소 실패는 브로커에 주문이 살아 있다는 뜻이므로 PENDING 이
            # 사실이다. 다음 폴에서 재시도되고, 규칙 5 가 이 단계를 판정에서
            # 제외하므로 중복 발주는 없다.
            return FillOutcome("STILL_OPEN", stage, status.filled_qty,
                               status.filled_price)

        if status.filled_qty > 0:
            return self._apply_fill(
                cycle=cycle, config=config, stage=stage, status=status,
                client_ref=client_ref, is_buy=is_buy, now=now,
                action="PARTIAL_CONFIRMED", terminal_status="CANCELED",
            )

        self._repo.update_order_log(
            client_ref=client_ref, status="CANCELED", settled_at=now,
        )
        restored = self._restore(cycle, stage, stage, is_buy)
        return FillOutcome("CANCELED_UNFILLED", restored, 0, None)

    def _apply_fill(
        self, *, cycle: Cycle, config: SplitConfig, stage: StageState,
        status: OrderStatus, client_ref: str, is_buy: bool, now: datetime,
        action: str, terminal_status: str,
    ) -> FillOutcome:
        """체결을 단계에 반영한다 — 매수와 매도의 비대칭이 여기 있다.

        매수 부분체결은 체결분으로 **보유를 만든다**(설계서 200행). 매도
        부분체결은 체결분만큼 **보유를 줄인다** — 잔량이 취소되어 돌아오는
        것이 `cancel_sell` 의 존재 이유다.
        """
        self._repo.update_order_log(
            client_ref=client_ref, status=terminal_status,
            fill_price=status.filled_price, fill_qty=status.filled_qty,
            settled_at=now,
        )
        if is_buy:
            applied = stage_mod.to_holding(
                stage, fill_price=status.filled_price,
                fill_qty=status.filled_qty, at=now,
            )
        elif status.filled_qty >= stage.fill_qty:
            applied = stage_mod.after_sell(stage, at=now,
                                           allow_rebuy=config.allow_rebuy)
        else:
            applied = stage_mod.cancel_sell(
                stage, remaining_qty=stage.fill_qty - status.filled_qty,
            )
        self._repo.save_stage(cycle.cycle_id, applied)
        self._emit(StageFilled(
            config_id=config.config_id, cycle_id=cycle.cycle_id,
            stage_no=stage.stage_no, side="BUY" if is_buy else "SELL",
            fill_price=status.filled_price, fill_qty=status.filled_qty, at=now,
        ))
        return FillOutcome(action, applied, status.filled_qty,
                           status.filled_price)
```

새 import: `datetime`, `timedelta`, `FillState`, `OrderStatus`, `BrokerError`, `StageFilled`.

- [ ] **Step 5: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/engine -q`
Expected: PASS

- [ ] **Step 6: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add src/autotrading7s/engine/executor.py tests/engine/test_executor_fill.py tests/conftest.py
git commit -m "$(printf 'feat: 체결 감시와 미체결 타임아웃 — 설계서 9절 ⑥\n\n부분체결의 비대칭이 이 커밋의 핵심이다. 매수 부분체결은 체결분으로 보유를\n만들고(설계서 200행), 매도 부분체결은 체결분만큼 보유를 줄인다. 같은 규칙에서\n나오지만 도착 상태가 다르다.\n\n브로커가 보고하는 filled_qty 를 누적값 그대로 order_log 에 넘긴다. 증분으로\n다루면 취득원가가 과소 계상되어 보고되는 이익이 부풀려진다 — Plan 2A 최악의\n결함과 같은 방향이다. 부분체결 매도 후 잔량을 재매도해 실현손익이 정확히\n50,000원인 것을 확인하는 테스트가 두 실패 방식을 함께 막는다.\n\nPARTIAL 갱신에 settled_at 을 넣지 않는다. 종결로 기록하면 2A 의 체결값 불변\n가드가 이후의 정상 갱신(PARTIAL → FILLED)을 거부한다.\n\n취소 실패는 STILL_OPEN 으로 남긴다 — 브로커에 주문이 살아 있으므로 PENDING 이\n사실이고, 규칙 5 가 그 단계를 판정에서 제외하므로 중복 발주는 없다.')"
```

---

## Task 6: D20 강제 종료의 도메인과 쓰기 경로

**배경 (2A 핸드오버 1).** 강제 종료의 쓰기 경로가 통째로 없다. `save_cycle(close_reason=FORCED)` 는 스키마 CHECK 가 `forced_close_reason`·`forced_close_qty` 를 요구하지만 `Cycle` 에 그 필드가 없어 `IntegrityError` 를 내고, `save_stage(force_sold(...))` 는 `force_sold` 가 전이표를 의도적으로 우회하는데 가드가 그 표를 참조해서 `StageInvariantError` 를 낸다. **가드가 이 불일치를 드러낸 것이 이득이다** — 가드 전에는 단계 쓰기만 조용히 성공하고 사이클 쓰기가 거부되어 절반만 강제 종료된 상태가 남았을 것이다.

**Ruling: `save_stage` 에 우회 플래그를 두지 않고, 전용 포트 메서드 `emergency_close_cycle` 을 둔다.** 강제 종료는 본질적으로 원자적이다 — 설계서 11.4절 ⑤와 ⑥이 함께 일어나야 하고, 절반만 된 상태가 바로 핸드오버가 경고한 그 상태다. 전용 메서드는 사이클과 모든 단계를 한 트랜잭션에 쓰고, `save_stage` 의 가드를 희석하지 않는다.

**이 메서드는 긴급청산(11.1절 ⑤⑦)도 함께 담당한다.** 두 경로가 같은 문제를 갖는다 — `force_sold` 로 전 단계를 일괄 갱신해야 하는데 `save_stage` 가 그것을 거부한다. 그래서 `close_reason` 이 `EMERGENCY` 이거나 `FORCED` 인 사이클만 받는다. `NORMAL` 종료는 이 문을 쓸 수 없다 — 정상 경로는 `save_stage` 의 가드를 통과해야 하고, 그것이 `close()` 의 보유 0 검사가 의미를 갖는 이유다. 틀렸을 경우 비용: 포트 메서드가 하나 늘어난다(18개→19개).

**Ruling: `forced_close_qty` 는 `Cycle` 의 필드로 둔다** (전용 테이블이 아니라). 설계서 11.4절이 그 값을 `cycle` 테이블 컬럼으로 이미 규정했고, 스키마 CHECK 가 `close_reason='FORCED'` 와 짝지어 강제한다. 도메인에 두면 그 CHECK 와 도메인 불변식이 같은 것을 두 층에서 말하게 되어 어긋날 수 없다.

**Files:**
- Modify: `src/autotrading7s/domain/cycle.py`, `src/autotrading7s/adapters/sqlite/mapping.py`, `src/autotrading7s/ports/repository.py`, `src/autotrading7s/adapters/sqlite/repository.py`
- Test: `tests/domain/test_cycle_force_close.py`, `tests/adapters/test_repository_force_close.py`

**Interfaces:**
- Produces:
  - `Cycle.forced_close_reason: str | None`, `Cycle.forced_close_qty: int | None`
  - `cycle.force_close(cycle, *, reason: str, qty: int, at: datetime) -> Cycle`
  - `RepositoryPort.emergency_close_cycle(*, cycle: Cycle, stages: Sequence[StageState]) -> None`
  - `RepositoryPort.set_realized_pnl(cycle_id: int, value: int) -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 도메인**

`tests/domain/test_cycle_force_close.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.cycle import Cycle, IllegalCycleTransition
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import CloseReason, CycleStatus

AT = datetime(2026, 9, 2, 15, 28, tzinfo=UTC)


def _ladder() -> Ladder:
    return Ladder(anchor_price=10_000, drop_pct=Decimal("0.05"),
                  target_pct=Decimal("0.05"), max_stages=7,
                  amount_per_stage=1_000_000)


def _liquidating() -> Cycle:
    return Cycle(cycle_id=1, config_id=1, seq=1,
                 status=CycleStatus.LIQUIDATING, anchor_price=10_000,
                 ladder=_ladder(), started_at=AT)


def test_force_close_records_the_statement_and_the_remainder():
    """설계서 11.4절 ⑤ — 증언과 잔량이 둘 다 기록된다."""
    closed = cycle_mod.force_close(
        _liquidating(), reason="거래정지로 청산 불가, 잔량 40주는 직접 처리 예정",
        qty=40, at=AT,
    )
    assert closed.status is CycleStatus.CLOSED
    assert closed.close_reason is CloseReason.FORCED
    assert closed.forced_close_qty == 40
    assert "거래정지" in closed.forced_close_reason
    assert closed.closed_at == AT


def test_force_close_only_from_liquidating():
    """설계서 11.4절 설계 제약 — 사용자가 먼저 긴급청산을 시도해야 한다.

    그 시도 이력(횟수·시각·실패 사유)이 강제 종료 다이얼로그의 근거가 된다.
    RUNNING 에서 바로 강제 종료하는 경로를 두면 그 근거 없이 내부 기록과
    실계좌를 어긋나게 만들 수 있다.
    """
    for status in (CycleStatus.RUNNING, CycleStatus.PAUSED,
                   CycleStatus.STARTING):
        cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status,
                    anchor_price=10_000, ladder=_ladder(), started_at=AT)
        with pytest.raises(IllegalCycleTransition):
            cycle_mod.force_close(cyc, reason="사유", qty=40, at=AT)


def test_force_close_does_not_check_completion():
    """close() 와 달리 단계 목록을 요구하지 않는다 — 보유가 남은 채로 끝난다.

    이것이 close() 의 우회가 아니라 별도 경로인 이유다. close() 는 보유 0 을
    확인하고, force_close 는 보유가 남았다는 사실 자체를 기록한다.
    """
    import inspect
    source = inspect.getsource(cycle_mod.force_close)
    assert "is_cycle_complete" not in source


def test_force_close_rejects_an_empty_statement():
    with pytest.raises(DomainInvariantError, match="reason"):
        cycle_mod.force_close(_liquidating(), reason="   ", qty=40, at=AT)


def test_force_close_rejects_zero_remainder():
    """설계서 11.4절 절차 ③ — 잔량이 0 이면 정상 close() 로 처리해야 한다.

    잔량 0 의 강제 종료는 의미가 없고, 허용하면 정상 종료 경로를 우회해
    보유 0 검사를 건너뛰는 수단이 된다.
    """
    with pytest.raises(DomainInvariantError, match="qty"):
        cycle_mod.force_close(_liquidating(), reason="사유", qty=0, at=AT)
    with pytest.raises(TypeError):
        cycle_mod.force_close(_liquidating(), reason="사유", qty=1.0, at=AT)


def test_forced_fields_and_close_reason_must_agree():
    """스키마의 D20 CHECK 와 같은 것을 도메인에서도 말한다.

    두 층이 같은 불변식을 말하면 어긋날 수 없다. 한 층만 말하면 다른 경로로
    들어온 값이 통과한다.
    """
    with pytest.raises(DomainInvariantError, match="FORCED"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED,
              close_reason=CloseReason.FORCED, started_at=AT, closed_at=AT)
    with pytest.raises(DomainInvariantError, match="FORCED"):
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.CLOSED,
              close_reason=CloseReason.NORMAL, forced_close_qty=40,
              forced_close_reason="사유", started_at=AT, closed_at=AT)


def test_normal_close_leaves_forced_fields_empty():
    from autotrading7s.domain.stage import StageState
    from autotrading7s.domain.types import StageStatus

    states = [StageState(stage_no=n, status=StageStatus.WAITING,
                         trigger_price=_ladder().trigger_price(n),
                         planned_qty=_ladder().planned_qty(n))
              for n in range(1, 8)]
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
                anchor_price=10_000, ladder=_ladder(), started_at=AT)
    closed = cycle_mod.close(cyc, reason=CloseReason.NORMAL, at=AT,
                             states=states)
    assert closed.forced_close_reason is None
    assert closed.forced_close_qty is None
```

- [ ] **Step 2: 실패하는 테스트를 쓴다 — 쓰기 경로**

`tests/adapters/test_repository_force_close.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus
from autotrading7s.ports.repository import RepositoryPort, StageInvariantError

AT = datetime(2026, 9, 2, 15, 28, tzinfo=UTC)


def _liquidating(repo):
    """첫 사이클(005930)을 LIQUIDATING 으로 만들어 반환한다.

    `load_active_cycles` 는 `ORDER BY id` 이므로 [0] 이 005930 이다 —
    `test_forced_close_removes_the_stock_from_holdings` 가 이 순서에 의존한다.
    """
    cyc = repo.load_active_cycles()[0]
    liquidating = cycle_mod.begin_liquidation(cyc)
    repo.save_cycle(liquidating)
    return liquidating


def test_force_close_writes_cycle_and_stages_together(repo_two_stocks):
    """설계서 11.4절 ⑤⑥ — 절반만 강제 종료된 상태가 남지 않아야 한다."""
    cyc = _liquidating(repo_two_stocks)
    stages = [stage_mod.force_sold(s, at=AT)
              for s in repo_two_stocks.load_stages(cyc.cycle_id)]
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)

    repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)

    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.status is CycleStatus.CLOSED
    assert reloaded.close_reason is CloseReason.FORCED
    assert reloaded.forced_close_qty == 100
    assert reloaded.forced_close_reason == "거래정지"
    assert all(s.status is StageStatus.SOLD
               for s in repo_two_stocks.load_stages(cyc.cycle_id))


def test_forced_close_removes_the_stock_from_holdings(repo_two_stocks):
    """설계서 11.4절 — 강제 종료 후 그 종목은 프로그램의 관리 밖이다.

    남은 주식이 holdings 뷰에서 사라지는 것이 의도다. 그 수량은
    forced_close_qty 에 남아 대사 기준선이 된다 (Task 8).
    """
    cyc = _liquidating(repo_two_stocks)
    stages = [stage_mod.force_sold(s, at=AT)
              for s in repo_two_stocks.load_stages(cyc.cycle_id)]
    repo_two_stocks.emergency_close_cycle(
        cycle=cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT),
        stages=stages,
    )
    codes = {h.stock_code for h in repo_two_stocks.holdings()}
    assert "005930" not in codes
    assert "000660" in codes          # 다른 종목은 영향받지 않는다


def test_rejects_a_cycle_that_is_not_emergency_or_forced(repo_two_stocks):
    """이 메서드는 전이표를 우회하므로 입력을 엄격히 검사해야 한다.

    검사가 없으면 이것이 save_stage 가드의 우회 수단이 되고, 그러면 가드가
    막고 있는 모든 것(체결값 덮어쓰기, 상태 역행)이 이 문으로 들어온다.
    정상 종료(NORMAL)도 거부한다 — 정상 경로는 close() 의 보유 0 검사와
    save_stage 의 가드를 통과해야 한다.
    """
    cyc = _liquidating(repo_two_stocks)
    stages = [stage_mod.force_sold(s, at=AT)
              for s in repo_two_stocks.load_stages(cyc.cycle_id)]
    with pytest.raises(ValueError, match="EMERGENCY"):
        repo_two_stocks.emergency_close_cycle(cycle=cyc, stages=stages)
    # 전 단계가 SOLD 이므로 정상 close() 가 성립한다 — 그래도 이 문은 막힌다
    normal = cycle_mod.close(cyc, reason=CloseReason.NORMAL, at=AT,
                             states=stages)
    with pytest.raises(ValueError, match="EMERGENCY"):
        repo_two_stocks.emergency_close_cycle(cycle=normal, stages=stages)


def test_rejects_stages_that_are_not_all_sold(repo_two_stocks):
    cyc = _liquidating(repo_two_stocks)
    stages = repo_two_stocks.load_stages(cyc.cycle_id)      # force_sold 안 함
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)
    with pytest.raises(StageInvariantError, match="SOLD"):
        repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)


def test_rejects_an_incomplete_stage_set(repo_two_stocks):
    """단계 일부만 쓰면 load_stages 가 이후 그 사이클을 로드할 수 없게 된다(H3)."""
    cyc = _liquidating(repo_two_stocks)
    stages = [stage_mod.force_sold(s, at=AT)
              for s in repo_two_stocks.load_stages(cyc.cycle_id)][:3]
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)
    with pytest.raises(StageInvariantError):
        repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)


def test_a_rejected_force_close_writes_nothing(repo_two_stocks):
    """원자성 — 거부된 강제 종료가 사이클만 CLOSED 로 남기면 안 된다."""
    cyc = _liquidating(repo_two_stocks)
    stages = repo_two_stocks.load_stages(cyc.cycle_id)
    closed = cycle_mod.force_close(cyc, reason="거래정지", qty=100, at=AT)
    with pytest.raises(StageInvariantError):
        repo_two_stocks.emergency_close_cycle(cycle=closed, stages=stages)
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)


def test_set_realized_pnl_round_trips(repo_two_stocks):
    """2A 핸드오버 2 — cycle_to_row 가 이 컬럼을 제외하므로 전용 메서드가 필요하다.

    사이클 종료 시 realized_pnl_for_cycle 의 값을 여기 기록하는 것이 엔진의
    몫이다. 리포지토리는 집계만 한다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    repo_two_stocks.set_realized_pnl(cyc.cycle_id, -580_000)
    row = repo_two_stocks._conn.execute(
        "SELECT realized_pnl FROM cycle WHERE id = ?", (cyc.cycle_id,)
    ).fetchone()
    assert dict(row)["realized_pnl"] == -580_000


def test_set_realized_pnl_rejects_a_missing_cycle(repo_two_stocks):
    from autotrading7s.ports.repository import RowNotFound
    with pytest.raises(RowNotFound):
        repo_two_stocks.set_realized_pnl(9999, 0)


def test_save_cycle_still_refuses_to_invent_forced_fields(repo_two_stocks):
    """save_stage 의 가드가 희석되지 않았음을 확인한다.

    force_sold 단계를 save_stage 로 쓰는 경로는 여전히 막혀 있어야 한다 —
    막혀 있지 않으면 전용 메서드를 둔 이유가 사라진다.
    """
    cyc = _liquidating(repo_two_stocks)
    holding = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.status is StageStatus.HOLDING)
    with pytest.raises(StageInvariantError):
        repo_two_stocks.save_stage(cyc.cycle_id,
                                   stage_mod.force_sold(holding, at=AT))


def test_port_declares_both_new_methods():
    for name in ("emergency_close_cycle", "set_realized_pnl"):
        assert name in RepositoryPort.__protocol_attrs__
```

- [ ] **Step 3: 두 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/domain/test_cycle_force_close.py tests/adapters/test_repository_force_close.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'force_close'`, `TypeError: Cycle.__init__() got an unexpected keyword argument 'forced_close_reason'`

- [ ] **Step 4: 도메인을 구현한다**

`Cycle` 에 두 필드를 추가한다 (기본값이 있으므로 마지막에 둔다).

```python
    forced_close_reason: str | None = None
    forced_close_qty: int | None = None
```

`__post_init__` 에 불변식을 추가한다.

```python
        forced = self.close_reason is CloseReason.FORCED
        has_fields = (self.forced_close_reason is not None
                      and self.forced_close_qty is not None)
        if forced != has_fields:
            # 스키마의 D20 CHECK 와 같은 불변식이다. 두 층이 같은 것을 말하면
            # 어긋날 수 없고, 한 층만 말하면 다른 경로로 들어온 값이 통과한다.
            raise DomainInvariantError(
                f"close_reason FORCED and forced_close_* must agree: "
                f"close_reason={self.close_reason}, "
                f"forced_close_reason={self.forced_close_reason!r}, "
                f"forced_close_qty={self.forced_close_qty!r}"
            )
        if self.forced_close_qty is not None:
            if (isinstance(self.forced_close_qty, bool)
                    or not isinstance(self.forced_close_qty, int)):
                raise TypeError(
                    f"forced_close_qty must be int, not "
                    f"{type(self.forced_close_qty).__name__}"
                )
            if self.forced_close_qty <= 0:
                raise DomainInvariantError(
                    f"forced_close_qty must be positive: {self.forced_close_qty}"
                )
```

새 전이 함수를 `close` 바로 뒤에 둔다.

```python
def force_close(cycle: Cycle, *, reason: str, qty: int, at: datetime) -> Cycle:
    """D20 강제 종료 — 설계서 11.4절.

    `close()` 의 우회가 아니라 별도 경로다. `close()` 는 보유 0 을 확인하고,
    이 함수는 **보유가 남았다는 사실 자체를 기록한다.** 그래서 단계 목록을
    요구하지 않는다.

    `LIQUIDATING` 에서만 호출할 수 있다. 사용자가 먼저 긴급청산을 시도해야
    하며, 그 시도 이력(횟수·시각·실패 사유)이 강제 종료 다이얼로그의 근거가
    된다. `RUNNING` 에서 바로 강제 종료하는 경로를 두면 그 근거 없이 내부
    기록과 실계좌를 어긋나게 만들 수 있다.

    설계서 10.2절이 금지하는 것과 구분된다 — 10.2절이 금지하는 것은
    **프로그램이** 불일치를 조용히 만드는 것이고, 이것은 사용자가 "잔량이
    얼마인지 알고 있으며 내가 처리한다" 고 명시적으로 증언하는 것이다.
    """
    if cycle.status is not CycleStatus.LIQUIDATING:
        raise IllegalCycleTransition(
            f"force_close requires LIQUIDATING, not {cycle.status.value} "
            f"(설계서 11.4절 — 긴급청산을 먼저 시도해야 한다)"
        )
    if isinstance(qty, bool) or not isinstance(qty, int):
        raise TypeError(f"qty must be int, not {type(qty).__name__}")
    if qty <= 0:
        raise DomainInvariantError(
            f"qty must be positive: {qty} — 잔량 0 의 강제 종료는 정상 close() "
            f"로 처리한다 (설계서 11.4절 절차 ③)"
        )
    if not reason or not reason.strip():
        raise DomainInvariantError("reason must be a non-empty statement")
    return replace(
        cycle, status=CycleStatus.CLOSED, close_reason=CloseReason.FORCED,
        closed_at=at, forced_close_reason=reason, forced_close_qty=qty,
    )
```

`_guard` 를 쓰지 않는 이유를 주석으로 남긴다: `_guard` 는 `_ALLOWED` 전이표를 보고 `LIQUIDATING → CLOSED` 를 허용하지만, 이 함수는 그것보다 **좁은** 조건(LIQUIDATING 만)을 강제하므로 직접 검사하는 것이 의도를 드러낸다.

- [ ] **Step 5: 매핑과 리포지토리를 구현한다**

`mapping.cycle_to_row` 에 두 키를 추가한다.

```python
        "forced_close_reason": cycle.forced_close_reason,
        "forced_close_qty": cycle.forced_close_qty,
```

`mapping.row_to_cycle` 의 `Cycle(...)` 에 두 인자를 추가한다.

```python
            forced_close_reason=row["forced_close_reason"],
            forced_close_qty=row["forced_close_qty"],
```

`realized_pnl` 은 여전히 두 함수 모두에서 제외한다 — `set_realized_pnl` 이 유일한 쓰기 경로이고, `Cycle` 에 그 필드가 없다.

`SqliteRepository` 에 두 메서드를 추가한다.

```python
    def emergency_close_cycle(
        self, *, cycle: Cycle, stages: Sequence[StageState]
    ) -> None:
        """D20 — 사이클과 모든 단계를 한 트랜잭션에 쓴다 (설계서 11.4절 ⑤⑥).

        `save_stage` 를 쓰지 않는 이유: `force_sold` 는 전이표를 의도적으로
        우회하는데 `save_stage` 의 가드는 그 표를 참조한다. 우회 플래그를 두면
        가드가 막고 있는 모든 것(체결값 덮어쓰기, 상태 역행)이 그 문으로
        들어온다. 그래서 전용 경로를 두고 **입력을 엄격히 검사한다.**

        원자적이어야 하는 이유: 절반만 강제 종료된 상태 — 사이클은 CLOSED 인데
        단계가 HOLDING 으로 남거나 그 반대 — 는 어느 경로로도 정리할 수 없다.
        """
        if cycle.close_reason not in (CloseReason.EMERGENCY, CloseReason.FORCED):
            raise ValueError(
                f"emergency_close_cycle requires close_reason EMERGENCY or "
                f"FORCED, got {cycle.close_reason} — 정상 종료는 save_stage 의 "
                f"가드를 통과해야 한다"
            )
        not_sold = [s.stage_no for s in stages
                    if s.status is not StageStatus.SOLD]
        if not_sold:
            raise StageInvariantError(
                f"emergency_close_cycle requires every stage to be SOLD; "
                f"stages {not_sold} are not"
            )
        expected = set(range(1, len(stages) + 1))
        if {s.stage_no for s in stages} != expected:
            raise StageInvariantError(
                f"emergency_close_cycle requires the complete stage set "
                f"1..{len(stages)}, got {sorted(s.stage_no for s in stages)}"
            )
        row = cycle_to_row(cycle)
        with self._conn:
            self._conn.execute(
                "UPDATE cycle SET status = :status, close_reason = :close_reason, "
                " closed_at = :closed_at, "
                " forced_close_reason = :forced_close_reason, "
                " forced_close_qty = :forced_close_qty "
                "WHERE id = :id",
                {**row, "id": cycle.cycle_id},
            )
            for stage in stages:
                stage_row = stage_to_row(cycle.cycle_id, stage)
                self._conn.execute(
                    "UPDATE stage_state SET status = :status, "
                    " fill_price = :fill_price, fill_qty = :fill_qty, "
                    " bought_at = :bought_at, last_sold_at = :last_sold_at, "
                    " rebuy_count = :rebuy_count "
                    "WHERE cycle_id = :cycle_id AND stage_no = :stage_no",
                    stage_row,
                )

    def set_realized_pnl(self, cycle_id: int, value: int) -> None:
        """사이클 종료 시 엔진이 `realized_pnl_for_cycle` 의 값을 기록한다.

        `cycle_to_row` 가 이 컬럼을 의도적으로 제외하므로(도메인 `Cycle` 에
        그 필드가 없다) 전용 경로가 필요하다 (2A 핸드오버 2).
        """
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE cycle SET realized_pnl = ? WHERE id = ?",
                (value, cycle_id),
            )
        if cursor.rowcount == 0:
            raise RowNotFound(f"no cycle with id {cycle_id}")
```

`RepositoryPort` 에 두 선언을 추가한다 (독스트링은 위 구현의 것을 요약해서 쓴다).

**주의:** `test_a_rejected_force_close_writes_nothing` 이 통과하려면 검사가 `with self._conn:` **앞에** 있어야 한다. 검사를 트랜잭션 안으로 넣으면 `stage_state` 업데이트 도중 예외가 나며 이미 실행된 `cycle` 업데이트가 롤백되긴 하지만, 검사를 먼저 하는 편이 의도가 명확하고 부분 실행 자체가 없다.

- [ ] **Step 6: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/domain tests/adapters -q`
Expected: PASS. `Cycle` 에 필드를 추가한 것이 기존 도메인 테스트를 깨지 않아야 한다 — 깨지면 기본값을 빼먹은 것이다.

- [ ] **Step 7: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add src/autotrading7s/domain/cycle.py src/autotrading7s/adapters/sqlite src/autotrading7s/ports/repository.py tests/domain/test_cycle_force_close.py tests/adapters/test_repository_force_close.py
git commit -m "$(printf 'feat: D20 강제 종료의 도메인과 원자적 쓰기 경로\n\n2A 핸드오버 1. 두 경로가 모두 막혀 있었다 — save_cycle 은 스키마 CHECK 가\n요구하는 필드가 Cycle 에 없어서, save_stage 는 force_sold 가 전이표를\n우회하는데 가드가 그 표를 참조해서.\n\nsave_stage 에 우회 플래그를 두지 않고 전용 포트 메서드를 뒀다. 플래그를 두면\n가드가 막고 있는 모든 것이 그 문으로 들어온다. D20 은 본질적으로 원자적이다 —\n절반만 강제 종료된 상태는 어느 경로로도 정리할 수 없다.\n\nforce_close 는 LIQUIDATING 에서만 호출된다. 사용자가 먼저 긴급청산을 시도해야\n하고 그 시도 이력이 다이얼로그의 근거가 된다(설계서 11.4절). 잔량 0 은 거부한다 —\n허용하면 정상 종료 경로의 보유 0 검사를 건너뛰는 수단이 된다.\n\n도메인 불변식이 스키마의 D20 CHECK 와 같은 것을 말한다. 두 층이 같은 것을\n말하면 어긋날 수 없다.\n\nset_realized_pnl 로 2A 핸드오버 2 를 해소했다.')"
```

---

## Task 7: 긴급청산 (설계서 11.1~11.3절)

**②를 빠뜨리면 긴급청산이 무력화된다.** 청산 시점에 하위 단계 매수 지정가 주문이 미체결로 살아 있을 수 있고, 전량 매도가 체결된 직후 그 매수가 체결되면 방금 다 팔았는데 다시 보유가 생긴다. 급락 중이라면 매수 체결 확률은 오히려 높다. *"판다"는 명령은 "더 이상 사지 않는다"를 포함해야 한다.*

**③에서 내부 기록이 아니라 실계좌를 신뢰한다.** 긴급청산이 불리는 상황은 바로 "시스템 오작동이 의심되는" 상황이다. 그 순간에 오작동했을지도 모르는 내부 기록을 근거로 수량을 정하는 것은 자기모순이다.

**Ruling: 이 모듈은 `engine/guards.py` 도 `domain/guards.py` 도 import 하지 않는다** (Plan 1 핸드오버 1). `max_orders_per_minute=0` 이 매도를 막게 되고, 그것은 손절 없는 전략의 유일한 탈출구에 레이트 리미터를 거는 것이다. 테스트가 import 부재를 고정한다.

**Ruling: 시장가 주문의 체결 확인은 한 번만 한다.** 부분체결로 남으면 `PARTIAL` 로 보고하고 사이클을 `LIQUIDATING` 에 남긴다 — 재시도인지 강제 종료인지는 사용자의 선택이다. 자동 재시도 루프는 급락 중에 무한히 팔려 들 수 있고, 긴급청산은 본질적으로 사용자 개입 경로다. 틀렸을 경우 비용: 유동성이 얕은 종목에서 사용자가 버튼을 두 번 눌러야 한다.

**Ruling: `Balance` 응답에 종목이 없으면 `FAILED` 다** (Plan 1 핸드오버 3). `Balance.qty_of` 는 0 을 반환하지만, "응답에 없음"은 "보유 0"의 증거가 아니다 — 응답이 잘렸거나 조회가 실패했을 수 있고, 그 상태에서 0주를 팔고 사이클을 닫으면 실계좌에 주식이 남은 채 프로그램이 손을 뗀다. 틀렸을 경우 비용: 정말 비어 있는 종목에 대해 사용자가 강제 종료를 거쳐야 한다 — 그쪽이 안전한 방향이다.

**Files:**
- Create: `src/autotrading7s/engine/emergency.py`
- Test: `tests/engine/test_emergency.py`

**Interfaces:**
- Produces:
  - `EmergencyOutcome` — `result: str`, `stock_code: str | None`, `qty_before: int | None`, `qty_after: int | None`, `canceled_orders: int`, `detail: str | None`
  - `EmergencyHandler(*, repo, broker, clock, emit)` — `.liquidate_single(config_id, *, reason) -> EmergencyOutcome`, `.liquidate_all(*, reason) -> list[EmergencyOutcome]` (둘 다 async)
  - `broker_qty(balance: Balance, code: str) -> int | None` — 응답에 없으면 `None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/engine/test_emergency.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import CycleClosed, EmergencyResult, Event
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.rules import BuyStage
from autotrading7s.domain.types import (
    Balance,
    CloseReason,
    CycleStatus,
    Holding,
    StageStatus,
    Tick,
    TickSource,
)
from autotrading7s.engine.emergency import EmergencyHandler, broker_qty
from autotrading7s.engine.executor import Executor

AT = datetime(2026, 9, 2, 14, 0, tzinfo=UTC)


def _make(repo, broker, *, market_open=True):
    clock = FakeClock(current=AT, market_open=market_open)
    events: list[Event] = []
    handler = EmergencyHandler(repo=repo, broker=broker, clock=clock,
                               emit=events.append)
    return handler, clock, events


def _liquidatable(repo):
    """005930 사이클을 그대로 반환한다 — 1단계가 10,000원 100주 보유 중이다."""
    return repo.load_active_cycles()[0]


# ── Balance 의 모호성 (Plan 1 핸드오버 3) ───────────────────────────────
def test_broker_qty_distinguishes_absent_from_zero():
    """`Balance.qty_of` 는 없는 종목에 0 을 반환한다.

    긴급청산은 두 상황을 구분해야 한다 — '응답에 없음'은 '보유 0'의 증거가
    아니다. 응답이 잘렸거나 조회가 실패했을 수 있고, 그 상태에서 사이클을
    닫으면 실계좌에 주식이 남은 채 프로그램이 손을 뗀다.
    """
    balance = Balance(cash=0, holdings=(Holding(code="000660", qty=0,
                                                avg_price=1),))
    assert balance.qty_of("005930") == 0        # 도메인의 산술용 답
    assert broker_qty(balance, "005930") is None
    assert broker_qty(balance, "000660") == 0


# ── 11.3절 장외 요청 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rejects_outside_market_hours(repo_two_stocks):
    """D16 — 시장가 주문은 장중에만 가능하다. 요청은 이력에 남는다."""
    cyc = _liquidatable(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True)
    handler, _, events = _make(repo_two_stocks, broker, market_open=False)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "REJECTED_CLOSED_MARKET"
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.RUNNING)
    row = repo_two_stocks._conn.execute(
        "SELECT result FROM emergency_liquidation_log"
    ).fetchone()
    assert dict(row)["result"] == "REJECTED_CLOSED_MARKET"
    assert [type(e) for e in events] == [EmergencyResult]


# ── 11.1절 ② 미체결 취소 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cancels_open_buy_orders_before_selling(repo_two_stocks):
    """②를 빠뜨리면 긴급청산이 무력화된다.

    전량 매도 직후 살아 있던 매수 지정가가 체결되면 방금 다 팔았는데 다시
    보유가 생긴다. 급락 중이라면 그 확률이 오히려 높다.
    """
    cyc = _liquidatable(repo_two_stocks)
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000,
                        holdings={"005930": (100, 1_000_000)})
    ex = Executor(repo=repo_two_stocks, broker=broker,
                  clock=FakeClock(current=AT), emit=lambda e: None)
    waiting = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.stage_no == 2)
    await ex.send(cycle=cyc, config=config, stage=waiting,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))
    assert len(repo_two_stocks.load_pending_orders()) == 1

    handler, _, events = _make(repo_two_stocks, broker)
    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.canceled_orders == 1
    assert repo_two_stocks.load_pending_orders() == []
    assert out.result == "SUCCESS"


# ── 11.1절 ③ 실계좌 수량 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sells_the_brokers_quantity_not_the_internal_one(repo_two_stocks):
    """내부 기록 100주, 실계좌 40주 → 40주를 팔아야 한다.

    내부 기록으로 팔면 브로커가 보유수량 부족으로 거부하고 청산이 실패한다.
    이 테스트가 그 차이를 강제한다 — validate_account 가 켜져 있으므로 잘못된
    수량은 조용히 통과하지 못한다.
    """
    cyc = _liquidatable(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (40, 400_000)})
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="오작동 의심")

    assert out.result == "SUCCESS"
    assert out.qty_before == 40
    assert out.qty_after == 0
    row = repo_two_stocks._conn.execute(
        "SELECT req_qty, order_type, path FROM order_log WHERE path = 'EMERGENCY'"
    ).fetchone()
    assert dict(row) == {"req_qty": 40, "order_type": "MARKET",
                         "path": "EMERGENCY"}


@pytest.mark.asyncio
async def test_absent_from_balance_is_a_failure(repo_two_stocks):
    cyc = _liquidatable(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True)   # 보유 없음
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "FAILED"
    assert "잔고" in out.detail
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)


@pytest.mark.asyncio
async def test_broker_zero_with_internal_holdings_is_a_failure(repo_two_stocks):
    """실계좌는 비었는데 내부 기록이 남은 경우 — 설계서 11.4절의 전제.

    팔 것이 없으므로 청산은 실패이고, 사이클을 LIQUIDATING 에 남겨 사용자가
    강제 종료를 선택할 수 있게 한다.
    """
    cyc = _liquidatable(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (0, 0)})
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "FAILED"
    assert out.qty_before == 0
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)


# ── 11.1절 ⑤⑥⑦ ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_success_closes_the_cycle_and_idles_the_config(repo_two_stocks):
    cyc = _liquidatable(repo_two_stocks)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="오작동 의심")

    assert out.result == "SUCCESS"
    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.status is CycleStatus.CLOSED
    assert reloaded.close_reason is CloseReason.EMERGENCY
    assert all(s.status is StageStatus.SOLD
               for s in repo_two_stocks.load_stages(cyc.cycle_id))
    assert repo_two_stocks.load_config(cyc.config_id).status == "IDLE"
    assert "005930" not in {h.stock_code for h in repo_two_stocks.holdings()}
    assert [type(e) for e in events] == [EmergencyResult, CycleClosed]


@pytest.mark.asyncio
async def test_success_records_realized_pnl(repo_two_stocks):
    """2A 핸드오버 2 — 종료 시 집계값을 기록하는 것은 엔진의 몫이다."""
    cyc = _liquidatable(repo_two_stocks)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _, _ = _make(repo_two_stocks, broker)
    await handler.liquidate_single(cyc.config_id, reason="테스트")

    row = repo_two_stocks._conn.execute(
        "SELECT realized_pnl FROM cycle WHERE id = ?", (cyc.cycle_id,)
    ).fetchone()
    # 픽스처는 매수를 order_log 없이 시드했으므로 매도만 집계된다:
    # 100주 × 10,500원 = 1,050,000. 값 자체가 아니라 '기록되었다'가 요점이다.
    assert dict(row)["realized_pnl"] == 1_050_000


@pytest.mark.asyncio
async def test_partial_market_fill_reports_partial_and_stays_liquidating(
    repo_two_stocks,
):
    """시장가가 부분체결로 남으면 자동 재시도하지 않는다.

    급락 중 자동 재시도 루프는 무한히 팔려 들 수 있다. 재시도인지 강제
    종료인지는 사용자의 선택이다.
    """
    from decimal import Decimal

    cyc = _liquidatable(repo_two_stocks)
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    # 시장가는 모드와 무관하게 즉시 전량 체결되므로, 부분체결을 만들려면
    # place_market_sell 뒤의 get_order 응답을 조정한다.
    original_get = broker.get_order

    async def partial(broker_order_id):
        status = await original_get(broker_order_id)
        from dataclasses import replace as dc_replace

        from autotrading7s.domain.types import FillState
        return dc_replace(status, state=FillState.PARTIAL, filled_qty=40)

    broker.get_order = partial              # type: ignore[method-assign]
    handler, _, events = _make(repo_two_stocks, broker)

    out = await handler.liquidate_single(cyc.config_id, reason="테스트")

    assert out.result == "PARTIAL"
    assert out.qty_after == 60
    assert (repo_two_stocks.load_cycle(cyc.cycle_id).status
            is CycleStatus.LIQUIDATING)
    assert repo_two_stocks.load_config(cyc.config_id).status != "IDLE"


# ── 11.1절 전체 청산 ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_liquidate_all_processes_stocks_sequentially(repo_two_stocks):
    """병렬 발주는 TR 호출 제한에 걸려 일부가 조용히 실패할 수 있다.

    순차 처리하면 각 종목의 결과가 개별 로그로 남고 중간에 실패해도 어디까지
    됐는지 명확하다.
    """
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"005930": (100, 1_000_000),
                                  "000660": (100, 600_000)})
    handler, _, events = _make(repo_two_stocks, broker)

    outcomes = await handler.liquidate_all(reason="전체 청산")

    assert [o.stock_code for o in outcomes] == ["005930", "000660"]
    assert all(o.result == "SUCCESS" for o in outcomes)
    rows = repo_two_stocks._conn.execute(
        "SELECT scope, stock_code FROM emergency_liquidation_log ORDER BY id"
    ).fetchall()
    assert [dict(r) for r in rows] == [
        {"scope": "ALL", "stock_code": "005930"},
        {"scope": "ALL", "stock_code": "000660"},
    ]


@pytest.mark.asyncio
async def test_liquidate_all_continues_after_one_failure(repo_two_stocks):
    """한 종목이 실패해도 나머지는 계속 청산한다."""
    broker = FakeBroker([10_500], validate_account=True,
                        holdings={"000660": (100, 600_000)})   # 005930 없음
    handler, _, events = _make(repo_two_stocks, broker)

    outcomes = await handler.liquidate_all(reason="전체 청산")

    assert [o.result for o in outcomes] == ["FAILED", "SUCCESS"]


# ── Plan 1 핸드오버 1 ───────────────────────────────────────────────────
def test_emergency_never_consults_guards():
    """긴급청산은 가드를 거치지 않는다.

    `max_orders_per_minute=0` 이 매도를 막게 되고, 그것은 손절 없는 전략의
    유일한 탈출구에 레이트 리미터를 거는 것이다. import 부재로 고정하는 이유:
    호출 부재는 미래의 수정으로 조용히 깨지지만, import 를 되살리려면 누군가
    이 테스트를 지워야 한다.
    """
    import ast
    import inspect

    from autotrading7s.engine import emergency as mod

    tree = ast.parse(inspect.getsource(mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert "autotrading7s.engine.guards" not in imported
    assert "autotrading7s.domain.guards" not in imported
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/engine/test_emergency.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.engine.emergency'`

- [ ] **Step 3: 구현한다**

`src/autotrading7s/engine/emergency.py`:

```python
"""긴급청산 — 설계서 11.1~11.3절.

**이 모듈은 가드를 거치지 않는다.** `engine/guards.py` 도 `domain/guards.py`
도 import 하지 않으며, 그 사실을 테스트가 고정한다. `max_orders_per_minute=0`
이 매도를 막게 되고, 그것은 손절 없는 전략의 유일한 탈출구에 레이트 리미터를
거는 것이다.

순서(설계서 11.1절):

    ① 대상 사이클 → LIQUIDATING  (자동 트리거 즉시 정지)
    ② 해당 종목 미체결 주문 전량 취소
    ③ get_balance() 로 실계좌 실제 보유수량 확인
    ④ MarketSellRequest(qty=실계좌수량, reason) 발주
    ⑤ 체결 확인 → 전 단계 SOLD 일괄 갱신
    ⑥ emergency_liquidation_log 기록
    ⑦ 사이클 CLOSED(EMERGENCY) → 설정 IDLE

**②를 빠뜨리면 긴급청산이 무력화된다.** 전량 매도 직후 살아 있던 매수 지정가가
체결되면 방금 다 팔았는데 다시 보유가 생긴다. "판다"는 명령은 "더 이상 사지
않는다"를 포함해야 한다.

**③에서 실계좌를 신뢰한다.** 긴급청산이 불리는 상황은 시스템 오작동이 의심되는
상황이다. 그 순간에 오작동했을지도 모르는 내부 기록으로 수량을 정하는 것은
자기모순이다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from autotrading7s.app.events import (
    EMERGENCY_RESULTS,
    CycleClosed,
    EmergencyResult,
    Event,
)
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import pnl
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import (
    Balance,
    CloseReason,
    FillState,
    MarketSellRequest,
    OrderPath,
    Side,
)
from autotrading7s.ports.broker import BrokerError, BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import RepositoryPort


def broker_qty(balance: Balance, code: str) -> int | None:
    """실계좌 보유수량. **응답에 그 종목이 없으면 `None`.**

    `Balance.qty_of` 는 없는 종목에 0 을 반환한다 — 평가금액 산술에는 맞는
    답이지만 긴급청산에는 아니다. '응답에 없음'은 '보유 0'의 증거가 아니고,
    그 상태에서 사이클을 닫으면 실계좌에 주식이 남은 채 프로그램이 손을 뗀다
    (Plan 1 핸드오버 3).
    """
    for holding in balance.holdings:
        if holding.code == code:
            return holding.qty
    return None


@dataclass(frozen=True, slots=True)
class EmergencyOutcome:
    result: str
    stock_code: str | None
    qty_before: int | None
    qty_after: int | None
    canceled_orders: int
    detail: str | None

    def __post_init__(self) -> None:
        if self.result not in EMERGENCY_RESULTS:
            raise ValueError(f"unknown emergency result: {self.result!r}")


class EmergencyHandler:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: Callable[[Event], None],
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    async def liquidate_all(self, *, reason: str | None) -> list[EmergencyOutcome]:
        """전체 종목 청산 — **종목별 순차 처리**.

        병렬로 발주하면 TR 호출 제한에 걸려 일부가 조용히 실패할 수 있다.
        순차 처리하면 각 종목의 결과가 개별 로그로 남고 중간에 실패해도
        어디까지 됐는지 명확하다.
        """
        outcomes: list[EmergencyOutcome] = []
        for cyc in self._repo.load_active_cycles():
            outcomes.append(
                await self.liquidate_single(cyc.config_id, reason=reason,
                                            scope="ALL")
            )
        return outcomes

    async def liquidate_single(
        self, config_id: int, *, reason: str | None, scope: str = "SINGLE",
    ) -> EmergencyOutcome:
        requested_at = self._clock.now()
        config = self._repo.load_config(config_id)
        code = config.stock_code

        if not self._clock.is_market_open(requested_at):
            # D16 — 예약 청산은 시스템이 타이밍을 정하는 쪽이고, 사용자가
            # 예약을 잊으면 의도치 않은 청산이 발생한다. 요청 자체는 남긴다.
            return self._finish(
                scope=scope, code=code, cycle_id=None,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "REJECTED_CLOSED_MARKET", code, None, None, 0,
                    f"장 운영시간이 아닙니다 ({requested_at.isoformat()})",
                ),
            )

        cycles = [c for c in self._repo.load_active_cycles()
                  if c.config_id == config_id]
        if not cycles:
            return self._finish(
                scope=scope, code=code, cycle_id=None,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome("FAILED", code, None, None, 0,
                                         "활성 사이클이 없습니다"),
            )
        cyc = cycles[0]

        # ① 자동 트리거 즉시 정지
        if cyc.status is not cycle_mod.CycleStatus.LIQUIDATING:
            cyc = cycle_mod.begin_liquidation(cyc)
            self._repo.save_cycle(cyc)

        # ② 미체결 주문 전량 취소
        canceled = await self._cancel_open_orders(cyc.cycle_id)

        # ③ 실계좌 수량 확인
        balance = await self._broker.get_balance()
        actual = broker_qty(balance, code)
        stages = self._repo.load_stages(cyc.cycle_id)
        internal = pnl.held_qty(stages)

        if actual is None:
            return self._finish(
                scope=scope, code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "FAILED", code, None, None, canceled,
                    f"잔고 응답에 {code} 가 없습니다 — 보유 0 으로 단정할 수 "
                    f"없어 청산을 중단합니다 (내부 기록 {internal}주)",
                ),
            )
        if actual == 0:
            if internal > 0:
                return self._finish(
                    scope=scope, code=code, cycle_id=cyc.cycle_id,
                    requested_at=requested_at, reason=reason,
                    outcome=EmergencyOutcome(
                        "FAILED", code, 0, 0, canceled,
                        f"실계좌 보유 0 이지만 내부 기록 {internal}주 — "
                        f"강제 종료가 필요합니다 (설계서 11.4절)",
                    ),
                )
            return self._close(
                cyc=cyc, config=config, stages=stages, scope=scope,
                requested_at=requested_at, reason=reason, qty_before=0,
                sold=0, canceled=canceled,
            )

        # ④ 시장가 매도 — 기록이 발주보다 먼저 온다 (설계서 9절과 같은 논리)
        client_ref = uuid.uuid4()
        self._repo.append_order_log(
            client_ref=str(client_ref), cycle_id=cyc.cycle_id,
            stage_state_id=None, side=Side.SELL, order_type="MARKET",
            path=OrderPath.EMERGENCY, req_price=None, req_qty=actual,
            trigger_reason=reason or "긴급청산", tick_price=None,
            tick_source=None, sent_at=self._clock.now(),
        )
        req = MarketSellRequest(code=code, qty=actual, client_ref=client_ref,
                                reason=reason or "긴급청산")
        try:
            ack = await self._broker.place_market_sell(req)
        except BrokerError as exc:
            self._repo.update_order_log(
                client_ref=str(client_ref), status="REJECTED",
                api_message=str(exc), settled_at=self._clock.now(),
            )
            return self._finish(
                scope=scope, code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome("FAILED", code, actual, actual,
                                         canceled, f"시장가 매도 실패: {exc}"),
            )

        # ⑤ 체결 확인 — 한 번만 본다
        status = await self._broker.get_order(ack.broker_order_id)
        terminal = "FILLED" if status.state is FillState.FILLED else "PARTIAL"
        self._repo.update_order_log(
            client_ref=str(client_ref), status=terminal,
            broker_order_id=ack.broker_order_id,
            fill_price=status.filled_price, fill_qty=status.filled_qty,
            settled_at=self._clock.now() if terminal == "FILLED" else None,
        )
        if status.filled_qty < actual:
            return self._finish(
                scope=scope, code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "PARTIAL", code, actual, actual - status.filled_qty,
                    canceled,
                    f"{actual}주 중 {status.filled_qty}주 체결 — 재시도 또는 "
                    f"강제 종료를 선택하세요",
                ),
            )
        return self._close(
            cyc=cyc, config=config, stages=stages, scope=scope,
            requested_at=requested_at, reason=reason, qty_before=actual,
            sold=status.filled_qty, canceled=canceled,
        )

    async def _cancel_open_orders(self, cycle_id: int) -> int:
        """②. 취소 실패는 세지 않고 로그에 남긴다 — 살아 있는 주문이 있다는
        사실이 이후 대사에서 드러난다."""
        canceled = 0
        for row in self._repo.load_pending_orders():
            if row.cycle_id != cycle_id or row.broker_order_id is None:
                continue
            try:
                await self._broker.cancel_order(row.broker_order_id)
            except BrokerError:
                continue
            self._repo.update_order_log(
                client_ref=row.client_ref, status="CANCELED",
                api_message="긴급청산으로 취소 (설계서 11.1절 ②)",
                settled_at=self._clock.now(),
            )
            canceled += 1
        return canceled

    def _close(
        self, *, cyc, config, stages, scope, requested_at, reason,
        qty_before: int, sold: int, canceled: int,
    ) -> EmergencyOutcome:
        """⑤⑦ — 전 단계를 SOLD 로 일괄 갱신하고 사이클을 닫는다.

        `emergency_close_cycle` 을 쓰는 이유: `force_sold` 는 전이표를
        우회하는데 `save_stage` 의 가드는 그 표를 참조한다. 사이클과 단계가
        한 트랜잭션에 써져야 절반만 청산된 상태가 남지 않는다.
        """
        at = self._clock.now()
        sold_stages = [stage_mod.force_sold(s, at=at) for s in stages]
        closed = cycle_mod.close(cyc, reason=CloseReason.EMERGENCY, at=at,
                                 states=sold_stages)
        self._repo.emergency_close_cycle(cycle=closed, stages=sold_stages)
        self._repo.set_realized_pnl(
            cyc.cycle_id, self._repo.realized_pnl_for_cycle(cyc.cycle_id)
        )
        self._repo.set_config_status(config.config_id, "IDLE", at=at)
        outcome = EmergencyOutcome("SUCCESS", config.stock_code, qty_before,
                                   qty_before - sold, canceled, None)
        result = self._finish(scope=scope, code=config.stock_code,
                              cycle_id=cyc.cycle_id,
                              requested_at=requested_at, reason=reason,
                              outcome=outcome)
        self._emit(CycleClosed(
            config_id=config.config_id, cycle_id=cyc.cycle_id,
            reason=CloseReason.EMERGENCY,
            realized_pnl=self._repo.realized_pnl_for_cycle(cyc.cycle_id),
            at=at,
        ))
        return result

    def _finish(
        self, *, scope: str, code: str | None, cycle_id: int | None,
        requested_at, reason: str | None, outcome: EmergencyOutcome,
    ) -> EmergencyOutcome:
        """⑥ — 모든 경로가 이력을 남기고 이벤트를 낸다.

        거부와 실패도 남긴다. 긴급청산은 사용자가 개입한 사건이므로 결과와
        무관하게 이력에 있어야 한다 (설계서 11.2절 전용 이력 로그).
        """
        completed_at = self._clock.now()
        self._repo.append_emergency_log(
            scope=scope, stock_code=code, cycle_id=cycle_id,
            requested_at=requested_at, reason=reason,
            qty_before=outcome.qty_before, qty_after=outcome.qty_after,
            canceled_orders=outcome.canceled_orders, result=outcome.result,
            detail_json=None if outcome.detail is None
                        else json.dumps({"detail": outcome.detail},
                                        ensure_ascii=False),
            completed_at=completed_at,
        )
        self._emit(EmergencyResult(
            scope=scope, stock_code=code, result=outcome.result,
            qty_before=outcome.qty_before, qty_after=outcome.qty_after,
            canceled_orders=outcome.canceled_orders, detail=outcome.detail,
            at=completed_at,
        ))
        return outcome
```

`cycle_mod.CycleStatus` 는 존재하지 않는다 — `from autotrading7s.domain.types import CycleStatus` 를 추가하고 `CycleStatus.LIQUIDATING` 으로 고친다.

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/engine/test_emergency.py -q`
Expected: PASS

- [ ] **Step 5: 전체 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/engine/emergency.py tests/engine/test_emergency.py
git commit -m "$(printf 'feat: 긴급청산 — 설계서 11.1~11.3절\n\n②(미체결 취소)를 먼저 한다. 전량 매도 직후 살아 있던 매수 지정가가 체결되면\n방금 다 팔았는데 다시 보유가 생기고, 급락 중이라면 그 확률이 오히려 높다.\n"판다"는 명령은 "더 이상 사지 않는다"를 포함해야 한다.\n\n③에서 실계좌 수량으로 판다. 긴급청산이 불리는 상황은 시스템 오작동이 의심되는\n상황이고, 그 순간에 오작동했을지도 모르는 내부 기록으로 수량을 정하는 것은\n자기모순이다. 내부 100주 / 실계좌 40주 시나리오를 브로커 검증을 켠 채 돌려\n잘못된 수량이 조용히 통과하지 못하게 했다.\n\nbroker_qty 가 "응답에 없음"과 "보유 0"을 구분한다(Plan 1 핸드오버 3). 없으면\nFAILED 다 — 보유 0 으로 단정하고 닫으면 실계좌에 주식이 남은 채 프로그램이\n손을 뗀다.\n\n가드를 import 하지 않는다. max_orders_per_minute=0 이 손절 없는 전략의 유일한\n탈출구를 막는다. import 부재를 테스트로 고정했다 — 호출 부재는 조용히 깨지지만\nimport 를 되살리려면 누군가 그 테스트를 지워야 한다.\n\n부분체결은 자동 재시도하지 않는다. 급락 중 재시도 루프는 무한히 팔려 들 수 있고,\n재시도인지 강제 종료인지는 사용자의 선택이다.')"
```

---

## Task 8: D20 강제 종료 핸들러 (설계서 11.4절)

**Files:**
- Modify: `src/autotrading7s/engine/emergency.py`
- Test: `tests/engine/test_force_close.py`

**Interfaces:**
- Produces: `EmergencyHandler.force_close(config_id: int, *, reason: str) -> EmergencyOutcome` (async)

**절차 (설계서 11.4절):**

```
① 사용자 → 강제 종료 요청(사유 + 텍스트 확인)   ← ForceClose 명령이 이미 검증했다
② 실계좌 잔고를 다시 조회해 남은 수량을 확정      ← 11.1절 ③과 같은 이유
③ 남은 수량이 0이면 강제 종료가 아니라 정상 close() 로 처리(사용자에게 알림)
④ 미체결 주문이 있으면 전량 취소                  ← 11.1절 ②과 같은 이유
⑤ 사이클 → CLOSED(FORCED), forced_close_reason·forced_close_qty
⑥ 전 단계를 SOLD 로 일괄 갱신하되, 단계별 잔량을 이력에 남긴다
⑦ emergency_liquidation_log 에 result=FORCED_CLOSE
⑧ split_config.status = IDLE
```

**Ruling: ③의 "남은 수량 0" 은 `broker_qty` 가 0 을 반환한 경우만이다.** 응답에 종목이 없으면(`None`) 강제 종료도 거부한다 — 잔량을 모르는 채로 증언을 기록하면 그 증언이 근거 없는 숫자를 담는다. 틀렸을 경우 비용: 조회가 계속 실패하는 상황에서 사용자가 막힌다 — 그러나 그때 필요한 것은 종료가 아니라 연결 복구다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/engine/test_force_close.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import EmergencyResult, Event
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus
from autotrading7s.engine.emergency import EmergencyHandler

AT = datetime(2026, 9, 2, 15, 28, tzinfo=UTC)
STATEMENT = "거래정지로 청산 불가, 잔량 100주는 직접 처리 예정"


def _handler(repo, broker, *, market_open=True):
    events: list[Event] = []
    return EmergencyHandler(repo=repo, broker=broker,
                            clock=FakeClock(current=AT,
                                            market_open=market_open),
                            emit=events.append), events


def _liquidating(repo):
    cyc = repo.load_active_cycles()[0]
    liq = cycle_mod.begin_liquidation(cyc)
    repo.save_cycle(liq)
    return liq


@pytest.mark.asyncio
async def test_force_close_requires_liquidating(repo_two_stocks):
    """설계서 11.4절 — 사용자가 먼저 긴급청산을 시도해야 한다.

    RUNNING 에서 바로 강제 종료할 수 있으면, 그 시도 이력(횟수·시각·실패
    사유)이라는 다이얼로그의 근거 없이 내부 기록과 실계좌를 어긋나게 만들 수
    있다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    assert cyc.status is CycleStatus.RUNNING
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, events = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FAILED"
    assert "LIQUIDATING" in out.detail
    assert repo_two_stocks.load_cycle(cyc.cycle_id).status is CycleStatus.RUNNING


@pytest.mark.asyncio
async def test_force_close_records_statement_and_remainder(repo_two_stocks):
    """⑤⑦⑧ — 증언과 잔량이 영구 기록되고 설정이 IDLE 로 돌아간다."""
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, events = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FORCED_CLOSE"
    assert out.qty_after == 100
    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.status is CycleStatus.CLOSED
    assert reloaded.close_reason is CloseReason.FORCED
    assert reloaded.forced_close_qty == 100
    assert reloaded.forced_close_reason == STATEMENT
    assert repo_two_stocks.load_config(cyc.config_id).status == "IDLE"
    row = repo_two_stocks._conn.execute(
        "SELECT result, reason, qty_after FROM emergency_liquidation_log"
    ).fetchone()
    assert dict(row) == {"result": "FORCED_CLOSE", "reason": STATEMENT,
                         "qty_after": 100}


@pytest.mark.asyncio
async def test_force_close_keeps_per_stage_remainders_in_the_log(repo_two_stocks):
    """⑥ — 전 단계를 SOLD 로 갱신하되 단계별 잔량을 이력에 남긴다.

    단계 상태를 SOLD 로 덮으면 그 정보가 사라진다. 사용자가 나중에 "어느
    단계에 얼마가 남았는지" 를 물을 수 있는 유일한 곳이 이 로그다.
    """
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _ = _handler(repo_two_stocks, broker)

    await handler.force_close(cyc.config_id, reason=STATEMENT)

    row = repo_two_stocks._conn.execute(
        "SELECT detail_json FROM emergency_liquidation_log"
    ).fetchone()
    detail = json.loads(dict(row)["detail_json"])
    assert detail["stage_remainders"] == {"1": 100}
    assert detail["broker_qty"] == 100


@pytest.mark.asyncio
async def test_zero_remainder_takes_the_normal_close_path(repo_two_stocks):
    """③ — 잔량 0 의 강제 종료는 의미가 없다.

    허용하면 정상 종료 경로의 보유 0 검사를 건너뛰는 수단이 된다. 실계좌가
    0 이면 사용자가 이미 직접 팔았다는 뜻이므로, 프로그램 관리 밖에 남는
    주식이 없다 — FORCED 가 아니라 EMERGENCY 종료다.
    """
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (0, 0)})
    handler, events = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "SUCCESS"
    reloaded = repo_two_stocks.load_cycle(cyc.cycle_id)
    assert reloaded.close_reason is CloseReason.EMERGENCY
    assert reloaded.forced_close_qty is None
    assert "정상 종료" in out.detail


@pytest.mark.asyncio
async def test_absent_from_balance_blocks_force_close_too(repo_two_stocks):
    """잔량을 모르는 채로 증언을 기록하면 그 증언이 근거 없는 숫자를 담는다."""
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True)
    handler, _ = _handler(repo_two_stocks, broker)

    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FAILED"
    assert repo_two_stocks.load_cycle(cyc.cycle_id).status is CycleStatus.LIQUIDATING


@pytest.mark.asyncio
async def test_force_close_cancels_open_orders(repo_two_stocks):
    """④ — 11.1절 ②와 같은 이유. 남은 매수 주문이 체결되면 관리 밖 주식이 늘어난다."""
    from autotrading7s.domain.rules import BuyStage
    from autotrading7s.domain.types import Tick, TickSource
    from autotrading7s.engine.executor import Executor

    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000,
                        holdings={"005930": (100, 1_000_000)})
    ex = Executor(repo=repo_two_stocks, broker=broker,
                  clock=FakeClock(current=AT), emit=lambda e: None)
    waiting = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.stage_no == 2)
    await ex.send(cycle=cyc, config=config, stage=waiting,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))
    liq = cycle_mod.begin_liquidation(repo_two_stocks.load_cycle(cyc.cycle_id))
    repo_two_stocks.save_cycle(liq)

    handler, _ = _handler(repo_two_stocks, broker)
    out = await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert out.result == "FORCED_CLOSE"
    assert out.canceled_orders == 1
    assert repo_two_stocks.load_pending_orders() == []


@pytest.mark.asyncio
async def test_forced_stock_disappears_from_holdings(repo_two_stocks):
    """설계서 11.4절 — 강제 종료 후 그 종목은 프로그램의 관리 밖이다."""
    cyc = _liquidating(repo_two_stocks)
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    handler, _ = _handler(repo_two_stocks, broker)

    await handler.force_close(cyc.config_id, reason=STATEMENT)

    assert "005930" not in {h.stock_code for h in repo_two_stocks.holdings()}
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/engine/test_force_close.py -q`
Expected: FAIL — `AttributeError: 'EmergencyHandler' object has no attribute 'force_close'`

- [ ] **Step 3: 구현한다**

`EmergencyHandler` 에 추가한다.

```python
    async def force_close(self, config_id: int, *, reason: str) -> EmergencyOutcome:
        """D20 강제 종료 — 설계서 11.4절.

        긴급청산이 끝까지 가지 못하는 상황(거래정지, 유동성 부재, 사용자가
        증권사 앱에서 직접 매도)에 사용자가 증언과 함께 호출한다. 설계서
        10.2절이 금지하는 것과 구분된다 — 10.2절이 금지하는 것은 **프로그램이**
        불일치를 조용히 만드는 것이고, 이것은 사용자의 의도적 개입이다.

        `LIQUIDATING` 에서만 호출된다. 사용자가 먼저 긴급청산을 시도해야 하고,
        그 시도 이력이 다이얼로그의 근거가 된다.
        """
        requested_at = self._clock.now()
        config = self._repo.load_config(config_id)
        code = config.stock_code
        cycles = [c for c in self._repo.load_active_cycles()
                  if c.config_id == config_id]
        if not cycles or cycles[0].status is not CycleStatus.LIQUIDATING:
            status = cycles[0].status.value if cycles else "없음"
            return self._finish(
                scope="SINGLE", code=code,
                cycle_id=cycles[0].cycle_id if cycles else None,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "FAILED", code, None, None, 0,
                    f"강제 종료는 LIQUIDATING 에서만 가능합니다 (현재 {status}) "
                    f"— 긴급청산을 먼저 시도하세요 (설계서 11.4절)",
                ),
            )
        cyc = cycles[0]

        # ② 실계좌 잔고 재조회
        balance = await self._broker.get_balance()
        actual = broker_qty(balance, code)
        stages = self._repo.load_stages(cyc.cycle_id)
        if actual is None:
            return self._finish(
                scope="SINGLE", code=code, cycle_id=cyc.cycle_id,
                requested_at=requested_at, reason=reason,
                outcome=EmergencyOutcome(
                    "FAILED", code, None, None, 0,
                    f"잔고 응답에 {code} 가 없습니다 — 잔량을 모르는 채로 "
                    f"증언을 기록할 수 없습니다",
                ),
            )

        # ④ 미체결 취소 (③보다 먼저 해도 무해하고, 0 잔량 경로에서도 필요하다)
        canceled = await self._cancel_open_orders(cyc.cycle_id)

        # ③ 잔량 0 → 정상 종료 경로
        if actual == 0:
            outcome = self._close(
                cyc=cyc, config=config, stages=stages, scope="SINGLE",
                requested_at=requested_at, reason=reason, qty_before=0,
                sold=0, canceled=canceled,
            )
            return EmergencyOutcome(
                outcome.result, code, 0, 0, canceled,
                "실계좌 잔량이 0 이므로 강제 종료가 아니라 정상 종료로 "
                "처리했습니다 (설계서 11.4절 절차 ③)",
            )

        # ⑤⑥ 사이클과 단계를 한 트랜잭션에
        at = self._clock.now()
        remainders = {str(s.stage_no): s.held_qty for s in stages
                      if s.held_qty > 0}
        sold_stages = [stage_mod.force_sold(s, at=at) for s in stages]
        closed = cycle_mod.force_close(cyc, reason=reason, qty=actual, at=at)
        self._repo.emergency_close_cycle(cycle=closed, stages=sold_stages)
        self._repo.set_realized_pnl(
            cyc.cycle_id, self._repo.realized_pnl_for_cycle(cyc.cycle_id)
        )
        # ⑧
        self._repo.set_config_status(config_id, "IDLE", at=at)

        # ⑦ — 단계별 잔량은 상태를 SOLD 로 덮으면 사라지므로 여기에만 남는다
        completed_at = self._clock.now()
        self._repo.append_emergency_log(
            scope="SINGLE", stock_code=code, cycle_id=cyc.cycle_id,
            requested_at=requested_at, reason=reason, qty_before=actual,
            qty_after=actual, canceled_orders=canceled,
            result="FORCED_CLOSE",
            detail_json=json.dumps(
                {"stage_remainders": remainders, "broker_qty": actual},
                ensure_ascii=False,
            ),
            completed_at=completed_at,
        )
        outcome = EmergencyOutcome("FORCED_CLOSE", code, actual, actual,
                                   canceled, None)
        self._emit(EmergencyResult(
            scope="SINGLE", stock_code=code, result="FORCED_CLOSE",
            qty_before=actual, qty_after=actual, canceled_orders=canceled,
            detail=None, at=completed_at,
        ))
        self._emit(CycleClosed(
            config_id=config_id, cycle_id=cyc.cycle_id,
            reason=CloseReason.FORCED,
            realized_pnl=self._repo.realized_pnl_for_cycle(cyc.cycle_id),
            at=at,
        ))
        return outcome
```

**`qty_after` 가 `actual` 인 것이 의도다.** 강제 종료는 아무것도 팔지 않으므로 종료 후에도 그 수량이 실계좌에 남아 있다. `qty_after=0` 으로 기록하면 이력이 "다 팔았다"고 말하게 되고, 그것이 설계서 11.4절이 방지하려는 바로 그 거짓이다.

- [ ] **Step 4~6: 테스트·회귀·커밋**

Run: `.venv/bin/python -m pytest tests/engine/test_force_close.py -q` → PASS
Run: `.venv/bin/python -m pytest -q` → PASS

```bash
git add src/autotrading7s/engine/emergency.py tests/engine/test_force_close.py
git commit -m "$(printf 'feat: D20 강제 종료 핸들러 — 설계서 11.4절\n\nLIQUIDATING 에서만 호출된다. 사용자가 먼저 긴급청산을 시도해야 하고 그 시도\n이력이 다이얼로그의 근거가 된다.\n\n잔량 0 은 강제 종료가 아니라 정상 종료로 처리한다(절차 ③). 허용하면 정상 종료\n경로의 보유 0 검사를 건너뛰는 수단이 된다. 실계좌가 0 이면 관리 밖에 남는\n주식이 없으므로 FORCED 가 아니다.\n\n잔고 응답에 종목이 없으면 강제 종료도 거부한다 — 잔량을 모르는 채로 증언을\n기록하면 그 증언이 근거 없는 숫자를 담는다.\n\nqty_after 를 잔량 그대로 기록한다. 강제 종료는 아무것도 팔지 않으므로 0 으로\n기록하면 이력이 "다 팔았다"고 말하게 되고, 그것이 11.4절이 방지하려는 거짓이다.\n\n단계별 잔량을 detail_json 에 남긴다. 단계 상태를 SOLD 로 덮으면 그 정보가\n사라지고, 사용자가 "어느 단계에 얼마가 남았는지" 를 물을 곳이 없어진다.')"
```

---

## Task 9: 잔고 대사 (설계서 10.2절)

**자동 보정은 하지 않는다 (D13).** 내부 기록이 실계좌보다 많으면 매도 주문이 계속 거부되어 `SELL_PENDING` 에서 무한 재시도에 빠진다. 그래서 멈추는 것이 안전하다. 반대로 프로그램이 내부 상태를 실계좌에 맞춰 고치면 단계별 체결가 정보가 조용히 조작되어 이후 모든 목표가 계산이 근거를 잃는다. **불일치는 사람이 확인해야 하는 사건이다.**

**Ruling: 강제 종료 기준선을 `reconcile_log` 로 표현한다.** 설계서 11.4절은 `forced_close_qty` 를 종목별로 누적해 대사 기준선으로 삼고 사용자가 초기화할 수단을 두라고 요구한다. 초기화 시점을 담을 컬럼이 없고 스키마는 버전 1을 넘는 마이그레이션 경로가 없으므로(2A 핸드오버 5), 초기화를 `reconcile_log` 에 `action_taken='BASELINE_RESET'` 로 기록하고 기준선을 **"마지막 초기화 이후에 강제 종료된 수량의 합"** 으로 계산한다. 틀렸을 경우 비용: 기준선 계산이 두 테이블을 보게 되어 조금 복잡하다 — `ALTER TABLE` 단계를 새로 만드는 것보다 싸다.

**Files:**
- Create: `src/autotrading7s/engine/reconciler.py`
- Modify: `src/autotrading7s/ports/repository.py`, `src/autotrading7s/adapters/sqlite/repository.py`
- Test: `tests/engine/test_reconciler.py`

**Interfaces:**
- Produces:
  - `RepositoryPort.forced_close_baseline(stock_code: str) -> int`
  - `RepositoryPort.reset_forced_close_baseline(stock_code: str, *, at: datetime) -> None`
  - `engine.reconciler.ReconcileReport` — `stock_code: str`, `internal_qty: int`, `broker_qty: int`, `baseline: int`, `verdict: str`, `action_taken: str | None`
  - `Reconciler(*, repo, broker, clock, emit)` — `.run_once() -> list[ReconcileReport]` (async), `.reset_baseline(stock_code) -> None`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/engine/test_reconciler.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import Event, ReconcileMismatch
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import CycleStatus
from autotrading7s.engine.reconciler import Reconciler

AT = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)


def _rec(repo, broker):
    events: list[Event] = []
    return Reconciler(repo=repo, broker=broker,
                      clock=FakeClock(current=AT), emit=events.append), events


@pytest.mark.asyncio
async def test_match_writes_no_event(repo_two_stocks):
    """일치하면 로그도 이벤트도 없다 (설계서 10.2절 표)."""
    broker = FakeBroker([10_000], holdings={"005930": (100, 1_000_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()

    assert {r.verdict for r in reports} == {"MATCH"}
    assert events == []
    assert repo_two_stocks._conn.execute(
        "SELECT count(*) c FROM reconcile_log"
    ).fetchone()["c"] == 0


@pytest.mark.asyncio
async def test_internal_less_warns_but_keeps_trading(repo_two_stocks):
    """내부 < 실계좌 — 외부에서 수동 매수한 듯. 경고만 하고 계속 돈다."""
    broker = FakeBroker([10_000], holdings={"005930": (150, 1_500_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()
    samsung = next(r for r in reports if r.stock_code == "005930")

    assert samsung.verdict == "INTERNAL_LESS"
    assert samsung.action_taken is None
    assert repo_two_stocks.load_active_cycles()[0].status is CycleStatus.RUNNING
    assert [type(e) for e in events] == [ReconcileMismatch]


@pytest.mark.asyncio
async def test_internal_more_pauses_that_stock(repo_two_stocks):
    """내부 > 실계좌 — 해당 종목 즉시 PAUSED.

    자동 보정하지 않는 이유(D13): 내부가 많으면 매도가 계속 거부되어
    SELL_PENDING 무한 재시도에 빠진다. 반대로 내부를 실계좌에 맞춰 고치면
    단계별 체결가가 조용히 조작되고 이후 모든 목표가 계산이 근거를 잃는다.
    """
    broker = FakeBroker([10_000], holdings={"005930": (40, 400_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()
    samsung = next(r for r in reports if r.stock_code == "005930")

    assert samsung.verdict == "INTERNAL_MORE"
    assert samsung.action_taken == "PAUSED"
    cyc = next(c for c in repo_two_stocks.load_active_cycles()
               if c.config_id == 1)
    assert cyc.status is CycleStatus.PAUSED
    # 설정은 ACTIVE 로 남는다 — 일시정지는 사이클의 상태다 (원장 Ruling 1)
    assert repo_two_stocks.load_config(1).status == "ACTIVE"
    # 다른 종목은 영향받지 않는다 — 종목별 대응이다
    other = next(c for c in repo_two_stocks.load_active_cycles()
                 if c.config_id == 2)
    assert other.status is CycleStatus.RUNNING


@pytest.mark.asyncio
async def test_reconciler_never_writes_stage_state(repo_two_stocks):
    """자동 보정 금지를 코드에서 확인한다 (D13).

    호출 부재가 아니라 참조 부재로 고정한다 — 대사가 단계를 쓸 수 있게 되면
    그것이 D13 이 금지한 바로 그 조작이다.
    """
    import inspect

    from autotrading7s.engine import reconciler as mod

    source = inspect.getsource(mod)
    assert "save_stage" not in source
    assert "emergency_close_cycle" not in source


# ── 강제 종료 기준선 (설계서 11.4절) ────────────────────────────────────
@pytest.mark.asyncio
async def test_forced_quantity_is_excluded_from_reconciliation(repo_two_stocks):
    """강제 종료된 수량은 대사 기준에서 빠진다.

    빼지 않으면 강제 종료 직후 매 5분마다 영구적으로 INTERNAL_LESS 경고가
    나고, 사용자는 그 경고를 무시하는 습관을 들인다 — 그러면 진짜 불일치도
    무시된다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    liq = cycle_mod.begin_liquidation(cyc)
    repo_two_stocks.save_cycle(liq)
    stages = [stage_mod.force_sold(s, at=AT)
              for s in repo_two_stocks.load_stages(cyc.cycle_id)]
    repo_two_stocks.emergency_close_cycle(
        cycle=cycle_mod.force_close(liq, reason="거래정지", qty=100, at=AT),
        stages=stages,
    )
    # 실계좌에는 강제 종료된 100주가 그대로 남아 있다
    broker = FakeBroker([10_000], holdings={"005930": (100, 1_000_000),
                                            "000660": (100, 600_000)})
    rec, events = _rec(repo_two_stocks, broker)

    reports = await rec.run_once()

    assert "005930" not in {r.stock_code for r in reports if r.verdict != "MATCH"}
    assert events == []


@pytest.mark.asyncio
async def test_baseline_reset_makes_the_difference_visible_again(repo_two_stocks):
    """설계서 11.4절 — 대사 제외는 영구적이지 않다.

    사용자가 그 주식을 처리한 뒤 기준선을 초기화하면 이후의 차이는 다시
    불일치로 보고돼야 한다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    liq = cycle_mod.begin_liquidation(cyc)
    repo_two_stocks.save_cycle(liq)
    stages = [stage_mod.force_sold(s, at=AT)
              for s in repo_two_stocks.load_stages(cyc.cycle_id)]
    repo_two_stocks.emergency_close_cycle(
        cycle=cycle_mod.force_close(liq, reason="거래정지", qty=100, at=AT),
        stages=stages,
    )
    assert repo_two_stocks.forced_close_baseline("005930") == 100

    repo_two_stocks.reset_forced_close_baseline("005930", at=AT)

    assert repo_two_stocks.forced_close_baseline("005930") == 0
    row = repo_two_stocks._conn.execute(
        "SELECT action_taken, verdict FROM reconcile_log"
    ).fetchone()
    assert dict(row)["action_taken"] == "BASELINE_RESET"


def test_baseline_is_zero_for_a_stock_never_force_closed(repo_two_stocks):
    assert repo_two_stocks.forced_close_baseline("000660") == 0
    assert repo_two_stocks.forced_close_baseline("035720") == 0
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

Run: `.venv/bin/python -m pytest tests/engine/test_reconciler.py -q` → FAIL, 구현 후 PASS.

`SqliteRepository` 에 두 메서드를 추가한다.

```python
    def forced_close_baseline(self, stock_code: str) -> int:
        """이 종목에서 강제 종료된 누적 수량 — 마지막 기준선 초기화 이후만.

        설계서 11.4절: 강제 종료된 수량을 대사 기준에서 제외해야 하고, 그
        제외는 영구적이지 않아야 한다. 초기화 시점을 담을 컬럼이 없고 스키마는
        버전 1을 넘는 마이그레이션 경로가 없으므로(2A 핸드오버 5), 초기화를
        `reconcile_log` 의 `action_taken='BASELINE_RESET'` 행으로 표현한다.
        """
        row = self._conn.execute(
            "SELECT COALESCE(SUM(c.forced_close_qty), 0) AS total "
            "FROM cycle c JOIN split_config s ON s.id = c.config_id "
            "WHERE s.stock_code = ? AND c.close_reason = 'FORCED' "
            "  AND c.closed_at > COALESCE(("
            "     SELECT MAX(checked_at) FROM reconcile_log "
            "     WHERE stock_code = ? AND action_taken = 'BASELINE_RESET'"
            "  ), '')",
            (stock_code, stock_code),
        ).fetchone()
        return int(dict(row)["total"])

    def reset_forced_close_baseline(
        self, stock_code: str, *, at: datetime
    ) -> None:
        """사용자가 남은 주식을 처리했다고 알린 시점을 기록한다."""
        self.append_reconcile_log(
            checked_at=at, stock_code=stock_code, internal_qty=0,
            broker_qty=0, verdict="MATCH", action_taken="BASELINE_RESET",
        )
```

**`closed_at > ''` 비교가 성립하는 이유:** `closed_at` 은 ISO-8601 TEXT 이고 SQLite 의 문자열 비교는 사전순이므로, 초기화가 없을 때의 `''` 는 모든 시각보다 작다. 시각 비교를 문자열로 하는 것은 ISO-8601 이 사전순 = 시간순인 덕분이며, 2A 의 코덱이 그 형식을 보장한다.

`engine/reconciler.py`:

```python
"""잔고 대사 — 설계서 10.2절.

**자동 보정은 하지 않는다 (D13).** 내부 기록이 실계좌보다 많으면 매도 주문이
계속 거부되어 `SELL_PENDING` 무한 재시도에 빠진다. 그래서 멈추는 것이 안전하다.
반대로 프로그램이 내부 상태를 실계좌에 맞춰 고치면 단계별 체결가 정보가 조용히
조작되어 이후 모든 목표가 계산이 근거를 잃는다. 불일치는 **사람이 확인해야 하는
사건**이다.

이 모듈은 `save_stage` 를 부르지 않으며, 그 사실을 테스트가 참조 부재로
고정한다.

강제 종료된 수량은 기준에서 제외한다 (설계서 11.4절). 빼지 않으면 강제 종료
직후 매 5분마다 영구적으로 경고가 나고, 사용자가 그 경고를 무시하는 습관을
들이면 진짜 불일치도 무시된다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autotrading7s.app.events import Event, ReconcileMismatch
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import pnl
from autotrading7s.domain.types import CycleStatus
from autotrading7s.engine.emergency import broker_qty
from autotrading7s.ports.broker import BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import CorruptRowError, RepositoryPort


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    stock_code: str
    internal_qty: int
    broker_qty: int
    baseline: int
    verdict: str
    action_taken: str | None


class Reconciler:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: Callable[[Event], None],
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    def reset_baseline(self, stock_code: str) -> None:
        self._repo.reset_forced_close_baseline(stock_code,
                                               at=self._clock.now())

    async def run_once(self) -> list[ReconcileReport]:
        balance = await self._broker.get_balance()
        at = self._clock.now()
        reports: list[ReconcileReport] = []
        for cyc in self._repo.load_active_cycles():
            config = self._repo.load_config(cyc.config_id)
            code = config.stock_code
            try:
                stages = self._repo.load_stages(cyc.cycle_id)
            except CorruptRowError:
                # 손상된 사이클의 격리와 사용자 통지는 복구·틱 루프의 책임이다
                # (2A 핸드오버 7). 대사가 그것을 중복해서 처리하면 같은 사건에
                # 두 개의 경로가 생기고, 어느 쪽이 PAUSED 를 만들었는지
                # 이력에서 구분되지 않는다.
                continue
            internal = pnl.held_qty(stages)
            reported = broker_qty(balance, code)
            baseline = self._repo.forced_close_baseline(code)
            # 응답에 없으면 0 으로 본다 — 대사는 경고를 내는 경로이므로
            # 긴급청산과 달리 여기서 멈출 이유가 없다. 결과가 INTERNAL_MORE 면
            # 그 종목이 PAUSED 되고, 그것이 안전한 방향이다.
            actual = (0 if reported is None else reported) - baseline
            if actual == internal:
                reports.append(ReconcileReport(code, internal, actual,
                                               baseline, "MATCH", None))
                continue
            verdict = "INTERNAL_LESS" if internal < actual else "INTERNAL_MORE"
            action = None
            if verdict == "INTERNAL_MORE" and cyc.status is CycleStatus.RUNNING:
                # 멈추는 것은 **사이클**이다. split_config.status 는
                # IDLE|ACTIVE 두 값뿐이며(설계서 12.1절·스키마 CHECK) "이
                # 설정이 사이클을 돌리고 있는가" 만 말한다 — 일시정지는
                # 사이클의 상태다.
                self._repo.save_cycle(cycle_mod.pause(cyc))
                action = "PAUSED"
            self._repo.append_reconcile_log(
                checked_at=at, stock_code=code, internal_qty=internal,
                broker_qty=actual, verdict=verdict, action_taken=action,
            )
            self._emit(ReconcileMismatch(
                stock_code=code, internal_qty=internal, broker_qty=actual,
                verdict=verdict, action_taken=action, at=at,
            ))
            reports.append(ReconcileReport(code, internal, actual, baseline,
                                           verdict, action))
        return reports
```

- [ ] **Step 5: 전체 회귀와 커밋**

Run: `.venv/bin/python -m pytest -q` → PASS

```bash
git add src/autotrading7s/engine/reconciler.py src/autotrading7s/ports/repository.py src/autotrading7s/adapters/sqlite/repository.py tests/engine/test_reconciler.py
git commit -m "$(printf 'feat: 잔고 대사 — 설계서 10.2절\n\n자동 보정을 하지 않는다(D13). 내부가 많으면 매도가 계속 거부되어 SELL_PENDING\n무한 재시도에 빠지므로 그 종목을 PAUSED 로 멈춘다. 내부를 실계좌에 맞춰 고치는\n쪽은 단계별 체결가를 조용히 조작하고 이후 모든 목표가 계산의 근거를 없앤다.\nsave_stage 참조 부재를 테스트로 고정했다.\n\n강제 종료된 수량을 기준에서 제외한다(설계서 11.4절). 빼지 않으면 강제 종료\n직후 매 5분마다 영구 경고가 나고, 사용자가 경고를 무시하는 습관을 들이면 진짜\n불일치도 무시된다.\n\n기준선 초기화를 reconcile_log 의 BASELINE_RESET 행으로 표현했다. 스키마는 버전\n1 을 넘는 마이그레이션 경로가 없으므로(2A 핸드오버 5) 컬럼 추가보다 싸다.')"
```

---

## Task 10: 재시작 복구 (설계서 10.1절)

**강제종료·정전·블루스크린 대비로 필수다.**

```
1. DB에서 PENDING(BUY_PENDING / SELL_PENDING) 상태 단계 조회
2. list_orders_today()로 각 client_ref의 결말 확인
     체결됨    → HOLDING / WAITING 으로 정정
     취소·거부 → 원래 상태 복구
     기록 없음 → 원래 상태 복구 (전일 미체결은 장 마감에 자동 소멸)
3. get_balance()로 초기 동기화 대사 (불일치 시 경고, 정지하지는 않음)
4. RUNNING 사이클의 구독 복원 → 감시 재개
```

**Ruling: 3단계는 `Reconciler` 를 쓰지 않는다.** `Reconciler` 는 `INTERNAL_MORE` 에서 종목을 `PAUSED` 로 만들지만, 설계서 10.1절 3 은 "정지하지는 않음" 을 명시한다. 재시작 직후의 불일치는 아직 정정되지 않은 주문 때문일 수 있으므로, 경고만 남기고 정지는 5분 뒤 첫 정기 대사에 맡긴다. 틀렸을 경우 비용: 진짜 불일치가 최대 5분간 정지 없이 돈다 — 설계서가 명시적으로 지정한 트레이드오프다.

**Ruling: 미체결 추적은 DB 를 진실로 삼는다.** 메모리 캐시를 두면 재시작 복구와 두 개의 진실이 생긴다. `load_pending_orders()` 가 매번 읽히며, 그 DTO 에 `stage_no` 를 추가한다 — 소비자(복구와 미체결 감시)가 전부 그것을 필요로 하고 `stage_state` 와의 조인으로 얻을 수 있다.

**Ruling: `CorruptRowError` 는 그 사이클만 격리한다** (2A 핸드오버 7). 손상된 단계 행 하나로 프로그램 전체가 기동 실패하면 사용자에게 나갈 길이 없다 — 자동 손절매가 없는 프로그램에서 크래시 루프는 포지션을 방치하는 것과 같다. 해당 설정을 `PAUSED` 로 만들고 `CycleLoadFailed` 를 내고 나머지 사이클로 넘어간다. 넓은 `except ValueError` 는 쓰지 않는다 — `CorruptRowError` 를 명시적으로 잡는다.

**Files:**
- Create: `src/autotrading7s/engine/recovery.py`
- Modify: `src/autotrading7s/ports/repository.py` (`PendingOrderRow.stage_no`), `src/autotrading7s/adapters/sqlite/repository.py` (`load_pending_orders` 조인)
- Test: `tests/engine/test_recovery.py`

**Interfaces:**
- Produces:
  - `PendingOrderRow.stage_no: int | None` (마지막 필드로 추가)
  - `RecoveryReport` — `resolved_orders: int`, `restored_stages: int`, `failed_cycles: tuple[int, ...]`, `subscribe_codes: tuple[str, ...]`
  - `Recovery(*, repo, broker, clock, emit)` — `.run() -> RecoveryReport` (async)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/engine/test_recovery.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.events import CycleLoadFailed, Event, ReconcileMismatch
from autotrading7s.domain.rules import BuyStage
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource
from autotrading7s.engine.executor import Executor
from autotrading7s.engine.recovery import Recovery

AT = datetime(2026, 9, 2, 9, 5, tzinfo=UTC)


def _recovery(repo, broker):
    events: list[Event] = []
    return Recovery(repo=repo, broker=broker, clock=FakeClock(current=AT),
                    emit=events.append), events


async def _leave_a_pending_buy(repo, broker):
    """엔진이 발주 직후 죽은 상태를 만든다 — BUY_PENDING + ACCEPTED 주문."""
    cyc = repo.load_active_cycles()[0]
    config = repo.load_config(cyc.config_id)
    ex = Executor(repo=repo, broker=broker, clock=FakeClock(current=AT),
                  emit=lambda e: None)
    waiting = next(s for s in repo.load_stages(cyc.cycle_id) if s.stage_no == 1)
    return cyc, await ex.send(
        cycle=cyc, config=config, stage=waiting,
        decision=BuyStage(stage_no=1, limit_price=10_000, qty=100, reason="r"),
        tick=Tick(code="005930", price=10_000, at=AT, source=TickSource.WS),
    )


@pytest.mark.asyncio
async def test_a_filled_order_is_reconciled_into_holding(repo_fresh):
    """2단계 '체결됨 → HOLDING 으로 정정'.

    죽어 있는 동안 체결된 주문을 놓치면 그 단계는 영원히 BUY_PENDING 이고,
    규칙 5 가 판정에서 제외하므로 그 자본이 조용히 잠긴다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    cyc, sent = await _leave_a_pending_buy(repo_fresh, broker)
    # 죽어 있는 동안 체결됐다
    broker._fill(broker._orders[sent.broker_order_id], 100)

    rec, events = _recovery(repo_fresh, broker)
    report = await rec.run()

    assert report.resolved_orders == 1
    stage = repo_fresh.load_stages(cyc.cycle_id)[0]
    assert stage.status is StageStatus.HOLDING
    assert (stage.fill_price, stage.fill_qty) == (10_000, 100)


@pytest.mark.asyncio
async def test_an_order_with_no_trace_restores_the_stage(repo_fresh):
    """'기록 없음 → 원래 상태 복구'.

    전일 미체결은 장 마감에 자동 소멸한다 — 한국 주식 주문은 당일에만
    유효하다. 그 단계를 WAITING 으로 돌려야 오늘 다시 시도된다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    cyc, sent = await _leave_a_pending_buy(repo_fresh, broker)

    async def empty(code):
        return []

    broker.list_orders_today = empty        # type: ignore[method-assign]
    rec, events = _recovery(repo_fresh, broker)
    report = await rec.run()

    assert report.restored_stages == 1
    assert repo_fresh.load_stages(cyc.cycle_id)[0].status is StageStatus.WAITING
    assert repo_fresh.load_pending_orders() == []


@pytest.mark.asyncio
async def test_a_partially_filled_order_confirms_the_filled_portion(repo_fresh):
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    cyc, sent = await _leave_a_pending_buy(repo_fresh, broker)
    broker._fill(broker._orders[sent.broker_order_id], 40)

    rec, _ = _recovery(repo_fresh, broker)
    await rec.run()

    stage = repo_fresh.load_stages(cyc.cycle_id)[0]
    assert stage.status is StageStatus.HOLDING
    assert stage.fill_qty == 40


@pytest.mark.asyncio
async def test_startup_reconcile_warns_but_never_pauses(repo_two_stocks):
    """3단계 — '불일치 시 경고, 정지하지는 않음'.

    재시작 직후의 불일치는 아직 정정되지 않은 주문 때문일 수 있다. 정지는
    5분 뒤 첫 정기 대사(10.2절)가 한다.
    """
    broker = FakeBroker([10_000], holdings={"005930": (40, 400_000),
                                            "000660": (100, 600_000)})
    rec, events = _recovery(repo_two_stocks, broker)

    await rec.run()

    mismatches = [e for e in events if isinstance(e, ReconcileMismatch)]
    assert [e.verdict for e in mismatches] == ["INTERNAL_MORE"]
    assert all(e.action_taken is None for e in mismatches)
    cyc = next(c for c in repo_two_stocks.load_active_cycles()
               if c.config_id == 1)
    assert cyc.status is CycleStatus.RUNNING
    assert repo_two_stocks.load_config(1).status == "ACTIVE"


@pytest.mark.asyncio
async def test_subscription_is_restored_for_active_cycles(repo_two_stocks):
    """4단계 — RUNNING 사이클의 구독 복원."""
    broker = FakeBroker([10_000], holdings={"005930": (100, 1_000_000),
                                            "000660": (100, 600_000)})
    rec, _ = _recovery(repo_two_stocks, broker)

    report = await rec.run()

    assert set(report.subscribe_codes) == {"005930", "000660"}


@pytest.mark.asyncio
async def test_a_corrupt_cycle_is_isolated_not_fatal(repo_two_stocks):
    """2A 핸드오버 7 — 손상된 행 하나가 기동을 막으면 사용자에게 나갈 길이 없다.

    자동 손절매가 없는 프로그램에서 크래시 루프는 포지션을 방치하는 것과
    같다. 그 사이클만 격리하고 나머지는 계속 복구한다.
    """
    # 1단계의 trigger_price 를 사다리와 어긋나게 손상시킨다 (H4)
    cyc = repo_two_stocks.load_active_cycles()[0]
    repo_two_stocks._conn.execute(
        "UPDATE stage_state SET trigger_price = trigger_price + 7 "
        "WHERE cycle_id = ? AND stage_no = 1", (cyc.cycle_id,)
    )
    repo_two_stocks._conn.commit()
    broker = FakeBroker([10_000], holdings={"000660": (100, 600_000)})
    rec, events = _recovery(repo_two_stocks, broker)

    report = await rec.run()

    assert report.failed_cycles == (cyc.cycle_id,)
    failures = [e for e in events if isinstance(e, CycleLoadFailed)]
    assert len(failures) == 1
    assert "stage_state" in failures[0].detail
    assert failures[0].action_taken == "PAUSED"
    assert repo_two_stocks.load_cycle(cyc.cycle_id).status is CycleStatus.PAUSED
    assert repo_two_stocks.load_config(cyc.config_id).status == "ACTIVE"
    # 손상되지 않은 종목은 계속 복구된다
    assert report.subscribe_codes == ("000660",)


def test_recovery_does_not_swallow_corruption_with_a_broad_except():
    """`CorruptRowError` 는 `ValueError` 의 하위다.

    엔진에 넓은 `except ValueError` 를 두면 DB 손상을 삼킨다 — 잘못된 가격이
    올라와도 조용히 넘어가고, 그 가격으로 주문이 나간다.
    """
    import ast
    import inspect

    from autotrading7s.engine import recovery as mod

    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            names = [n.id for n in ast.walk(node.type)
                     if isinstance(n, ast.Name)]
            assert "ValueError" not in names, "넓은 except ValueError 금지"
            assert "Exception" not in names, "넓은 except Exception 금지"
```

- [ ] **Step 2: 실패 확인 → 구현**

`PendingOrderRow` 에 필드를 추가한다.

```python
    stage_no: int | None = None
```

`load_pending_orders` 의 SQL 을 조인으로 바꾼다.

```python
    def load_pending_orders(self) -> list[PendingOrderRow]:
        rows = self._conn.execute(
            "SELECT o.*, s.stage_no AS stage_no FROM order_log o "
            "LEFT JOIN stage_state s ON s.id = o.stage_state_id "
            f"WHERE o.status IN ({', '.join('?' * len(self._PENDING_STATUSES))}) "
            "ORDER BY o.id",
            self._PENDING_STATUSES,
        ).fetchall()
        return [row_to_pending_order(dict(r)) for r in rows]
```

`LEFT JOIN` 인 이유: 긴급청산 주문은 `stage_state_id` 가 `None` 이므로 내부 조인이면 그 행이 사라진다 — 재시작 복구가 미체결 시장가 주문을 놓치게 된다.

`src/autotrading7s/engine/recovery.py`:

```python
"""재시작 복구 — 설계서 10.1절.

강제종료·정전·블루스크린 대비로 필수다. 죽어 있는 동안 체결된 주문을 놓치면
그 단계는 영원히 PENDING 이고, 규칙 5 가 판정에서 제외하므로 그 자본이 조용히
잠긴다.

**손상된 사이클은 격리하고 기동은 계속한다** (2A 핸드오버 7). `load_stages` 는
fail-closed 이고 복구 API 가 없으므로 단계 행 하나의 손상이 사이클 전체를
로드 불가로 만든다. 그것으로 프로그램이 기동 실패하면 사용자에게 나갈 길이
없고, 자동 손절매가 없는 프로그램에서 크래시 루프는 포지션을 방치하는 것과
같다.

**넓은 `except` 를 쓰지 않는다.** `CorruptRowError` 가 `ValueError` 의 하위이고
`ValueError` 를 넓게 잡으면 DB 손상을 삼킨다 — 잘못된 가격이 올라와도 조용히
넘어가고 그 가격으로 주문이 나간다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autotrading7s.app.events import CycleLoadFailed, Event, ReconcileMismatch
from autotrading7s.domain import pnl
from autotrading7s.domain import stage as stage_mod
from autotrading7s.domain.types import CycleStatus, FillState, StageStatus
from autotrading7s.engine.emergency import broker_qty
from autotrading7s.ports.broker import BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import CorruptRowError, RepositoryPort


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    resolved_orders: int
    restored_stages: int
    failed_cycles: tuple[int, ...]
    subscribe_codes: tuple[str, ...]


class Recovery:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: Callable[[Event], None],
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    async def run(self) -> RecoveryReport:
        resolved, restored = await self._resolve_pending_orders()
        failed, codes = await self._load_and_reconcile()
        return RecoveryReport(resolved, restored, tuple(failed), tuple(codes))
```

`CorruptRowError` 는 **Task 4 에서 이미 `ports/repository.py` 로 옮겨져 있다** — `engine/` 이 `adapters/` 를 import 할 수 없기 때문이다. 여기서 다시 옮기지 않는다.

나머지 구현:

```python
    async def _resolve_pending_orders(self) -> tuple[int, int]:
        """1~2단계. 당일 주문 조회로 각 미체결 주문의 결말을 확인한다."""
        resolved = restored = 0
        # 조회는 한 번만 한다. 루프 안에서 부르면 미체결 주문 수만큼 TR 호출이
        # 나가고, 기동 직후에 호출 제한에 걸릴 수 있다.
        orders = await self._broker.list_orders_today(None)
        by_ref = {str(o.client_ref): o for o in orders}
        for row in self._repo.load_pending_orders():
            if row.stage_no is None:
                # 긴급청산 주문 — 단계에 붙지 않는다. 종결만 시킨다.
                continue
            found = by_ref.get(row.client_ref)
            try:
                stages = self._repo.load_stages(row.cycle_id)
            except CorruptRowError:
                continue          # 아래 _load_and_reconcile 이 격리한다
            stage = next(s for s in stages if s.stage_no == row.stage_no)
            if stage.status not in (StageStatus.BUY_PENDING,
                                    StageStatus.SELL_PENDING):
                continue          # 이미 정정됐다
            is_buy = stage.status is StageStatus.BUY_PENDING
            at = self._clock.now()

            if found is None or found.filled_qty == 0:
                # 기록 없음 / 취소 / 거부 → 원래 상태 복구. 전일 미체결은 장
                # 마감에 자동 소멸한다(한국 주식 주문은 당일에만 유효).
                self._repo.update_order_log(
                    client_ref=row.client_ref, status="CANCELED",
                    api_message="재시작 복구 — 체결 흔적 없음", settled_at=at,
                )
                back = (stage_mod.cancel_buy(stage) if is_buy
                        else stage_mod.cancel_sell(
                            stage, remaining_qty=stage.fill_qty))
                self._repo.save_stage(row.cycle_id, back)
                restored += 1
                continue

            terminal = ("FILLED" if found.state is FillState.FILLED
                        else "CANCELED")
            self._repo.update_order_log(
                client_ref=row.client_ref, status=terminal,
                broker_order_id=found.broker_order_id,
                fill_price=found.filled_price, fill_qty=found.filled_qty,
                settled_at=at,
            )
            config = self._repo.load_config(
                self._repo.load_cycle(row.cycle_id).config_id)
            if is_buy:
                applied = stage_mod.to_holding(
                    stage, fill_price=found.filled_price,
                    fill_qty=found.filled_qty, at=at)
            elif found.filled_qty >= stage.fill_qty:
                applied = stage_mod.after_sell(
                    stage, at=at, allow_rebuy=config.allow_rebuy)
            else:
                applied = stage_mod.cancel_sell(
                    stage, remaining_qty=stage.fill_qty - found.filled_qty)
            self._repo.save_stage(row.cycle_id, applied)
            resolved += 1
        return resolved, restored

    async def _load_and_reconcile(self) -> tuple[list[int], list[str]]:
        """3~4단계. 경고만 하고 정지하지 않는다 (설계서 10.1절 3)."""
        balance = await self._broker.get_balance()
        at = self._clock.now()
        failed: list[int] = []
        codes: list[str] = []
        for cyc in self._repo.load_active_cycles():
            config = self._repo.load_config(cyc.config_id)
            try:
                stages = self._repo.load_stages(cyc.cycle_id)
            except CorruptRowError as exc:
                # 격리는 **사이클**을 멈추는 것이다. RUNNING 일 때만 전이한다 —
                # STARTING 은 이미 트리거를 받지 않고(accepts_triggers False),
                # LIQUIDATING 을 되돌리면 진행 중인 긴급청산의 상태를
                # 프로그램이 뒤집는 것이 된다 (원장 Ruling 5).
                action = None
                if cyc.status is CycleStatus.RUNNING:
                    self._repo.save_cycle(cycle_mod.pause(cyc))
                    action = "PAUSED"
                self._emit(CycleLoadFailed(
                    config_id=cyc.config_id, cycle_id=cyc.cycle_id,
                    detail=str(exc), action_taken=action, at=at,
                ))
                failed.append(cyc.cycle_id)
                continue
            internal = pnl.held_qty(stages)
            reported = broker_qty(balance, config.stock_code)
            baseline = self._repo.forced_close_baseline(config.stock_code)
            actual = (0 if reported is None else reported) - baseline
            if actual != internal:
                verdict = ("INTERNAL_LESS" if internal < actual
                           else "INTERNAL_MORE")
                self._repo.append_reconcile_log(
                    checked_at=at, stock_code=config.stock_code,
                    internal_qty=internal, broker_qty=actual,
                    verdict=verdict, action_taken=None,
                )
                self._emit(ReconcileMismatch(
                    stock_code=config.stock_code, internal_qty=internal,
                    broker_qty=actual, verdict=verdict, action_taken=None,
                    at=at,
                ))
            if cyc.status in (CycleStatus.RUNNING, CycleStatus.STARTING):
                codes.append(config.stock_code)
        return failed, codes
```

- [ ] **Step 3~5: 통과 확인 → 전체 회귀 → 커밋**

```bash
git add src/autotrading7s/engine/recovery.py src/autotrading7s/ports src/autotrading7s/adapters/sqlite tests/engine/test_recovery.py
git commit -m "$(printf 'feat: 재시작 복구 — 설계서 10.1절\n\n죽어 있는 동안 체결된 주문을 놓치면 그 단계는 영원히 PENDING 이고, 규칙 5 가\n판정에서 제외하므로 그 자본이 조용히 잠긴다.\n\n손상된 사이클은 격리하고 기동은 계속한다(2A 핸드오버 7). load_stages 는\nfail-closed 이고 복구 API 가 없어서 단계 행 하나로 사이클 전체가 로드 불가가\n된다. 그것으로 기동이 실패하면 사용자에게 나갈 길이 없고, 자동 손절매가 없는\n프로그램에서 크래시 루프는 포지션 방치와 같다.\n\n넓은 except 를 금지하는 테스트를 뒀다. CorruptRowError 가 ValueError 의\n하위이므로 넓게 잡으면 DB 손상을 삼키고, 잘못된 가격으로 주문이 나간다.\n\n기동 대사는 정지하지 않는다(설계서 10.1절 3). 재시작 직후의 불일치는 아직\n정정되지 않은 주문 때문일 수 있고, 정지는 첫 정기 대사가 한다.\n\nCorruptRowError 를 ports 로 옮겼다 — engine 은 adapters 를 import 할 수 없다.\nPendingOrderRow 에 stage_no 를 추가했다(LEFT JOIN — 긴급청산 주문은\nstage_state_id 가 없으므로 내부 조인이면 사라진다).')"
```

---

## Task 11: 오케스트레이터 (설계서 7.1절)

**Ruling: 큐는 `queue.Queue` 다, `asyncio.Queue` 가 아니다.** GUI 는 Tkinter 메인 스레드에서 `put` 하고 엔진은 asyncio 이벤트 루프에서 꺼낸다. `asyncio.Queue` 는 스레드 안전하지 않으므로 다른 스레드에서 `put` 하면 조용히 깨진다. 엔진은 `get_nowait()` 로 비우고 주입된 `sleep` 으로 양보한다.

**Ruling: 실시간 대기는 주입된 `sleep` 으로 한다.** `asyncio.sleep` 을 직접 부르면 G2 시나리오 12건이 실제 시간을 소모하고, 3초 타임아웃 테스트마다 3초가 든다. `run()` 의 생성자가 `sleep: Callable[[float], Awaitable[None]]` 를 받고 테스트는 `FakeClock` 을 전진시키는 함수를 넘긴다.

**Ruling: `run()` 은 시세 스트림이 끝나면 정상 종료한다.** `FakeBroker` 의 스크립트가 유한하므로 G2 게이트가 `run()` 을 그대로 돌릴 수 있다. 실전에서 스트림이 끝나는 것은 종료 신호(장 마감 또는 연결 종료)이며, 끊김은 예외로 오고 그것은 폴백 경로가 받는다.

**Files:**
- Create: `src/autotrading7s/engine/orchestrator.py`
- Test: `tests/engine/test_orchestrator.py`

**Interfaces:**
- Produces: `Orchestrator(*, repo, broker, clock, settings, command_q, priority_q, event_q, sleep=asyncio.sleep, fallback_poll_sec=1.0)` — `.on_tick(tick)`, `.poll_pending()`, `.reconcile()`, `.drain_commands()`, `.run()` (전부 async), `.stopped: bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/engine/test_orchestrator.py`:

```python
from __future__ import annotations

import queue
from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.fake.broker import FailMode, FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.commands import (
    EmergencyLiquidate,
    PauseCycle,
    ResetReconcileBaseline,
    Shutdown,
    StartCycle,
)
from autotrading7s.app.events import (
    CycleClosed,
    EmergencyResult,
    GuardBlocked,
    QuoteFallback,
    StageFilled,
)
from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource
from autotrading7s.engine.orchestrator import Orchestrator

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _build(repo, broker, *, total_limit=100_000_000, max_orders=10):
    clock = FakeClock(current=AT)
    qs = (queue.Queue(), queue.Queue(), queue.Queue())

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    orch = Orchestrator(
        repo=repo, broker=broker, clock=clock,
        settings=EngineSettings(total_limit=total_limit,
                                max_orders_per_minute=max_orders),
        command_q=qs[0], priority_q=qs[1], event_q=qs[2], sleep=sleep,
        # 재구독이 즉시 다시 끊기는 시뮬레이션에서 run() 이 무한 루프가 되지
        # 않도록 유한한 값을 준다. 기본값 None(무한)은 실전용이다.
        max_fallback_rounds=1,
    )
    return orch, clock, qs


def _drain(event_q):
    out = []
    while not event_q.empty():
        out.append(event_q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_priority_queue_is_consumed_first(repo_two_stocks):
    """설계서 7.1절 — priority_q 가 긴급 기능의 즉시성을 구조적으로 보장한다.

    일반 명령이 100건 쌓여 있어도 긴급청산이 먼저 처리돼야 한다. 순서가
    뒤바뀌면 급락 중에 청산이 100건 뒤로 밀린다.
    """
    broker = FakeBroker([10_000], validate_account=True,
                        holdings={"005930": (100, 1_000_000)})
    orch, clock, (command_q, priority_q, event_q) = _build(repo_two_stocks,
                                                           broker)
    for _ in range(100):
        command_q.put(PauseCycle(config_id=2))
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="긴급", confirmed_text=None))

    await orch.drain_commands()

    events = _drain(event_q)
    assert isinstance(events[0], EmergencyResult)
    assert events[0].result == "SUCCESS"


@pytest.mark.asyncio
async def test_start_cycle_confirms_the_anchor_on_the_first_tick(repo_fresh):
    """앵커는 GUI 가 정하지 않는다 — 엔진이 첫 틱의 가격으로 확정한다.

    STARTING 은 트리거를 받지 않으므로(도메인 accepts_triggers), 앵커 확정
    전에는 어떤 주문도 나가지 않는다.
    """
    repo_fresh.set_config_status(1, "IDLE", at=AT)
    cyc = repo_fresh.load_active_cycles()[0]
    broker = FakeBroker([9_800], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _build(repo_fresh, broker)

    # 이미 RUNNING 인 사이클을 STARTING 으로 되돌릴 수는 없으므로, 새 설정을
    # StartCycle 로 시작시킨다.
    command_q.put(StartCycle(config_id=1))
    await orch.drain_commands()
    starting = [c for c in repo_fresh.load_active_cycles()
                if c.status is CycleStatus.STARTING]
    assert starting, "StartCycle 이 STARTING 사이클을 만들어야 한다"

    await orch.on_tick(Tick(code="005930", price=9_800, at=AT,
                            source=TickSource.WS))

    running = [c for c in repo_fresh.load_active_cycles()
               if c.status is CycleStatus.RUNNING]
    assert running[0].anchor_price == 9_800
    assert running[0].ladder is not None


@pytest.mark.asyncio
async def test_buy_trigger_places_an_order_and_fills(repo_fresh):
    """틱 → decide() → 가드 → 발주 → 체결 반영의 한 바퀴."""
    broker = FakeBroker([10_000, 9_500], validate_account=True,
                        cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.on_tick(Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))
    await orch.poll_pending()

    cyc = repo_fresh.load_active_cycles()[0]
    stages = repo_fresh.load_stages(cyc.cycle_id)
    filled = [s for s in stages if s.status is StageStatus.HOLDING]
    assert [s.stage_no for s in filled] == [2]
    assert any(isinstance(e, StageFilled) for e in _drain(event_q))


@pytest.mark.asyncio
async def test_guard_block_emits_an_event_and_places_nothing(repo_fresh):
    """② 가드 실패 시 로그만 남기고 종료한다 (설계서 9절)."""
    broker = FakeBroker([9_500], validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker, total_limit=1)

    await orch.on_tick(Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))

    blocked = [e for e in _drain(event_q) if isinstance(e, GuardBlocked)]
    assert len(blocked) == 1
    assert "총한도" in blocked[0].reason
    assert await broker.list_orders_today("005930") == []


@pytest.mark.asyncio
async def test_cycle_closes_when_the_last_share_is_sold(repo_fresh):
    """D5 — 사이클 종료는 보유 0 도달로만 일어난다.

    종료 시 realized_pnl 을 기록하고 설정을 IDLE 로 돌리는 것이 엔진의 몫이다
    (2A 핸드오버 2·6).
    """
    broker = FakeBroker([10_000, 9_500, 10_100], validate_account=True,
                        cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    # 2단계 매수 → 목표가 도달 → 매도 → 보유 0
    await orch.on_tick(Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))
    await orch.poll_pending()
    clock.advance(120)                     # 쿨다운 경과
    await orch.on_tick(Tick(code="005930", price=10_100, at=clock.now(),
                            source=TickSource.WS))
    await orch.poll_pending()

    closed = [e for e in _drain(event_q) if isinstance(e, CycleClosed)]
    assert len(closed) == 1
    assert repo_fresh.load_config(1).status == "IDLE"
    row = repo_fresh._conn.execute(
        "SELECT realized_pnl FROM cycle WHERE id = ?", (closed[0].cycle_id,)
    ).fetchone()
    assert dict(row)["realized_pnl"] == closed[0].realized_pnl


@pytest.mark.asyncio
async def test_websocket_drop_falls_back_to_rest_and_keeps_deciding(repo_fresh):
    """설계서 8.4절 — 끊겨도 트리거 판정은 계속 수행한다.

    폴백 중에 판정을 멈추면 급락 구간에서 매수 기회를 통째로 놓치고, 더
    나쁘게는 목표가 도달한 매도를 놓친다. 폴백 구간 진입·복귀가 각각 이벤트로
    남아야 한다(로깅 요구).
    """
    broker = FakeBroker([10_000, 9_500, 9_400], validate_account=True,
                        cash=100_000_000, fail_mode=FailMode.DISCONNECT,
                        fail_after=1)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.run()

    events = _drain(event_q)
    fallbacks = [e for e in events if isinstance(e, QuoteFallback)]
    assert [e.active for e in fallbacks][:1] == [True]
    # 폴백 구간에서도 주문이 나갔다 — 판정이 멈추지 않았다는 증거
    assert len(await broker.list_orders_today("005930")) >= 1


@pytest.mark.asyncio
async def test_shutdown_stops_the_run_loop(repo_fresh):
    broker = FakeBroker([10_000] * 50, fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, _) = _build(repo_fresh, broker)
    command_q.put(Shutdown())

    await orch.run()

    assert orch.stopped is True


@pytest.mark.asyncio
async def test_reset_reconcile_baseline_command_is_handled(repo_two_stocks):
    broker = FakeBroker([10_000])
    orch, clock, (command_q, _, _) = _build(repo_two_stocks, broker)
    command_q.put(ResetReconcileBaseline(stock_code="005930"))

    await orch.drain_commands()

    row = repo_two_stocks._conn.execute(
        "SELECT action_taken FROM reconcile_log"
    ).fetchone()
    assert dict(row)["action_taken"] == "BASELINE_RESET"


@pytest.mark.asyncio
async def test_empty_stage_set_pauses_instead_of_crashing(repo_fresh):
    """Plan 1 핸드오버 5 — `is_cycle_complete([])` 가 DomainInvariantError 다.

    엔진이 그것을 흡수하지 않으면 단계 행이 사라진 사이클 하나가 틱 루프를
    죽인다. 그러면 다른 종목의 매도도 함께 멈춘다.
    """
    from autotrading7s.app.events import CycleLoadFailed

    cyc = repo_fresh.load_active_cycles()[0]
    repo_fresh._conn.execute("DELETE FROM stage_state WHERE cycle_id = ?",
                             (cyc.cycle_id,))
    repo_fresh._conn.commit()
    broker = FakeBroker([9_500], validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.on_tick(Tick(code="005930", price=9_500, at=AT,
                            source=TickSource.WS))

    events = _drain(event_q)
    assert any(isinstance(e, CycleLoadFailed) for e in events)
    # 멈추는 것은 사이클이다 — 설정은 ACTIVE 로 남는다 (원장 Ruling 1)
    assert repo_fresh.load_active_cycles()[0].status is CycleStatus.PAUSED
    assert repo_fresh.load_config(1).status == "ACTIVE"


def test_orchestrator_never_sleeps_on_the_real_clock():
    """주입된 sleep 만 쓴다.

    asyncio.sleep 을 직접 부르면 G2 시나리오 12건이 실제 시간을 소모하고,
    3초 타임아웃 테스트마다 3초가 든다.
    """
    import inspect

    from autotrading7s.engine import orchestrator as mod

    source = inspect.getsource(mod)
    assert "await asyncio.sleep" not in source
```

- [ ] **Step 2: 실패 확인 → 구현**

`src/autotrading7s/engine/orchestrator.py`:

```python
"""엔진 조립 — 설계서 7.1절.

다섯 개의 asyncio 태스크가 하나의 이벤트 루프에서 협력적으로 돈다: 명령 소비,
시세 수신, 트리거 판정, 미체결 감시, 잔고 대사. 단일 작성자 구조이므로
리포지토리의 확인-후-갱신이 안전하다 (2A 핸드오버 3).

`priority_q` 를 `command_q` 보다 먼저 비운다. 이것이 설계서 6절 "긴급 기능의
즉시성" 을 구조적으로 보장하는 지점이다 — 일반 명령이 100건 쌓여 있어도
긴급청산이 먼저 처리된다.

큐는 `queue.Queue` 다. GUI 는 Tkinter 메인 스레드에서 `put` 하므로
`asyncio.Queue` 는 스레드 안전하지 않아 조용히 깨진다.

**규칙을 재구현하지 않는다.** 트리거 판정은 `rules.decide()` 가 전부 하고 이
모듈은 그 결과를 집행한다. 여기에 "PENDING 이면 건너뛴다" 같은 코드가 생기면
그것은 규칙 5 의 중복이다.
"""

from __future__ import annotations

import asyncio
import queue
from collections.abc import Awaitable, Callable
from datetime import timedelta

from autotrading7s.app import commands as cmd
from autotrading7s.app.events import (
    CycleClosed,
    CycleLoadFailed,
    EngineStopped,
    Event,
    GuardBlocked,
    QuoteFallback,
    TickUpdate,
)
from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.rules import BuyStage, TriggerParams, decide
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    Tick,
    TickSource,
)
from autotrading7s.engine.emergency import EmergencyHandler
from autotrading7s.engine.executor import Executor
from autotrading7s.engine.guards import GuardGate
from autotrading7s.engine.reconciler import Reconciler
from autotrading7s.ports.broker import BrokerDisconnected, BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import CorruptRowError, RepositoryPort


class Orchestrator:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        settings: EngineSettings, command_q: queue.Queue,
        priority_q: queue.Queue, event_q: queue.Queue,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        fallback_poll_sec: float = 1.0,
        max_fallback_rounds: int | None = None,
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._settings = settings
        self._command_q = command_q
        self._priority_q = priority_q
        self._event_q = event_q
        self._sleep = sleep
        self._fallback_poll_sec = fallback_poll_sec
        # None 은 무한 재시도다 — 상시 가동 프로세스에서 옳은 기본값이다.
        # 테스트는 유한한 값을 넘겨 종료 조건을 얻는다.
        self._max_fallback_rounds = max_fallback_rounds
        self._guards = GuardGate(repo, settings)
        self._executor = Executor(repo=repo, broker=broker, clock=clock,
                                  emit=self._emit)
        self._emergency = EmergencyHandler(repo=repo, broker=broker,
                                           clock=clock, emit=self._emit)
        self._reconciler = Reconciler(repo=repo, broker=broker, clock=clock,
                                      emit=self._emit)
        self.stopped = False

    def _emit(self, event: Event) -> None:
        self._event_q.put(event)

    # ── 명령 소비 ───────────────────────────────────────────────────────
    async def drain_commands(self) -> None:
        """`priority_q` 를 먼저 완전히 비우고, 그 다음 `command_q` 를 본다."""
        while True:
            try:
                command = self._priority_q.get_nowait()
            except queue.Empty:
                break
            await self._handle(command)
        while True:
            try:
                command = self._command_q.get_nowait()
            except queue.Empty:
                break
            await self._handle(command)

    async def _handle(self, command: cmd.Command) -> None:
        if isinstance(command, cmd.EmergencyLiquidate):
            if command.scope == "ALL":
                await self._emergency.liquidate_all(reason=command.reason)
            else:
                await self._emergency.liquidate_single(command.config_id,
                                                       reason=command.reason)
        elif isinstance(command, cmd.ForceClose):
            await self._emergency.force_close(command.config_id,
                                             reason=command.reason)
        elif isinstance(command, cmd.StartCycle):
            self._start_cycle(command.config_id)
        elif isinstance(command, cmd.PauseCycle):
            self._transition(command.config_id, cycle_mod.pause)
        elif isinstance(command, cmd.ResumeCycle):
            self._transition(command.config_id, cycle_mod.resume)
        elif isinstance(command, cmd.StopCycle):
            # D5 — 정지는 자동 트리거를 멈추는 것이고 사이클 종료가 아니다.
            self._transition(command.config_id, cycle_mod.pause)
        elif isinstance(command, cmd.ResetReconcileBaseline):
            self._reconciler.reset_baseline(command.stock_code)
        elif isinstance(command, cmd.Shutdown):
            self.stopped = True

    def _start_cycle(self, config_id: int) -> None:
        """앵커는 첫 틱에서 확정된다 — GUI 가 가격을 정하지 않는다.

        `create_cycle` 이 이미 STARTING 사이클을 삽입하고 반환하므로
        `cycle.start()` 를 다시 부르지 않는다 (원장 Ruling 2).
        """
        at = self._clock.now()
        self._repo.create_cycle(config_id, at)
        self._repo.set_config_status(config_id, "ACTIVE", at=at)

    def _isolate(self, cyc) -> str | None:
        """데이터 문제가 있는 사이클을 격리한다 — 사이클을 PAUSED 로.

        RUNNING 일 때만 전이한다 (원장 Ruling 5): STARTING 은 이미 트리거를
        받지 않고(`accepts_triggers` False), LIQUIDATING 을 되돌리면 진행 중인
        긴급청산의 상태를 프로그램이 뒤집는 것이 된다. 반환값은 **실제로 한
        것**이며 그대로 이벤트의 `action_taken` 이 된다.
        """
        if cyc.status is not CycleStatus.RUNNING:
            return None
        self._repo.save_cycle(cycle_mod.pause(cyc))
        return "PAUSED"

    def _transition(self, config_id: int, fn) -> None:
        """사이클 상태만 바꾼다.

        `split_config.status` 는 IDLE|ACTIVE 두 값뿐이며(설계서 12.1절·스키마
        CHECK) "이 설정이 사이클을 돌리고 있는가" 만 말한다. 일시정지는
        사이클의 상태다 (원장 Ruling 1).
        """
        for cyc in self._repo.load_active_cycles():
            if cyc.config_id == config_id:
                self._repo.save_cycle(fn(cyc))

    # ── 틱 처리 ─────────────────────────────────────────────────────────
    async def on_tick(self, tick: Tick) -> None:
        self._emit(TickUpdate(stock_code=tick.code, price=tick.price,
                              source=tick.source, at=tick.at))
        for cyc in self._repo.load_active_cycles():
            config = self._repo.load_config(cyc.config_id)
            if config.stock_code != tick.code:
                continue
            try:
                await self._advance(cyc, config, tick)
            except (CorruptRowError, DomainInvariantError) as exc:
                # Plan 1 핸드오버 5 / 2A 핸드오버 7 — 한 사이클의 데이터
                # 문제가 틱 루프를 죽이면 다른 종목의 매도도 함께 멈춘다.
                self._emit(CycleLoadFailed(
                    config_id=cyc.config_id, cycle_id=cyc.cycle_id,
                    detail=str(exc), action_taken=self._isolate(cyc),
                    at=self._clock.now(),
                ))

    async def _advance(self, cyc, config, tick: Tick) -> None:
        if cyc.status is CycleStatus.STARTING:
            ladder = config.to_ladder(anchor_price=tick.price)
            self._repo.save_cycle(cycle_mod.confirm_anchor(
                cyc, anchor_price=tick.price, ladder=ladder, at=tick.at))
            return
        if not cyc.accepts_triggers():
            return

        stages = self._repo.load_stages(cyc.cycle_id)
        params = TriggerParams(target_pct=config.target_pct,
                               allow_rebuy=config.allow_rebuy,
                               rebuy_cooldown_sec=config.rebuy_cooldown_sec)
        decisions = decide(
            tick=tick, cycle=cyc, states=stages, params=params,
            now=self._clock.now(),
            market_open=self._clock.is_market_open(self._clock.now()),
            stock_code=config.stock_code,
        )
        for decision in decisions:
            # 주문 빈도 제한의 '지금' 은 틱의 시각이다, 시계가 아니다. 빈도는
            # 시장 시간 기준으로 세는 것이 맞고, 그래야 시세 스크립트만으로
            # 시나리오가 결정론적으로 재현된다 — 시계를 따로 전진시키지 않으면
            # 창이 미끄러지지 않아 11번째 주문부터 전부 막힌다.
            now = tick.at
            if isinstance(decision, BuyStage):
                verdict = self._guards.check_buy(
                    decision, stock_code=config.stock_code,
                    stock_limit=config.total_limit, now=now)
                side = "BUY"
            else:
                verdict = self._guards.check_sell(decision, now=now)
                side = "SELL"
            if not verdict.allowed:
                self._emit(GuardBlocked(
                    config_id=config.config_id, stage_no=decision.stage_no,
                    side=side, reason=verdict.reason, at=now,
                ))
                continue
            # 한 틱이 여러 매도를 낼 수 있으므로 결정 사이에 증가시킨다
            self._guards.record_order(now)
            stage = next(s for s in self._repo.load_stages(cyc.cycle_id)
                         if s.stage_no == decision.stage_no)
            await self._executor.send(cycle=cyc, config=config, stage=stage,
                                      decision=decision, tick=tick)

    # ── 미체결 감시 ─────────────────────────────────────────────────────
    async def poll_pending(self) -> None:
        """DB 를 진실로 삼는다 — 메모리 캐시를 두면 재시작 복구와 두 개의
        진실이 생긴다."""
        for row in self._repo.load_pending_orders():
            if row.stage_no is None or row.broker_order_id is None:
                continue
            config_id: int | None = None
            cyc = None
            try:
                cyc = self._repo.load_cycle(row.cycle_id)
                config_id = cyc.config_id
                config = self._repo.load_config(config_id)
                stage = next(s for s in self._repo.load_stages(row.cycle_id)
                             if s.stage_no == row.stage_no)
                await self._executor.poll_fill(
                    cycle=cyc, config=config, stage=stage,
                    client_ref=row.client_ref,
                    broker_order_id=row.broker_order_id, sent_at=row.sent_at,
                    timeout_sec=self._settings.pending_timeout_sec,
                )
                self._close_if_complete(cyc, config)
            except (CorruptRowError, DomainInvariantError) as exc:
                # `is_cycle_complete([])` 도 여기로 온다 (Plan 1 핸드오버 5).
                # 한 사이클의 데이터 문제로 미체결 감시 전체가 멈추면 다른
                # 종목의 체결 반영도 함께 멈춘다.
                self._emit(CycleLoadFailed(
                    config_id=config_id, cycle_id=row.cycle_id,
                    detail=str(exc),
                    action_taken=None if cyc is None else self._isolate(cyc),
                    at=self._clock.now(),
                ))

    def _close_if_complete(self, cyc, config) -> None:
        """D5 — 사이클 종료는 보유 0 도달로만 일어난다."""
        stages = self._repo.load_stages(cyc.cycle_id)
        if not cycle_mod.is_cycle_complete(stages):
            return
        if not any(s.rebuy_count or s.last_sold_at for s in stages):
            return          # 아직 아무것도 사고팔지 않은 사이클이다
        at = self._clock.now()
        closed = cycle_mod.close(cyc, reason=CloseReason.NORMAL, at=at,
                                 states=stages)
        self._repo.save_cycle(closed)
        realized = self._repo.realized_pnl_for_cycle(cyc.cycle_id)
        self._repo.set_realized_pnl(cyc.cycle_id, realized)
        self._repo.set_config_status(config.config_id, "IDLE", at=at)
        self._emit(CycleClosed(config_id=config.config_id,
                               cycle_id=cyc.cycle_id,
                               reason=CloseReason.NORMAL,
                               realized_pnl=realized, at=at))

    async def reconcile(self) -> None:
        await self._reconciler.run_once()

    # ── 조립 ────────────────────────────────────────────────────────────
    async def run(self) -> None:
        """시세 스트림을 소비하며 매 틱마다 명령·판정·감시를 돈다.

        태스크를 실제로 5개 띄우지 않고 한 루프에서 순서대로 부르는 이유:
        단일 이벤트 루프에서 협력적으로 도는 것과 관측 가능한 동작이 같고,
        틱 단위로 결정론적이어서 G2 시나리오 12건을 재현할 수 있다. 실전에서
        틱 사이의 유휴 시간이 길어지면 대사와 미체결 감시가 늦어지므로, 그때는
        `asyncio.create_task` 로 분리하는 것이 다음 단계다.
        """
        await self.drain_commands()
        rounds = 0
        while not self.stopped:
            codes = self._subscribed_codes()
            if not codes:
                return
            try:
                async for tick in self._broker.subscribe_quotes(codes):
                    await self._cycle_once(tick)
                    if self.stopped:
                        return
                return                      # 스트림 정상 종료
            except BrokerDisconnected:
                await self._fallback(codes)
                rounds += 1
                if (self._max_fallback_rounds is not None
                        and rounds >= self._max_fallback_rounds):
                    # 재구독이 즉시 다시 끊기면 무한 루프가 된다. 실전에서는
                    # 무한 재시도가 맞지만(상시 가동), 테스트는 종료 조건이
                    # 있어야 이 경로를 돌릴 수 있다.
                    self._emit(EngineStopped(
                        detail=f"시세 재연결 {rounds}회 실패 — 엔진을 멈춥니다",
                        at=self._clock.now(),
                    ))
                    self.stopped = True
                    return

    async def _cycle_once(self, tick: Tick) -> None:
        await self.drain_commands()
        await self.on_tick(tick)
        await self.poll_pending()
        if self._due_for_reconcile(tick):
            await self.reconcile()

    async def _fallback(self, codes: list[str]) -> None:
        """설계서 8.4절 — REST 폴백. **트리거 판정은 계속 수행한다.**

        폴백 중에 판정을 멈추면 급락 구간의 매수 기회를 통째로 놓치고, 더
        나쁘게는 목표가에 도달한 매도를 놓친다.
        """
        at = self._clock.now()
        self._emit(QuoteFallback(stock_codes=tuple(codes), active=True, at=at))
        for _ in range(3):
            if self.stopped:
                return
            for code in codes:
                price = await self._broker.get_price(code)
                await self._cycle_once(Tick(code=code, price=price,
                                            at=self._clock.now(),
                                            source=TickSource.REST_POLL))
            await self._sleep(self._fallback_poll_sec)
        self._emit(QuoteFallback(stock_codes=tuple(codes), active=False,
                                 at=self._clock.now()))

    def _subscribed_codes(self) -> list[str]:
        codes: list[str] = []
        for cyc in self._repo.load_active_cycles():
            code = self._repo.load_config(cyc.config_id).stock_code
            if code not in codes:
                codes.append(code)
        return codes

    def _due_for_reconcile(self, tick: Tick) -> bool:
        last = getattr(self, "_last_reconcile", None)
        if last is None:
            self._last_reconcile = tick.at
            return False
        if tick.at - last >= timedelta(
            seconds=self._settings.reconcile_interval_sec
        ):
            self._last_reconcile = tick.at
            return True
        return False
```

**`_close_if_complete` 의 두 번째 조건이 필요한 이유:** 갓 시작한 사이클은 전 단계가 `WAITING` 이므로 `is_cycle_complete` 가 `True` 다. 그것으로 닫으면 아무것도 사지 않은 사이클이 즉시 종료된다. `rebuy_count` 나 `last_sold_at` 이 하나라도 있으면 그 사이클은 최소 한 번 매도를 완료했다는 뜻이다.

- [ ] **Step 3~5: 통과 확인 → 전체 회귀 → 커밋**

```bash
git add src/autotrading7s/engine/orchestrator.py tests/engine/test_orchestrator.py
git commit -m "$(printf 'feat: 오케스트레이터 — 설계서 7.1절\n\npriority_q 를 command_q 보다 먼저 완전히 비운다. 일반 명령 100건이 쌓여 있어도\n긴급청산이 먼저 처리되는 것을 테스트가 확인한다 — 순서가 뒤바뀌면 급락 중에\n청산이 100건 뒤로 밀린다.\n\n큐는 queue.Queue 다. GUI 는 Tkinter 메인 스레드에서 put 하므로 asyncio.Queue 는\n스레드 안전하지 않아 조용히 깨진다.\n\n실시간 대기를 주입된 sleep 으로 한다. asyncio.sleep 을 직접 부르면 G2 시나리오\n12건이 실제 시간을 소모한다. 그 부재를 테스트로 고정했다.\n\nWS 끊김에서 REST 폴백으로 넘어가고 폴백 중에도 판정을 계속한다(설계서 8.4절).\n멈추면 급락 구간의 매수 기회를 놓치고 더 나쁘게는 목표가 매도를 놓친다.\n\n규칙을 재구현하지 않는다 — decide() 의 결과를 집행할 뿐이다. 한 틱이 여러\n매도를 낼 수 있으므로 결정 사이에 guard 카운터를 증가시킨다(Plan 1 핸드오버 2).\n\n데이터 문제(CorruptRowError, DomainInvariantError)는 그 사이클만 PAUSED 로\n격리한다 — 틱 루프가 죽으면 다른 종목의 매도도 함께 멈춘다.')"
```

---

## Task 12: 스레드 브리지와 headless 기동

**Ruling: `cli.py` 는 지금 시뮬레이션 브로커만 기동한다.** 키움 어댑터가 없으므로 `--env mock`·`--env real` 의 브로커 배선은 Plan 3 이 채운다. 존재하지 않는 어댑터를 위한 배선을 미리 짜면 Plan 3 이 그 배선에 맞추게 되고, 그것이 Plan 1 에서 겪은 "계약이 소비자보다 먼저 정해져 어긋나는" 문제다. 지금은 `--simulate <가격들>` 경로만 동작하고 그 밖은 명확한 오류를 낸다. **DB 경로 분리(D15)는 지금 확정한다** — `--env` 가 `data/mock/` 과 `data/real/` 을 가른다.

**Files:**
- Create: `src/autotrading7s/app/engine_thread.py`, `src/autotrading7s/cli.py`
- Test: `tests/app/test_engine_thread.py`, `tests/app/test_cli.py`

**Interfaces:**
- Produces:
  - `EngineThread(*, orchestrator_factory, recovery_factory)` — `.command_q`, `.priority_q`, `.event_q`, `.start()`, `.send(cmd)`, `.send_priority(cmd)`, `.drain_events() -> list[Event]`, `.stop(timeout=5.0)`, `.is_alive() -> bool`
  - `cli.db_path_for(env: str) -> Path`, `cli.build_parser()`, `cli.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/app/test_engine_thread.py`:

```python
from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from autotrading7s.app.commands import PauseCycle, Shutdown, StartCycle
from autotrading7s.app.engine_thread import EngineThread
from autotrading7s.app.events import EngineStopped, Event

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


class _Orchestrator:
    """큐만 소비하는 최소 오케스트레이터 — 스레드 브리지 자체를 검증한다."""

    def __init__(self, command_q, priority_q, event_q):
        self.command_q = command_q
        self.priority_q = priority_q
        self.event_q = event_q
        self.seen: list[object] = []
        self.stopped = False

    async def run(self):
        import queue
        while not self.stopped:
            for q in (self.priority_q, self.command_q):
                try:
                    command = q.get_nowait()
                except queue.Empty:
                    continue
                self.seen.append(command)
                if isinstance(command, Shutdown):
                    self.stopped = True
            self.event_q.put(EngineStopped(detail=None, at=AT))


class _Recovery:
    def __init__(self):
        self.ran = False

    async def run(self):
        self.ran = True


def test_send_priority_rejects_a_plain_command():
    """priority_q 에 일반 명령이 들어가면 우선순위 보장이 무의미해진다.

    타입이 자격을 표현하므로(Task 1) 브리지가 그것을 단정할 수 있다.
    """
    thread = EngineThread(
        orchestrator_factory=lambda **kw: _Orchestrator(**kw),
        recovery_factory=lambda: _Recovery(),
    )
    with pytest.raises(TypeError, match="PriorityCommand"):
        thread.send_priority(PauseCycle(config_id=1))


def test_recovery_runs_before_the_orchestrator():
    """설계서 10.1절 — 복구가 끝나기 전에 트리거 판정을 시작하면 안 된다.

    미체결 주문이 아직 정정되지 않은 상태에서 판정하면 그 단계가 PENDING 이
    아니라 WAITING 으로 보여 중복 발주가 된다.
    """
    recovery = _Recovery()
    order: list[str] = []
    orch_box: list[_Orchestrator] = []

    def make_orch(**kw):
        order.append("orchestrator")
        orch = _Orchestrator(**kw)
        orch_box.append(orch)
        return orch

    def make_recovery():
        order.append("recovery")
        return recovery

    thread = EngineThread(orchestrator_factory=make_orch,
                          recovery_factory=make_recovery)
    thread.start()
    thread.send(Shutdown())
    thread.stop()

    assert recovery.ran is True
    assert order == ["recovery", "orchestrator"]


def test_commands_reach_the_engine_and_events_come_back():
    orch_box: list[_Orchestrator] = []

    def make_orch(**kw):
        orch = _Orchestrator(**kw)
        orch_box.append(orch)
        return orch

    thread = EngineThread(orchestrator_factory=make_orch,
                          recovery_factory=lambda: _Recovery())
    thread.start()
    thread.send(StartCycle(config_id=3))
    thread.send(Shutdown())
    thread.stop()

    assert any(isinstance(c, StartCycle) for c in orch_box[0].seen)
    events = thread.drain_events()
    assert events and all(isinstance(e, Event) for e in events)


def test_stop_joins_the_thread():
    thread = EngineThread(
        orchestrator_factory=lambda **kw: _Orchestrator(**kw),
        recovery_factory=lambda: _Recovery(),
    )
    thread.start()
    thread.send(Shutdown())
    thread.stop()
    assert thread.is_alive() is False
```

`tests/app/test_cli.py`:

```python
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
    code = cli.main(["--env", "real", "--settings", str(settings)])
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
```

- [ ] **Step 2: 실패 확인 → 구현**

`src/autotrading7s/app/engine_thread.py`:

```python
"""스레드 브리지 — 설계서 7.1절.

큐를 소유하고 엔진 스레드를 띄운다. GUI(Tkinter 메인 스레드)는 이 객체의
`send`·`send_priority`·`drain_events` 만 쓰며 DB 를 직접 건드리지 않는다 —
그 규칙이 리포지토리의 단일 작성자 전제를 성립시킨다 (2A 핸드오버 3).

복구가 오케스트레이터보다 먼저 돈다. 미체결 주문이 아직 정정되지 않은 상태에서
판정하면 그 단계가 PENDING 이 아니라 WAITING 으로 보여 중복 발주가 된다.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable

from autotrading7s.app.commands import Command, PriorityCommand
from autotrading7s.app.events import Event


class EngineThread:
    def __init__(
        self, *, orchestrator_factory: Callable[..., object],
        recovery_factory: Callable[[], object],
    ) -> None:
        self.command_q: queue.Queue = queue.Queue()
        self.priority_q: queue.Queue = queue.Queue()
        self.event_q: queue.Queue = queue.Queue()
        self._orchestrator_factory = orchestrator_factory
        self._recovery_factory = recovery_factory
        self._thread: threading.Thread | None = None
        self._orchestrator: object | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        await self._recovery_factory().run()          # type: ignore[attr-defined]
        self._orchestrator = self._orchestrator_factory(
            command_q=self.command_q, priority_q=self.priority_q,
            event_q=self.event_q,
        )
        await self._orchestrator.run()                # type: ignore[attr-defined]

    def send(self, command: Command) -> None:
        self.command_q.put(command)

    def send_priority(self, command: PriorityCommand) -> None:
        """긴급 명령만 받는다.

        일반 명령이 들어가면 우선순위 보장이 무의미해진다 — Task 1 이 자격을
        타입으로 표현했으므로 여기서 단정할 수 있다.
        """
        if not isinstance(command, PriorityCommand):
            raise TypeError(
                f"priority_q accepts PriorityCommand only, got "
                f"{type(command).__name__}"
            )
        self.priority_q.put(command)

    def drain_events(self) -> list[Event]:
        """GUI 가 `root.after(200ms)` 마다 부른다."""
        out: list[Event] = []
        while True:
            try:
                out.append(self.event_q.get_nowait())
            except queue.Empty:
                return out

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
```

`src/autotrading7s/cli.py`:

```python
"""headless 기동 — 설계서 14.4절, 16절.

GUI 없이 엔진만 돌린다. EC2 에서 자동 테스트할 수 있는 경로가 이것뿐이다
(설계서 18.1 리스크 7).

**키움 어댑터가 없다는 사실을 숨기지 않는다.** 조용히 시뮬레이션으로 대체하면
사용자가 실전이라고 믿는 채로 가짜 브로커에 주문을 낸다.
"""

from __future__ import annotations

import argparse
import asyncio
import queue
import sys
from datetime import UTC, datetime
from pathlib import Path

from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.app.settings import load_settings
from autotrading7s.engine.orchestrator import Orchestrator
from autotrading7s.engine.recovery import Recovery

_DB_PATHS = {
    "mock": Path("data/mock/autotrading7s.db"),
    "real": Path("data/real/autotrading7s.db"),
}


def db_path_for(env: str) -> Path:
    """D15 — 모의투자와 실전의 DB 파일이 절대 섞이지 않는다.

    한 파일을 공유하면 모의투자의 체결 기록이 실전 사이클의 목표가 계산에
    섞여 들어갈 수 있다.
    """
    try:
        return _DB_PATHS[env]
    except KeyError:
        raise ValueError(
            f"env must be one of {sorted(_DB_PATHS)}: {env!r}"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autotrading7s.cli")
    parser.add_argument("--env", choices=sorted(_DB_PATHS), required=True)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=None,
                        help="DB 경로 재지정 (기본값은 --env 가 정한다)")
    parser.add_argument("--simulate", default=None,
                        help="쉼표로 구분한 가격 스크립트. 시뮬레이션 브로커로 "
                             "기동한다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.settings)
    db = args.db if args.db is not None else db_path_for(args.env)
    db.parent.mkdir(parents=True, exist_ok=True)

    if args.simulate is None:
        print(
            "키움 어댑터가 아직 구현되지 않았습니다 (Plan 3). 지금은 "
            "--simulate 로 시뮬레이션 브로커만 기동할 수 있습니다.",
            file=sys.stderr,
        )
        return 2

    # 시뮬레이션 브로커·시계는 어댑터이므로 여기(app 층)에서만 import 한다 —
    # engine/ 은 adapters/ 를 알지 못한다.
    from autotrading7s.adapters.fake.broker import FakeBroker
    from autotrading7s.adapters.fake.clock import FakeClock

    script = [int(p) for p in args.simulate.split(",")]
    conn = connect(db)
    apply_schema(conn)
    repo = SqliteRepository(conn)
    broker = FakeBroker(script, validate_account=True)
    clock = FakeClock(current=datetime.now(UTC))
    event_q: queue.Queue = queue.Queue()

    async def run() -> None:
        await Recovery(repo=repo, broker=broker, clock=clock,
                       emit=event_q.put).run()
        await Orchestrator(
            repo=repo, broker=broker, clock=clock, settings=settings,
            command_q=queue.Queue(), priority_q=queue.Queue(),
            event_q=event_q,
        ).run()

    asyncio.run(run())
    return 0
```

- [ ] **Step 3~5: 통과 확인 → 전체 회귀 → 커밋**

```bash
git add src/autotrading7s/app/engine_thread.py src/autotrading7s/cli.py tests/app/test_engine_thread.py tests/app/test_cli.py
git commit -m "$(printf 'feat: 스레드 브리지와 headless 기동\n\n브리지가 큐를 소유하고 GUI 는 send·send_priority·drain_events 만 쓴다. 그\n규칙이 리포지토리의 단일 작성자 전제를 성립시킨다(2A 핸드오버 3).\n\nsend_priority 가 PriorityCommand 만 받는다 — 일반 명령이 들어가면 우선순위\n보장이 무의미해진다.\n\n복구가 오케스트레이터보다 먼저 돈다. 미체결 주문이 정정되지 않은 상태에서\n판정하면 그 단계가 WAITING 으로 보여 중복 발주가 된다.\n\ncli 는 키움 어댑터가 없다는 사실을 숨기지 않고 오류로 알린다 — 조용히\n시뮬레이션으로 대체하면 사용자가 실전이라고 믿는 채로 가짜 브로커에 주문을\n낸다. DB 경로 분리(D15)는 지금 확정했다.')"
```

---

## Task 13: G2 게이트 (설계서 15.2절)

**G2 가 G3 보다 넓다는 점이 중요하다.** 모의투자로는 갭하락을 만들 수 없고 응답 타임아웃을 유발할 수 없다. **실패 경로는 `FakeBroker` 에서만 체계적으로 검증된다.**

**Ruling: 게이트는 `max_orders_per_minute=60` 으로 돈다.** `FakeBroker` 의 틱 간격이 1초로 고정되어 있어 전 사이클 14건의 주문이 14초 안에 나가지만, 실전에서 그 14틱은 몇 시간에 걸쳐 온다. 빈도 제한 자체는 Task 2 의 테스트가 경계값까지 검증한다. 틀렸을 경우 비용: 없음.

**Ruling: 기대 실현손익을 손으로 적지 않고 도메인 함수로 계산한다.** Plan 2A 에서 절대 숫자를 적어 여섯 번 틀렸고, 그 근본 원인은 "계산 결과를 문서에 박아두면 정확성을 유지할 수 없다" 는 것이었다. 게이트는 `ladder.trigger_price(n)`·`ladder.planned_qty(n)`·`target_price(...)` 로 기댓값을 만든다 — 엔진이 지정가와 수량을 어디서 가져오는지를 여전히 검증하며(엔진은 틱과 단계 기록에서 가져오고 테스트는 사다리에서 가져온다), 산술 실수의 여지가 없다.

**Files:**
- Create: `tests/test_g2_gate.py`
- Modify: `README.md`

- [ ] **Step 1: 게이트를 쓴다**

`tests/test_g2_gate.py` — 설계서 15.2절의 13개 항목에 각각 하나씩, 그리고 D20 하나.

```python
"""G2 게이트 — 설계서 15.2절.

G1 이 도메인 계약의 조합을, G2a 가 영속성 계약의 조합을 검증했듯, 이 게이트는
**엔진의 조합**을 검증한다. 12건의 시나리오 중 대부분은 모의투자 계좌로
재현할 수 없다 — 갭하락을 주문해서 만들 수 없고, 응답 타임아웃을 유발할 수
없고, WebSocket 을 끊었다 붙일 수도 없다. 그래서 이 게이트가 G3 보다 넓다.
"""

from __future__ import annotations

import queue
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from autotrading7s.adapters.fake.broker import (
    FailMode,
    FakeBroker,
    FillMode,
)
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.app.commands import EmergencyLiquidate, ForceClose
from autotrading7s.app.events import (
    CycleClosed,
    GuardBlocked,
    QuoteFallback,
    ReconcileMismatch,
)
from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.ladder import target_price
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    StageStatus,
    Tick,
    TickSource,
)
from autotrading7s.engine.orchestrator import Orchestrator
from autotrading7s.engine.recovery import Recovery
from autotrading7s.ports.repository import SplitConfig

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
ANCHOR = 10_000


def _repo(tmp_path, *, allow_rebuy=False, amount=1_000_000, stages=7,
          limit=99_999_999):
    conn = connect(tmp_path / "g2.db")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    repo.save_config(SplitConfig(
        config_id=None, stock_code="005930", stock_name="삼성전자", label=None,
        max_stages=stages, drop_pct=Decimal("0.05"),
        target_pct=Decimal("0.05"), amount_per_stage=amount,
        allow_rebuy=allow_rebuy, rebuy_cooldown_sec=60, total_limit=limit,
        status="IDLE", created_at=AT, updated_at=AT,
    ))
    return repo


def _engine(repo, broker, *, total_limit=99_999_999, max_orders=60,
            clock=None):
    clock = clock or FakeClock(current=AT)
    qs = (queue.Queue(), queue.Queue(), queue.Queue())

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    orch = Orchestrator(
        repo=repo, broker=broker, clock=clock,
        settings=EngineSettings(total_limit=total_limit,
                                max_orders_per_minute=max_orders),
        command_q=qs[0], priority_q=qs[1], event_q=qs[2], sleep=sleep,
        max_fallback_rounds=1,
    )
    return orch, clock, qs


def _start(repo, orch):
    """설정을 시작시켜 STARTING 사이클을 만든다."""
    from autotrading7s.app.commands import StartCycle
    orch._command_q.put(StartCycle(config_id=1))
    return orch


def _events(event_q):
    out = []
    while not event_q.empty():
        out.append(event_q.get_nowait())
    return out


def _ladder(repo):
    return repo.load_config(1).to_ladder(anchor_price=ANCHOR)


# ══ 1. 7단계 전 사이클 ═══════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_full_seven_stage_cycle(tmp_path):
    """하락 → 단계별 매수 → 반등 → 단계별 매도 → 보유 0 → IDLE.

    기대 실현손익을 손으로 적지 않고 사다리에서 계산한다. 엔진은 지정가를
    틱에서, 수량을 단계 기록에서 가져오고 테스트는 사다리에서 가져오므로,
    둘이 일치하는 것은 여전히 실질적인 검증이다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    downs = [ladder.trigger_price(n) for n in range(1, 8)]
    ups = [target_price(ladder.trigger_price(n), Decimal("0.05"))
           for n in range(7, 0, -1)]
    script = [ANCHOR, *downs, *ups]

    broker = FakeBroker(script, validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _engine(repo, broker)
    _start(repo, orch)

    await orch.run()

    cycles = repo._conn.execute("SELECT id, status, close_reason, realized_pnl "
                                "FROM cycle").fetchall()
    assert len(cycles) == 1
    row = dict(cycles[0])
    assert row["status"] == "CLOSED"
    assert row["close_reason"] == "NORMAL"

    expected = sum(
        (target_price(ladder.trigger_price(n), Decimal("0.05"))
         - ladder.trigger_price(n)) * ladder.planned_qty(n)
        for n in range(1, 8)
    )
    assert row["realized_pnl"] == expected
    assert repo.load_config(1).status == "IDLE"
    assert repo.holdings() == []

    orders = repo._conn.execute(
        "SELECT side, req_qty FROM order_log ORDER BY id"
    ).fetchall()
    sides = [dict(o)["side"] for o in orders]
    assert sides == ["BUY"] * 7 + ["SELL"] * 7
    assert [dict(o)["req_qty"] for o in orders][:7] == [
        ladder.planned_qty(n) for n in range(1, 8)
    ]
    closed = [e for e in _events(event_q) if isinstance(e, CycleClosed)]
    assert len(closed) == 1 and closed[0].realized_pnl == expected


# ══ 2. 갭하락 3단계 동시 통과 → 틱별 순차 매수 ═══════════════════════════
@pytest.mark.asyncio
async def test_g2_gap_down_buys_one_stage_per_tick(tmp_path):
    """규칙 2 — 갭하락으로 3단계가 한꺼번에 통과해도 한 틱에 하나만 산다.

    모의투자로는 갭하락을 주문해서 만들 수 없다. 이 시나리오가 FakeBroker
    에서만 검증되는 이유다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    gap = ladder.trigger_price(4)          # 4단계까지 한 번에 통과하는 가격
    broker = FakeBroker([ANCHOR, gap, gap, gap, gap], validate_account=True,
                        cash=100_000_000)
    orch, clock, (_, _, event_q) = _engine(repo, broker)
    _start(repo, orch)

    await orch.run()

    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    holding = [s.stage_no for s in repo.load_stages(cyc)
               if s.status is StageStatus.HOLDING]
    assert holding == [1, 2, 3, 4]
    # 틱 4개에 매수 4건 — 한 틱에 두 건이 나가지 않았다
    buys = repo._conn.execute(
        "SELECT count(*) c FROM order_log WHERE side = 'BUY'"
    ).fetchone()["c"]
    assert buys == 4


# ══ 3. 매도·매수 동시 충족 → 매도 우선 ═══════════════════════════════════
@pytest.mark.asyncio
async def test_g2_sell_wins_when_both_trigger(tmp_path):
    """규칙 1 — 매도가 하나라도 있으면 그 틱은 매도만 집행한다.

    **진짜 동시 충족 상황을 만들어야 한다.** 3단계를 9,000원에 보유하고
    1·2단계가 대기 중이면, 3단계의 목표가 9,450원은 1단계 발동가(10,000)와
    2단계 발동가(9,500)보다 낮다 — 그 가격 한 틱에서 매도 조건과 매수 조건이
    함께 성립한다.

    그 상태는 틱으로 만들 수 없다(규칙 2 가 낮은 번호부터 사므로). 그래서
    단계 상태를 직접 시드한다. 규칙 1 이 없으면 이 틱에서 1단계 매수도 함께
    나가고, 그것이 세븐스플릿에서 가장 나쁜 순서다 — 반등 중에 물타기가
    일어난다.
    """
    from autotrading7s.domain import stage as stage_mod

    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    cyc = repo.create_cycle(1, AT)
    cyc = cycle_mod.confirm_anchor(cycle_mod.start(cyc, at=AT),
                                   anchor_price=ANCHOR, ladder=ladder, at=AT)
    repo.save_cycle(cyc)
    repo.set_config_status(1, "ACTIVE", at=AT)
    for n in range(1, 8):
        st = stage_mod.StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=ladder.trigger_price(n),
            planned_qty=ladder.planned_qty(n),
        )
        if n == 3:
            st = stage_mod.to_holding(stage_mod.to_buy_pending(st),
                                      fill_price=ladder.trigger_price(3),
                                      fill_qty=ladder.planned_qty(3), at=AT)
        repo.save_stage(cyc.cycle_id, st)

    target3 = target_price(ladder.trigger_price(3), Decimal("0.05"))
    assert target3 < ladder.trigger_price(2), "동시 충족 상황이 아니다"

    broker = FakeBroker([target3], validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _engine(repo, broker)
    await orch.on_tick(Tick(code="005930", price=target3,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))

    sides = [dict(o)["side"] for o in repo._conn.execute(
        "SELECT side FROM order_log ORDER BY id").fetchall()]
    assert sides == ["SELL"]


# ══ 4. 재매수 쿨다운 ════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_rebuy_cooldown(tmp_path):
    """규칙 3 — 60초 안의 재매수는 막히고, 지나면 다시 산다.

    쿨다운이 없으면 같은 단계가 수수료를 태우며 분당 수십 번 회전한다.
    """
    repo = _repo(tmp_path, allow_rebuy=True)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    target = target_price(trigger, Decimal("0.05"))
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (_, _, event_q) = _engine(repo, broker, clock=clock)
    _start(repo, orch)
    await orch.drain_commands()

    def tick(price, at):
        return Tick(code="005930", price=price, at=at, source=TickSource.WS)

    await orch.on_tick(tick(ANCHOR, AT))                     # 앵커 확정
    await orch.on_tick(tick(trigger, AT + timedelta(seconds=1)))
    await orch.poll_pending()
    await orch.on_tick(tick(target, AT + timedelta(seconds=2)))
    await orch.poll_pending()
    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    assert repo.load_stages(cyc)[0].status is StageStatus.WAITING

    # 쿨다운 안 — 사지 않는다
    clock.advance(30)
    await orch.on_tick(tick(trigger, AT + timedelta(seconds=32)))
    assert repo.load_stages(cyc)[0].status is StageStatus.WAITING

    # 쿨다운 경과 — 다시 산다
    clock.advance(31)
    await orch.on_tick(tick(trigger, AT + timedelta(seconds=63)))
    await orch.poll_pending()
    assert repo.load_stages(cyc)[0].status is StageStatus.HOLDING


# ══ 5. 미체결 3초 타임아웃 → 취소 → 재시도 ══════════════════════════════
@pytest.mark.asyncio
async def test_g2_pending_timeout_cancels_and_retries(tmp_path):
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, _ = _engine(repo, broker, clock=clock)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))
    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    assert repo.load_stages(cyc)[0].status is StageStatus.BUY_PENDING

    clock.advance(3.0)
    await orch.poll_pending()
    assert repo.load_stages(cyc)[0].status is StageStatus.WAITING

    # 다음 틱에 재시도된다
    broker._fill_mode = FillMode.INSTANT
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=5),
                            source=TickSource.WS))
    await orch.poll_pending()
    assert repo.load_stages(cyc)[0].status is StageStatus.HOLDING


# ══ 6. 부분체결 매수·매도 비대칭 ════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_partial_fill_asymmetry(tmp_path):
    """매수 부분체결은 보유를 만들고, 매도 부분체결은 보유를 줄인다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    planned = ladder.planned_qty(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"), validate_account=True,
                        cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, _ = _engine(repo, broker, clock=clock)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))
    clock.advance(3.0)
    await orch.poll_pending()

    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    bought = repo.load_stages(cyc)[0]
    assert bought.status is StageStatus.HOLDING
    assert bought.fill_qty == int(planned * Decimal("0.4"))

    target = target_price(bought.fill_price, Decimal("0.05"))
    await orch.on_tick(Tick(code="005930", price=target,
                            at=AT + timedelta(seconds=5),
                            source=TickSource.WS))
    clock.advance(3.0)
    await orch.poll_pending()
    sold_partly = repo.load_stages(cyc)[0]
    assert sold_partly.status is StageStatus.HOLDING       # 잔량이 보유로 복귀
    assert sold_partly.fill_qty < bought.fill_qty
    assert sold_partly.fill_price == bought.fill_price     # 취득원가 불변


# ══ 7. 응답 타임아웃 → 조회 확인 → 중복 발주 없음 ═══════════════════════
@pytest.mark.asyncio
async def test_g2_response_timeout_does_not_duplicate(tmp_path):
    """D12 — **이 시스템에서 가장 중요한 분기다.**

    모의투자로는 응답 타임아웃을 유발할 수 없다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000,
                        fail_mode=FailMode.TIMEOUT)
    orch, clock, (_, _, event_q) = _engine(repo, broker)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))

    assert len(await broker.list_orders_today("005930")) == 1
    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    assert repo.load_stages(cyc)[0].status is StageStatus.BUY_PENDING
    assert [r.status for r in repo.load_pending_orders()] == ["ACCEPTED"]


# ══ 8. 명시적 거부 → 상태 복구 ══════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_rejection_restores_state(tmp_path):
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000,
                        fail_mode=FailMode.REJECT, fail_after=0)
    orch, clock, (_, _, event_q) = _engine(repo, broker)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))

    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    assert repo.load_stages(cyc)[0].status is StageStatus.WAITING
    assert repo.load_pending_orders() == []


# ══ 9. WS 끊김 → REST 폴백 → 재연결 ════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_websocket_drop_falls_back_and_keeps_deciding(tmp_path):
    """설계서 8.4절 — 끊겨도 트리거 판정은 계속 수행한다.

    모의투자로는 WebSocket 을 끊었다 붙일 수 없다.
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    script = [ANCHOR, ladder.trigger_price(1), ladder.trigger_price(2)]
    broker = FakeBroker(script, validate_account=True, cash=100_000_000,
                        fail_mode=FailMode.DISCONNECT, fail_after=1)
    orch, clock, (_, _, event_q) = _engine(repo, broker)
    _start(repo, orch)

    await orch.run()

    fallbacks = [e for e in _events(event_q) if isinstance(e, QuoteFallback)]
    assert fallbacks and fallbacks[0].active is True
    # 폴백 구간의 틱이 REST_POLL 로 기록됐고 주문이 나갔다
    sources = {dict(r)["tick_source"] for r in repo._conn.execute(
        "SELECT tick_source FROM order_log").fetchall()}
    assert "REST_POLL" in sources


# ══ 10. 대사 불일치 → 자동 PAUSED ═══════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_reconcile_mismatch_pauses(tmp_path):
    """D13 — 자동 보정하지 않고 멈춘다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _engine(repo, broker)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))
    await orch.poll_pending()

    # 사용자가 증권사 앱에서 직접 절반을 팔았다
    held, cost = broker._positions["005930"]
    broker._positions["005930"] = (held // 2, cost // 2)

    await orch.reconcile()

    mismatches = [e for e in _events(event_q)
                  if isinstance(e, ReconcileMismatch)]
    assert [e.verdict for e in mismatches] == ["INTERNAL_MORE"]
    assert mismatches[0].action_taken == "PAUSED"
    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    assert repo.load_cycle(cyc).status is CycleStatus.PAUSED
    # 설정은 ACTIVE 로 남는다 — 일시정지는 사이클의 상태다 (원장 Ruling 1)
    assert repo.load_config(1).status == "ACTIVE"


# ══ 11. 프로세스 강제 종료 후 재시작 복구 ═══════════════════════════════
@pytest.mark.asyncio
async def test_g2_restart_recovery(tmp_path):
    """설계서 10.1절 — 발주 직후 죽었고 그 사이에 체결됐다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, _ = _engine(repo, broker)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))
    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    order_id = repo.load_pending_orders()[0].broker_order_id
    broker._fill(broker._orders[order_id], ladder.planned_qty(1))   # 죽은 동안 체결

    # 재시작
    events: list[object] = []
    report = await Recovery(repo=repo, broker=broker,
                            clock=FakeClock(current=AT),
                            emit=events.append).run()

    assert report.resolved_orders == 1
    stage = repo.load_stages(cyc)[0]
    assert stage.status is StageStatus.HOLDING
    assert stage.fill_qty == ladder.planned_qty(1)
    assert report.subscribe_codes == ("005930",)


# ══ 12. 긴급청산 ════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_emergency_liquidation(tmp_path):
    """미체결 취소·실계좌 수량 사용·장외 거부를 한 시나리오에서 확인한다."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (_, priority_q, event_q) = _engine(repo, broker, clock=clock)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))
    await orch.poll_pending()
    # 하위 단계 매수 주문을 미체결로 남긴다
    broker._fill_mode = FillMode.NEVER
    await orch.on_tick(Tick(code="005930", price=ladder.trigger_price(2),
                            at=AT + timedelta(seconds=2),
                            source=TickSource.WS))
    assert len(repo.load_pending_orders()) == 1

    # 장외 요청은 거부된다 (11.3절)
    clock.set_market_open(False)
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="오작동 의심",
                                      confirmed_text=None))
    await orch.drain_commands()
    assert repo._conn.execute(
        "SELECT result FROM emergency_liquidation_log ORDER BY id"
    ).fetchall()[-1]["result"] == "REJECTED_CLOSED_MARKET"

    # 장중 요청은 실행된다
    clock.set_market_open(True)
    broker._fill_mode = FillMode.INSTANT
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="오작동 의심",
                                      confirmed_text=None))
    await orch.drain_commands()

    log = repo._conn.execute(
        "SELECT result, qty_before, qty_after, canceled_orders "
        "FROM emergency_liquidation_log ORDER BY id"
    ).fetchall()[-1]
    assert dict(log)["result"] == "SUCCESS"
    assert dict(log)["canceled_orders"] == 1
    assert dict(log)["qty_before"] == ladder.planned_qty(1)
    assert dict(log)["qty_after"] == 0
    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    assert repo.load_cycle(cyc).close_reason is CloseReason.EMERGENCY
    assert repo.holdings() == []
    assert repo.load_config(1).status == "IDLE"


# ══ 13. 총한도 도달 시 매수 중단 ════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_total_limit_stops_buying(tmp_path):
    """설계서 6절 — **손절매가 없으므로 이것이 유일한 구조적 보호장치다.**

    브로커 검증을 켠 채로 돌린다. 끄면 한도를 넘긴 매수도 조용히 체결되어
    이 테스트가 아무것도 검증하지 않는다 (2A 핸드오버 4).
    """
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    # 2단계까지만 들어가는 한도
    limit = (ladder.trigger_price(1) * ladder.planned_qty(1)
             + ladder.trigger_price(2) * ladder.planned_qty(2))
    script = [ANCHOR] + [ladder.trigger_price(n) for n in range(1, 8)]
    broker = FakeBroker(script, validate_account=True, cash=100_000_000)
    orch, clock, (_, _, event_q) = _engine(repo, broker, total_limit=limit)
    _start(repo, orch)

    await orch.run()

    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    holding = [s.stage_no for s in repo.load_stages(cyc)
               if s.status is StageStatus.HOLDING]
    assert holding == [1, 2]
    blocked = [e for e in _events(event_q) if isinstance(e, GuardBlocked)]
    assert blocked and all("총한도" in e.reason for e in blocked)


# ══ D20. 강제 종료 ══════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_g2_forced_close_when_liquidation_cannot_finish(tmp_path):
    """설계서 11.4절 — 거래정지로 청산이 끝까지 가지 못하는 경우."""
    repo = _repo(tmp_path)
    ladder = _ladder(repo)
    trigger = ladder.trigger_price(1)
    broker = FakeBroker([ANCHOR], validate_account=True, cash=100_000_000)
    clock = FakeClock(current=AT)
    orch, clock, (_, priority_q, event_q) = _engine(repo, broker, clock=clock)
    _start(repo, orch)
    await orch.drain_commands()
    await orch.on_tick(Tick(code="005930", price=ANCHOR, at=AT,
                            source=TickSource.WS))
    await orch.on_tick(Tick(code="005930", price=trigger,
                            at=AT + timedelta(seconds=1),
                            source=TickSource.WS))
    await orch.poll_pending()
    qty = ladder.planned_qty(1)

    # 거래정지 — 시장가 매도가 거부된다
    from autotrading7s.ports.broker import BrokerRejected

    async def halted(req):
        raise BrokerRejected("40510", "거래정지")

    broker.place_market_sell = halted        # type: ignore[method-assign]
    priority_q.put(EmergencyLiquidate(scope="SINGLE", config_id=1,
                                      reason="오작동 의심",
                                      confirmed_text=None))
    await orch.drain_commands()
    cyc = repo._conn.execute("SELECT id FROM cycle").fetchone()["id"]
    assert repo.load_cycle(cyc).status is CycleStatus.LIQUIDATING

    # 사용자가 증언과 함께 강제 종료한다
    priority_q.put(ForceClose(config_id=1,
                              reason=f"거래정지로 청산 불가, 잔량 {qty}주는 "
                                     f"직접 처리 예정",
                              confirmed_text="강제종료"))
    await orch.drain_commands()

    closed = repo.load_cycle(cyc)
    assert closed.status is CycleStatus.CLOSED
    assert closed.close_reason is CloseReason.FORCED
    assert closed.forced_close_qty == qty
    assert "거래정지" in closed.forced_close_reason
    assert repo.holdings() == []
    assert repo.load_config(1).status == "IDLE"
    # 대사 기준선에 그 수량이 반영되어 이후 대사가 조용하다
    assert repo.forced_close_baseline("005930") == qty
    await orch.reconcile()
    assert not [e for e in _events(event_q)
                if isinstance(e, ReconcileMismatch)]


# ══ 의존 방향과 게이트 자신의 전제 ══════════════════════════════════════
def test_engine_and_app_do_not_import_adapters():
    """설계서 7.2절 — 화살표는 항상 안쪽을 향한다.

    `cli.py` 는 예외다 — 조립 지점이므로 구체 어댑터를 알아야 한다.
    """
    import ast
    import pathlib

    root = pathlib.Path("src/autotrading7s")
    offenders: list[str] = []
    for path in list((root / "engine").rglob("*.py")) + \
                list((root / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue          # 상대 import 는 같은 패키지 안이다
                names = [node.module or ""]
            for name in names:
                if "autotrading7s.adapters" in name:
                    offenders.append(f"{path}: {name}")
    assert offenders == []


def test_gate_runs_with_broker_validation_enabled():
    """게이트 자신의 전제를 게이트가 단정한다 (2A 핸드오버 4).

    `validate_account` 를 끄면 한도를 넘긴 매수와 없는 포지션의 매도가 조용히
    통과하고, 시나리오 12·13 이 아무것도 검증하지 않게 된다. 그 사실이 이
    파일에서 조용히 사라지지 않도록 소스에서 직접 확인한다.
    """
    import pathlib

    source = pathlib.Path("tests/test_g2_gate.py").read_text(encoding="utf-8")
    # 이 테스트 자신의 문자열 리터럴이 양쪽 계수에 각각 1 을 더하므로 균형이
    # 유지된다. 정규식으로 인자 목록을 파싱하는 것보다 튼튼하다.
    assert source.count("FakeBroker(") > 10, "FakeBroker 생성이 거의 없다"
    assert source.count("FakeBroker(") == source.count("validate_account=True")
```

- [ ] **Step 2: 게이트를 돌린다**

Run: `.venv/bin/python -m pytest tests/test_g2_gate.py -q`
Expected: PASS. 실패하는 시나리오가 있으면 **게이트를 고치지 말고 엔진을 고친다** — 게이트는 설계서 15.2절을 그대로 옮긴 것이다. 단, 시세 스크립트가 의도한 상황을 만들지 못했다면 그것은 게이트의 결함이며 스크립트를 고친다(그 경우 왜 그런지 커밋 메시지에 남긴다).

- [ ] **Step 3: 전체 회귀와 커버리지**

Run: `.venv/bin/python -m pytest -q --cov --cov-report=term-missing`
Expected: PASS, `autotrading7s.engine`·`autotrading7s.app` 커버리지 90% 이상, 전체 95% 이상.

- [ ] **Step 4: README 갱신**

`README.md` 의 상태 절을 갱신한다.

```markdown
**Plan 2B (엔진 + G2) 완료.** 시뮬레이션 브로커로 7단계 전 사이클과 설계서
15.2절의 실패 경로 12건이 검증된다 — 갭하락 순차 매수, 응답 타임아웃 후
중복 발주 없음, WebSocket 끊김 시 REST 폴백, 대사 불일치 자동 정지, 재시작
복구, 긴급청산과 D20 강제 종료, 총한도 도달 시 매수 중단.

미구현: 키움 어댑터(Plan 3), GUI(Plan 4).
```

- [ ] **Step 5: 커밋**

```bash
git add tests/test_g2_gate.py README.md
git commit -m "$(printf 'test: G2 게이트 — 엔진 조합과 실패 경로 12건\n\nG1 이 도메인 계약의 조합을, G2a 가 영속성 계약의 조합을 검증했듯 이 게이트는\n엔진의 조합을 검증한다. 시나리오 대부분은 모의투자로 재현할 수 없다 — 갭하락을\n주문해서 만들 수 없고, 응답 타임아웃을 유발할 수 없고, WebSocket 을 끊었다\n붙일 수도 없다. 그래서 G2 가 G3 보다 넓다.\n\n기대 실현손익을 손으로 적지 않고 사다리에서 계산한다. Plan 2A 에서 절대 숫자를\n적어 여섯 번 틀렸고, 근본 원인은 계산 결과를 문서에 박아두면 정확성을 유지할 수\n없다는 것이었다. 엔진은 지정가를 틱에서·수량을 단계 기록에서 가져오고 테스트는\n사다리에서 가져오므로 일치는 여전히 실질적 검증이다.\n\n게이트가 자기 전제를 단정한다: 모든 FakeBroker 생성에 validate_account=True 가\n있는지 소스에서 확인한다. 끄면 한도를 넘긴 매수와 없는 포지션의 매도가 조용히\n통과하고 시나리오 12·13 이 아무것도 검증하지 않는다(2A 핸드오버 4).')"
```

---

## G2 게이트 통과 기준

Plan 2B 완료 시 다음이 모두 통과해야 한다.

- [ ] 7단계 전 사이클이 완주하고 실현손익이 사다리에서 계산한 기댓값과 정확히 일치한다
- [ ] 갭하락에서 한 틱에 한 단계만 매수한다 (규칙 2)
- [ ] 매도·매수 동시 충족 시 그 틱은 매도만 집행한다 (규칙 1)
- [ ] 재매수 쿨다운이 경계에서 동작한다 (규칙 3)
- [ ] 미체결 3초 타임아웃 → 취소 → 다음 틱 재시도
- [ ] 매수 부분체결은 보유를 만들고, 매도 부분체결은 보유를 줄인다
- [ ] 응답 타임아웃 후 조회로 확인하며 **주문이 정확히 하나다** (D12)
- [ ] 명시적 거부에서 단계가 WAITING 으로 복구된다
- [ ] WebSocket 끊김 시 REST 폴백으로 넘어가고 **판정이 계속된다** (설계서 8.4절)
- [ ] 대사 불일치(내부 > 실계좌)가 그 종목을 자동 `PAUSED` 로 만든다 (D13)
- [ ] 재시작 복구가 죽은 동안의 체결을 정정하고 구독을 복원한다
- [ ] 긴급청산이 미체결을 먼저 취소하고 실계좌 수량으로 팔며 장외 요청을 거부한다
- [ ] 총한도 도달 시 매수가 중단되고 그 이유가 이벤트로 나간다
- [ ] D20 강제 종료가 증언과 잔량을 기록하고 대사 기준선을 세운다
- [ ] `engine/`·`app/` 이 `adapters/` 를 import 하지 않는다 (`cli.py` 제외)
- [ ] 게이트의 모든 `FakeBroker` 생성에 `validate_account=True` 가 있다
- [ ] `autotrading7s.engine`·`autotrading7s.app` 커버리지 90% 이상, 전체 95% 이상

---

## Plan 2B 이후

**Plan 3 (키움 어댑터 + 인증)** 과 **Plan 4 (GUI)** 로 갈라진다. 두 계획은 파일이 겹치지 않으므로 병행할 수 있다.

- Plan 3: `adapters/kiwoom/`(`RestClient`·`QuoteStream`·`TokenManager`·`endpoints.toml`), `token_session` 접근자(2A 핸드오버 8), 스키마 마이그레이션 단계(2A 핸드오버 5), 설계서 18.2절의 미확정 값 8건 확정. **외부 선행조건: 키움 API 사용승인.**
- Plan 4: `ui/`(`main_window`·`holdings_table`·`stage_detail`·`config_dialog`·`emergency_dialog`·`log_view`). Task 1 의 큐 계약만 소비하며, `app/engine_thread.py` 가 유일한 접점이다.
