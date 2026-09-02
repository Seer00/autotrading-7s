# AutoTrading 7s — Plan 2A: 영속성 + 브로커 포트 (G2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 도메인 객체를 SQLite에 저장·복원하는 리포지토리와, 키움 API 없이 엔진을 검증할 수 있는 시뮬레이션 브로커를 구현하고, Plan 1이 Plan 2로 넘긴 제약 다섯 건을 코드로 강제한다.

**Architecture:** 헥사고날 구조의 어댑터 층. `ports/`에 Protocol을 선언하고 `adapters/sqlite/`와 `adapters/fake/`가 구현한다. 도메인은 이 층을 모른다. 핵심은 SQLite가 아니라 **매핑 계층**이다 — 행과 도메인 객체 사이의 변환이 일어나는 곳이고, Plan 1이 남긴 제약(tz-aware datetime, `Decimal`의 TEXT 왕복, 완전한 단계 집합, `trigger_price` 대조)이 전부 여기서 착륙한다.

**Tech Stack:** Python 3.12, 표준 라이브러리 `sqlite3`, pytest. 런타임 외부 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`

**선행 기록:** `docs/superpowers/records/2026-09-01-plan1-execution-ledger.md` — Plan 1 실행 중 내린 판단 62건과 그 근거. 이 계획이 강제하는 제약들의 출처다.

## Global Constraints

설계서와 Plan 1의 전역 제약. 모든 태스크의 요구사항에 암묵적으로 포함된다.

- **Python 3.12** 이상. `from __future__ import annotations` 를 모듈 docstring 직후 첫 import로 둔다.
- **`domain/` 패키지는 표준 라이브러리 외 어떤 것도 import 하지 않는다.** 이 계획은 `domain/`을 세 곳만 건드린다(Task 1의 `errors.py` 신설과 예외 타입 전환, Task 6의 `CloseReason.FORCED` 멤버 추가) — 그때도 이 규칙을 지킨다. `tests/test_g1_gate.py`의 AST 테스트가 이를 자동 검증한다.
- **`adapters/`는 `ports/`와 `domain/`에 의존하고, 그 반대는 없다.** 화살표는 항상 안쪽을 향한다(설계서 7.2절).
- **금액·가격은 원 단위 `int`, 비율만 `Decimal`.** `float`를 금액 계산에 쓰는 것을 금지한다. SQLite는 `Decimal`을 모르므로 **TEXT로 저장**한다(설계서 12.1절).
- **도메인의 모든 `datetime`은 tz-aware여야 한다.** SQLite TEXT에서 파싱할 때 tzinfo를 잃지 않아야 한다 — Plan 1이 Task 9에서 확인한 실패 모드는 naive와 aware를 빼면 엔진 틱 루프 안에서 `TypeError`가 터지는 것이다.
- **앱키·시크릿·접근토큰은 DB에 저장하지 않는다.** `token_session` 테이블은 `env`, `app_key_hash`, 발급·만료시각만 담는다(설계서 13.1절). 이 계획은 토큰을 다루지 않지만 스키마는 그 형태로 만든다.
- **모의/실전 DB 파일 분리**: `data/mock/autotrading7s.db`, `data/real/autotrading7s.db`(설계서 13.2절). 리포지토리는 경로를 받고 환경을 스스로 정하지 않는다.
- 커밋 메시지는 한국어 본문 + Conventional Commits 접두어.

## Plan 1이 넘긴 제약 — 이 계획이 강제해야 하는 것

원장의 handover 9건 중 이 계획의 범위에 속하는 다섯 건. 각 항목에 담당 태스크를 적었다.

| # | 제약 | 출처 | 담당 |
|---|---|---|---|
| H1 | 복원된 행의 정합성 실패와 호출자 버그가 둘 다 맨 `ValueError`다. `DomainInvariantError(ValueError)`를 도입해 구분해야 한다 | Plan 1 최종 리뷰 handover 8 | Task 1 |
| H2 | 도메인의 모든 `datetime`이 tz-aware여야 한다는 전역 규칙이 타입 수준에 없다. Task 9의 쿨다운에서만 검사한다 | Plan 1 Task 9 판단 | Task 5 |
| H3 | 리포지토리는 사이클의 **단계 집합을 완전하게** 로드해야 한다. 누락 시 `decide()`가 그 단계를 조용히 건너뛰고 사다리 순서가 어긋난다 | Plan 1 Task 7 #4 판단 | Task 7 |
| H4 | `stage_state.trigger_price`를 로드 시점에 `ladder_json`과 대조해야 한다. 설계서 4.2절이 같은 숫자를 두 곳에 쓰지만 스키마가 둘을 묶지 않는다 | Plan 1 최종 리뷰 handover 3 | Task 7 |
| H5 | 실현손익은 도메인에 존재하지 않고 존재할 수 없다. `after_sell`이 `fill_price`·`fill_qty`를 비우므로 `order_log`에서 집계해야 한다 | Plan 1 최종 리뷰 handover 7 | Task 9 |

Plan 2B로 넘기는 handover 네 건은 이 계획의 범위 밖이다: 긴급청산이 `guards.check_sell`을 거치면 안 됨, 한 틱 안에서 guard 컨텍스트를 증가시켜야 함, `Balance.qty_of`가 없는 종목에 0을 반환함, `stage_no > max_stages` 검출.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `src/autotrading7s/domain/errors.py` | `DomainInvariantError(ValueError)` — 도메인 객체의 상태가 무효할 때 |
| `src/autotrading7s/ports/broker.py` | `BrokerPort` Protocol과 `CancelAck` |
| `src/autotrading7s/ports/repository.py` | `RepositoryPort` Protocol |
| `src/autotrading7s/adapters/sqlite/schema.sql` | 8개 테이블 + `holdings` 뷰 (설계서 12절) |
| `src/autotrading7s/adapters/sqlite/migrations.py` | 스키마 적용과 버전 추적 |
| `src/autotrading7s/adapters/sqlite/codec.py` | `Decimal`·`datetime`의 TEXT 왕복 (H2) |
| `src/autotrading7s/adapters/sqlite/mapping.py` | 행 ↔ 도메인 객체 변환, `CorruptRowError` (H1·H3·H4) |
| `src/autotrading7s/ports/repository.py` | `RepositoryPort` + 계약 DTO `SplitConfig`·`HoldingRow` |
| `src/autotrading7s/adapters/sqlite/repository.py` | `SqliteRepository(RepositoryPort)` |
| `src/autotrading7s/adapters/fake/broker.py` | `FakeBroker(BrokerPort)` — 시세 재생, 체결·실패 모드 |
| `tests/adapters/sqlite/*` | 위 각 모듈의 테스트 |
| `tests/adapters/test_fake_broker.py` | 시뮬 브로커 테스트 |
| `tests/test_g2a_gate.py` | G2a 게이트 — 전 도메인 객체 왕복 + H1~H5 검증 |

매핑을 `codec.py`(원시 타입 변환)와 `mapping.py`(도메인 객체 변환)로 나눈 이유는 전자가 후자 없이 단독 테스트 가능하고, `Decimal`·`datetime` 왕복 정확성이 나머지 전부의 전제이기 때문이다.

---

### Task 1: `DomainInvariantError` 도입 — 복원 실패와 호출자 버그를 구분 (H1)

**Files:**
- Create: `src/autotrading7s/domain/errors.py`
- Modify: `src/autotrading7s/domain/types.py`, `stage.py`, `cycle.py`, `rules.py`, `guards.py`, `ladder.py`
- Test: `tests/domain/test_errors.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `DomainInvariantError(ValueError)` — 도메인 객체의 상태가 무효
  - `LadderConfigError(DomainInvariantError)` — 재부모화됨(기존에는 `ValueError` 직속)

**왜 지금인가.** Plan 1 최종 리뷰가 이 타입을 권고했고, 나는 "여덟 타입의 예외 분류를 바꾸는 교차 변경은 절반만 맞으면 안 하는 것보다 나쁘다"는 이유로 미뤘다. 이제 소비자가 생긴다 — 매핑 계층(Task 6·7)은 "이 DB 행이 손상됐다"와 "호출자에 버그가 있다"를 구분해야 하고, 구분하지 못하면 메시지 문자열을 매칭하게 된다.

**전환 대상 — 상태 무효 vs 인자 무효**

`raise ValueError`가 도메인에 32곳 있다. **전부 바꾸는 것이 아니다.** 기준은 "객체의 상태가 무효한가" 대 "호출 인자가 무효한가"다.

`DomainInvariantError`로 바꾸는 것:

| 위치 | 이유 |
|---|---|
| 모든 `__post_init__`의 값 검사 (`types.py`의 `Tick`·`LimitOrderRequest`·`MarketSellRequest`·`Holding`, `stage.py`의 `StageState`, `cycle.py`의 `Cycle`, `rules.py`의 `TriggerParams`·`BuyStage`·`SellStage`, `guards.py`의 `GuardContext`) | 복원된 행이 만드는 실패의 지점 |
| `decide()`의 `stage_no` 중복 거부 | 데이터 오류 |
| `decide()`의 `target_pct` 불일치 거부 | 중복 저장된 설정값의 불일치 |
| `decide()`의 `trigger_price` 불일치 거부 | 같음 |
| `is_cycle_complete([])`의 빈 시퀀스 거부 | Plan 1이 "데이터 정합성 실패"로 명시한 것 |
| `LadderConfigError` | 부모를 `DomainInvariantError`로 바꾼다. 복원된 `ladder_json`이 이것을 낼 수 있다 |

`ValueError`로 남기는 것:

| 위치 | 이유 |
|---|---|
| `normalize_tick`의 비양수 거부 | 호출 인자 검증 |
| `target_price`의 `fill_price` 비양수 거부 | 같음 |
| `to_holding`의 fill 비양수 거부 | 같음 |
| `cancel_sell`의 `remaining_qty` 범위 거부 | 같음 |
| `Ladder._check_stage`의 범위 밖 단계 | 같음 |
| `confirm_anchor`의 앵커 불일치 | 호출자가 짝을 틀린 것 |
| `close()`의 미완료 사이클 거부 | 업무 규칙 거부이며 데이터 손상이 아니다 |
| `decide()`의 `tick.code != stock_code` | 라우팅 버그 |

`TypeError`는 하나도 바꾸지 않는다. 타입 오류는 별도 범주이며 복원 코드도 같은 방식으로 다룰 수 있다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_errors.py`**

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder, LadderConfigError, target_price
from autotrading7s.domain.rules import BuyStage, TriggerParams
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.tick_size import normalize_tick
from autotrading7s.domain.types import Side, StageStatus, Tick, TickSource

FIVE = Decimal("0.05")
T0 = __import__("datetime").datetime(2026, 9, 1, 9, 0,
                                     tzinfo=__import__("datetime").timezone.utc)


def test_domain_invariant_error_is_a_value_error():
    """기존 호출부가 ValueError 를 잡고 있으므로 하위 호환을 유지한다."""
    assert issubclass(DomainInvariantError, ValueError)


def test_ladder_config_error_is_a_domain_invariant_error():
    """복원된 ladder_json 이 이것을 낼 수 있으므로 매핑 계층이 함께 잡아야 한다."""
    assert issubclass(LadderConfigError, DomainInvariantError)


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(
            lambda: Tick(code="005930", price=0, at=T0, source=TickSource.WS),
            id="Tick.price",
        ),
        pytest.param(
            lambda: StageState(stage_no=0, status=StageStatus.WAITING,
                               trigger_price=9_000, planned_qty=111),
            id="StageState.stage_no",
        ),
        pytest.param(
            lambda: TriggerParams(target_pct=Decimal("0")),
            id="TriggerParams.target_pct",
        ),
        pytest.param(
            lambda: BuyStage(stage_no=1, limit_price=0, qty=10, reason="t"),
            id="BuyStage.limit_price",
        ),
    ],
)
def test_post_init_value_failures_raise_domain_invariant_error(make):
    """복원된 행이 만드는 실패는 DomainInvariantError 여야 한다."""
    with pytest.raises(DomainInvariantError):
        make()


def test_ladder_value_failure_raises_ladder_config_error():
    with pytest.raises(LadderConfigError):
        Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=Decimal("0"),
               max_stages=7, amount_per_stage=1_000_000)


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: normalize_tick(Decimal(0), Side.BUY),
                     id="normalize_tick"),
        pytest.param(lambda: target_price(0, FIVE), id="target_price"),
    ],
)
def test_argument_failures_stay_plain_value_error(make):
    """호출 인자 검증은 DomainInvariantError 가 아니다 — 데이터 손상이 아니라 버그다."""
    with pytest.raises(ValueError) as exc:
        make()
    assert not isinstance(exc.value, DomainInvariantError)


def test_type_failures_are_unchanged():
    """TypeError 는 하나도 바꾸지 않는다."""
    with pytest.raises(TypeError):
        Tick(code="005930", price=9340.5, at=T0, source=TickSource.WS)
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인**

Run: `.venv/bin/python -m pytest tests/domain/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.errors'`

- [ ] **Step 3: `src/autotrading7s/domain/errors.py` 작성**

```python
"""도메인 예외.

`DomainInvariantError` 는 "이 도메인 객체의 상태가 무효하다" 를 뜻한다. 호출 인자가
무효한 것(맨 `ValueError`)과 구분하는 이유는 Plan 2 의 매핑 계층이 둘을 다르게
다뤄야 하기 때문이다 — 복원된 행의 정합성 실패는 그 행을 지목하는 `CorruptRowError`
로 감싸 사용자에게 보이고, 호출자 버그는 그대로 올려 개발 중에 드러나게 한다.

`ValueError` 를 상속하는 이유는 하위 호환이다. Plan 1 의 테스트와 호출부가
`ValueError` 를 잡고 있으며, 그 기대를 깨지 않는다.
"""

from __future__ import annotations


class DomainInvariantError(ValueError):
    """도메인 객체의 상태가 무효할 때. 주로 `__post_init__` 이 던진다."""
```

- [ ] **Step 4: 상태 무효 raise를 전환**

위 표의 "`DomainInvariantError`로 바꾸는 것" 열에 해당하는 모든 `raise ValueError(` 를
`raise DomainInvariantError(` 로 바꾼다. 각 파일에 import를 추가한다:

```python
from autotrading7s.domain.errors import DomainInvariantError
```

`ladder.py`의 `LadderConfigError`는 부모를 바꾼다:

```python
from autotrading7s.domain.errors import DomainInvariantError


class LadderConfigError(DomainInvariantError):
    """사다리 설정이 실행 불가능할 때. 설정 등록 시점에 던진다.

    `DomainInvariantError` 를 상속하므로 매핑 계층이 복원된 `ladder_json` 의
    정합성 실패를 다른 도메인 불변식 실패와 같은 방식으로 잡을 수 있다.
    """
```

`_check_int_field`·`_check_fill_field` 같은 공용 헬퍼가 `ValueError`를 던지면 그
헬퍼를 `DomainInvariantError`로 바꾼다 — 그것들은 `__post_init__`에서만 쓰인다.
`tick_size.py`는 `normalize_tick`의 인자 검증뿐이므로 **건드리지 않는다.**

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/domain/test_errors.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: 새 테스트 PASS, 기존 453개도 전부 PASS. `DomainInvariantError`가 `ValueError`의
하위 클래스이므로 `pytest.raises(ValueError)` 기대가 그대로 성립한다. 하나라도 깨지면
전환 대상을 잘못 골랐다는 뜻이므로 멈추고 보고한다.

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/domain/errors.py src/autotrading7s/domain/ tests/domain/test_errors.py
git commit -m "$(printf 'feat: DomainInvariantError 도입 — 복원 실패와 호출자 버그 구분\n\nPlan 1 최종 리뷰의 handover 8. 복원된 행의 정합성 실패와 호출자 버그가 둘 다\n맨 ValueError 여서 Plan 2 의 매핑 계층이 메시지 문자열로 구분해야 할 상황이었다.\n\n상태가 무효한 경우(__post_init__ 의 값 검사, decide() 의 중복·불일치 거부,\nis_cycle_complete 의 빈 시퀀스)만 전환했다. 호출 인자 검증(normalize_tick,\ntarget_price, to_holding, cancel_sell, confirm_anchor, close)은 ValueError 로\n남긴다 — 데이터 손상이 아니라 버그다. TypeError 는 하나도 바꾸지 않았다.\n\nValueError 를 상속하므로 Plan 1 의 453개 테스트가 그대로 통과한다.')"
```

---
### Task 2: 브로커 포트

**Files:**
- Create: `src/autotrading7s/ports/broker.py`
- Modify: `src/autotrading7s/domain/types.py` (`CancelAck` 추가)
- Test: `tests/ports/test_broker.py`, `tests/ports/__init__.py`

**Interfaces:**
- Consumes: `Tick`, `LimitOrderRequest`, `MarketSellRequest`, `OrderAck`, `OrderStatus`, `Balance` (Plan 1의 `domain/types.py`)
- Produces:
  - `CancelAck(broker_order_id: str, canceled_at: datetime)` — frozen, `domain/types.py`에 추가
  - `BrokerPort` Protocol, `@runtime_checkable`, 8개 메서드 (설계서 8.1절)

**설계서 8.1절이 이 포트를 확정한다.** 메서드 목록과 시그니처를 그대로 쓴다. 두
주문 타입이 나뉘어 있는 것(`place_limit_order` / `place_market_sell`)이 핵심이며,
자동 트리거 경로가 시장가를 표현할 수 없게 만드는 구조다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/ports/test_broker.py`**

`mkdir -p tests/ports && touch tests/ports/__init__.py` 를 먼저 실행한다.

```python
from __future__ import annotations

import inspect
from datetime import datetime, timezone

from autotrading7s.domain.types import CancelAck
from autotrading7s.ports.broker import BrokerPort

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def test_cancel_ack_is_frozen():
    import dataclasses

    ack = CancelAck(broker_order_id="X1", canceled_at=T0)
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        ack.broker_order_id = "X2"  # type: ignore[misc]


def test_broker_port_declares_the_eight_methods():
    """설계서 8.1절의 메서드 목록. 하나라도 빠지면 어댑터가 구현을 빼먹는다."""
    expected = {
        "subscribe_quotes", "place_limit_order", "place_market_sell",
        "cancel_order", "get_order", "list_orders_today", "get_balance",
        "get_price",
    }
    declared = {
        name for name, _ in inspect.getmembers(BrokerPort, inspect.isfunction)
        if not name.startswith("_")
    }
    assert declared == expected


def test_broker_port_is_runtime_checkable():
    """어댑터가 포트를 만족하는지 테스트에서 단정할 수 있어야 한다."""

    class Stub:
        def subscribe_quotes(self, codes): ...   # 포트와 같이 일반 def 다
        async def place_limit_order(self, req): ...
        async def place_market_sell(self, req): ...
        async def cancel_order(self, broker_order_id): ...
        async def get_order(self, broker_order_id): ...
        async def list_orders_today(self, code): ...
        async def get_balance(self): ...
        async def get_price(self, code): ...

    assert isinstance(Stub(), BrokerPort)


def test_incomplete_stub_does_not_satisfy_the_port():
    class Missing:
        def subscribe_quotes(self, codes): ...

    assert not isinstance(Missing(), BrokerPort)


def test_subscribe_quotes_is_not_a_coroutine_function():
    """이 결정은 `runtime_checkable` 이 검사하지 않으므로 여기서 고정한다.

    `async def` 로 선언하면 호출이 코루틴을 반환해 호출부가 `async for` 를 바로
    쓸 수 없다. Plan 3 의 키움 어댑터가 이 결정을 어기면 여기서 잡힌다.
    """
    assert not inspect.iscoroutinefunction(BrokerPort.subscribe_quotes)
    for name in ("place_limit_order", "place_market_sell", "cancel_order",
                 "get_order", "list_orders_today", "get_balance", "get_price"):
        assert inspect.iscoroutinefunction(getattr(BrokerPort, name)), name
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/ports/test_broker.py -v`
Expected: FAIL — `ImportError: cannot import name 'CancelAck'`

- [ ] **Step 3: `CancelAck` 를 `domain/types.py` 에 추가**

`OrderAck` 바로 아래에 넣는다.

```python
@dataclass(frozen=True, slots=True)
class CancelAck:
    broker_order_id: str
    canceled_at: datetime
```

- [ ] **Step 4: `src/autotrading7s/ports/broker.py` 작성**

```python
"""브로커 포트 — 설계서 8.1절.

도메인이 증권사를 보는 유일한 창이다. 키움 어댑터(Plan 3)와 시뮬 브로커(Task 11)가
이것을 구현하며, 엔진은 둘을 구분하지 못한다.

주문 요청이 두 타입으로 나뉘어 있는 것이 이 포트의 핵심이다. 자동 트리거 경로는
`LimitOrderRequest` 만 만들 수 있고 그 타입에는 시장가를 표현할 방법이 없다. 시장가는
`MarketSellRequest` 뿐이며 긴급청산 전용이고 `reason` 이 필수다(설계서 8.2절).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from autotrading7s.domain.types import (
    Balance,
    CancelAck,
    LimitOrderRequest,
    MarketSellRequest,
    OrderAck,
    OrderStatus,
    Tick,
)


@runtime_checkable
class BrokerPort(Protocol):
    def subscribe_quotes(self, codes: list[str]) -> AsyncIterator[Tick]:
        """실시간 체결가 스트림. 끊김 시 어댑터가 재연결하고 구독을 복원한다."""
        ...

    async def place_limit_order(self, req: LimitOrderRequest) -> OrderAck:
        """자동 트리거 경로 전용. 지정가만 표현할 수 있다."""
        ...

    async def place_market_sell(self, req: MarketSellRequest) -> OrderAck:
        """긴급청산 전용. `req.reason` 이 필수다."""
        ...

    async def cancel_order(self, broker_order_id: str) -> CancelAck: ...

    async def get_order(self, broker_order_id: str) -> OrderStatus: ...

    async def list_orders_today(self, code: str | None) -> list[OrderStatus]:
        """당일 주문 내역. 설계서 9절의 UNKNOWN 분기가 client_ref 대조에 쓴다."""
        ...

    async def get_balance(self) -> Balance:
        """예수금과 보유종목. 대사(설계서 10.2절)와 긴급청산의 수량 확정에 쓴다."""
        ...

    async def get_price(self, code: str) -> int:
        """WebSocket 끊김 시 REST 폴백(설계서 8.4절)."""
        ...
```

`subscribe_quotes` 만 `async def` 가 아닌 이유: `AsyncIterator` 를 반환하는
비동기 제너레이터 함수는 `async def` 로 정의하면 코루틴을 반환하게 되어 호출부가
`async for` 를 바로 쓸 수 없다. Protocol 에서는 일반 `def` 로 선언하고 구현이
`async def` 제너레이터로 만든다.

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/ports/test_broker.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS. `test_domain_imports_nothing_external` 도 계속 통과해야 한다 —
`ports/` 는 `domain/` 밖이므로 그 테스트의 대상이 아니지만, `types.py` 에 `CancelAck`
를 추가한 것이 새 import 를 들이지 않았음을 확인한다.

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/ports/broker.py src/autotrading7s/domain/types.py tests/ports/
git commit -m "$(printf 'feat: 브로커 포트 선언\n\n설계서 8.1절. 도메인이 증권사를 보는 유일한 창이며 키움 어댑터(Plan 3)와\n시뮬 브로커가 이것을 구현한다.\n\n주문 요청이 두 타입으로 나뉘어 있어 자동 트리거 경로가 시장가를 표현할 수 없다.\n메서드 목록을 집합으로 단정하는 테스트를 두어 어댑터가 구현을 빼먹는 것을 막는다.')"
```

---

### Task 3: 리포지토리 포트

**Files:**
- Create: `src/autotrading7s/ports/repository.py`
- Test: `tests/ports/test_repository.py`

**Interfaces:**
- Consumes: `Cycle`, `StageState`, `CloseReason`, `CycleStatus`, `Ladder` (모두 `domain/`)
- Produces: `RepositoryPort` Protocol(`@runtime_checkable`), `SplitConfig`, `HoldingRow`

**DTO 두 개가 포트와 함께 산다.** `SplitConfig` 와 `HoldingRow` 는 도메인 타입이
아니다 — 설정은 사용자 입력의 저장 형태이고, `HoldingRow` 는 UI 를 위한 읽기 모델이다.
그렇다고 SQLite 어댑터의 것도 아니다: **이 포트의 계약이 그 두 타입으로 쓰여 있다.**
어댑터 층에 두면 포트를 소비하는 모든 코드(Plan 2B 의 `engine/` 포함)가 DTO 하나를
얻으려고 `adapters/sqlite/` 를 import 해야 하고, 그것은 화살표를 거꾸로 돌리는 것이다.

그래서 여기서 정의하고, `adapters/sqlite/mapping.py` 가 **가져다 쓴다**.

포트를 먼저 선언하는 이유는 Task 8~10이 메서드를 하나씩 채워 나가는 동안 "무엇을
채워야 하는가" 의 목록이 고정되어 있어야 하기 때문이다. 실제 저장·복원 **동작**은
Task 8~10이 검사한다 — 이 태스크는 계약의 모양만 고정한다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/ports/test_repository.py`**

```python
from __future__ import annotations

import dataclasses
import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.ladder import Ladder
from autotrading7s.ports.repository import HoldingRow, RepositoryPort, SplitConfig

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_config(**over) -> SplitConfig:
    kw = dict(config_id=None, stock_code="005930", stock_name="삼성전자",
              label="기본", max_stages=7, drop_pct=FIVE, target_pct=FIVE,
              amount_per_stage=1_000_000, allow_rebuy=True,
              rebuy_cooldown_sec=60, total_limit=7_000_000, status="ACTIVE",
              created_at=T0, updated_at=T0)
    return SplitConfig(**{**kw, **over})


def test_repository_port_declares_the_expected_methods():
    """Task 8~10 이 채워야 하는 목록. 여기가 고정되어야 진행 상황을 셀 수 있다."""
    expected = {
        # 설정
        "save_config", "load_config", "list_configs", "set_config_status",
        # 사이클과 단계
        "create_cycle", "load_cycle", "save_cycle", "load_stages", "save_stage",
        "load_active_cycles",
        # 주문 이력과 실현손익
        "append_order_log", "update_order_log", "load_pending_orders",
        "realized_pnl_for_cycle",
        # 긴급청산·대사 이력
        "append_emergency_log", "append_reconcile_log",
        # 보유현황 뷰
        "holdings",
    }
    declared = {
        name for name, _ in inspect.getmembers(RepositoryPort, inspect.isfunction)
        if not name.startswith("_")
    }
    assert declared == expected


def test_repository_port_is_runtime_checkable():
    assert getattr(RepositoryPort, "_is_runtime_protocol", False) is True


def test_split_config_to_ladder_carries_every_field_through():
    """설정의 어느 필드가 사다리로 흘러가는지 고정한다 — 이름을 잘못 짝지으면
    앵커 확정 시점에 조용히 다른 사다리가 만들어진다."""
    lad = a_config().to_ladder(anchor_price=10_000)
    assert lad == Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=FIVE,
                         max_stages=7, amount_per_stage=1_000_000)
    # 1단계는 앵커 그대로, 2단계는 5% 아래(호가 단위로 내림)
    assert lad.trigger_price(1) == 10_000
    assert lad.trigger_price(2) == 9_500


def test_split_config_to_ladder_rejects_an_invalid_anchor():
    """`Ladder` 의 검증이 이 경계에서도 살아 있어야 한다 — 설정이 유효해도
    앵커가 유효하지 않으면 사다리는 만들어지지 않는다."""
    from autotrading7s.domain.ladder import LadderConfigError

    with pytest.raises(LadderConfigError):
        a_config().to_ladder(anchor_price=0)


def test_the_contract_dtos_are_frozen():
    """엔진과 UI 가 같은 객체를 들고 있으므로 변경 불가여야 한다."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        a_config().stock_code = "000660"  # type: ignore[misc]

    row = HoldingRow(stock_code="005930", stock_name="삼성전자", label="기본",
                     cycle_id=1, total_qty=316, avg_price=9_458,
                     holding_stages=3, max_stages=7,
                     cycle_status=CycleStatus.RUNNING)
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.total_qty = 0  # type: ignore[misc]
```

`CycleStatus` import 를 위 블록의 import 목록에 더한다
(`from autotrading7s.domain.types import CycleStatus`).

**`to_ladder` 를 여기서 검사하는 이유.** 그 메서드가 이 태스크의 유일한 **동작**이다
— 나머지는 선언뿐이다. Plan 1 에서 가장 비싼 결함들이 "필드 이름을 잘못 짝지어
조용히 다른 값이 흘러간 것"이었으므로(`to_holding` 이 9,000×111 을 1×1 로 덮어쓴
사례), 필드가 사다리로 옮겨가는 지점을 값으로 못 박는다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/ports/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.ports.repository'`

- [ ] **Step 3: `src/autotrading7s/ports/repository.py` 작성**

```python
"""리포지토리 포트 — 설계서 12절 스키마의 접근면.

SQLite 구현(Task 8~10)이 이것을 만족한다. 엔진(Plan 2B)은 이 포트만 보므로,
저장 방식이 바뀌어도 엔진은 모른다.

메서드가 도메인 객체를 주고받는다 — 행이나 dict 가 아니다. 변환은 어댑터의 매핑
계층이 하며, 그곳이 Plan 1 이 넘긴 제약(완전한 단계 집합, trigger_price 대조,
tz-aware datetime)을 강제하는 지점이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, OrderPath, Side


@dataclass(frozen=True, slots=True)
class SplitConfig:
    """분할 설정 — 설계서 12.1절 `split_config`.

    도메인에는 이 타입이 없다. 설정은 사용자 입력의 저장 형태이고, 도메인이 쓰는
    것은 그것으로 만든 `Ladder` 와 `TriggerParams` 다. 그렇다고 어댑터의 것도
    아니다 — **이 포트의 계약이 이 타입으로 쓰여 있으므로 포트와 함께 산다.**
    SQLite 어댑터든 다른 어떤 구현이든 이것을 가져다 쓴다.
    """

    config_id: int | None
    stock_code: str
    stock_name: str | None
    label: str | None
    max_stages: int
    drop_pct: Decimal
    target_pct: Decimal
    amount_per_stage: int
    allow_rebuy: bool
    rebuy_cooldown_sec: int
    total_limit: int
    status: str
    created_at: datetime
    updated_at: datetime

    def to_ladder(self, anchor_price: int) -> Ladder:
        """앵커가 확정된 뒤 이 설정으로 사다리를 만든다."""
        return Ladder(
            anchor_price=anchor_price,
            drop_pct=self.drop_pct,
            target_pct=self.target_pct,
            max_stages=self.max_stages,
            amount_per_stage=self.amount_per_stage,
        )


@dataclass(frozen=True, slots=True)
class HoldingRow:
    """설계서 12.3절 `holdings` 뷰의 한 행.

    현재가와 평가손익률은 실시간 값이므로 뷰에 없다 — UI 가 최신 틱과 결합해
    `domain/pnl.py` 의 순수 함수로 계산한다.
    """

    stock_code: str
    stock_name: str | None
    label: str | None
    cycle_id: int
    total_qty: int
    avg_price: int
    holding_stages: int
    max_stages: int
    cycle_status: CycleStatus


@runtime_checkable
class RepositoryPort(Protocol):
    # ── 설정 ────────────────────────────────────────────────────────────
    def save_config(self, config: SplitConfig) -> int:
        """새 설정을 저장하고 id 를 반환. 같은 (stock_code, label) 은 UNIQUE 로 거부."""
        ...

    def load_config(self, config_id: int) -> SplitConfig: ...

    def list_configs(self) -> list[SplitConfig]: ...

    def set_config_status(self, config_id: int, status: str) -> None:
        """IDLE | ACTIVE."""
        ...

    # ── 사이클과 단계 ───────────────────────────────────────────────────
    def create_cycle(self, config_id: int, started_at: datetime) -> Cycle:
        """seq 를 자동 증가시켜 STARTING 사이클을 만든다."""
        ...

    def load_cycle(self, cycle_id: int) -> Cycle: ...

    def save_cycle(self, cycle: Cycle) -> None: ...

    def load_active_cycles(self) -> list[Cycle]:
        """CLOSED 가 아닌 사이클. 재시작 복구(Plan 2B)가 쓴다."""
        ...

    def load_stages(self, cycle_id: int) -> list[StageState]:
        """사이클의 **모든** 단계. 개수가 max_stages 와 다르면 거부한다(H3).

        각 단계의 trigger_price 를 사이클의 ladder_json 과 대조한다(H4).
        """
        ...

    def save_stage(self, cycle_id: int, stage: StageState) -> None: ...

    # ── 주문 이력과 실현손익 ────────────────────────────────────────────
    def append_order_log(
        self, *, client_ref: str, cycle_id: int, stage_state_id: int | None,
        side: Side, order_type: str, path: OrderPath, req_price: int | None,
        req_qty: int, trigger_reason: str, tick_price: int | None,
        tick_source: str | None, sent_at: datetime,
    ) -> int:
        """status=SENDING 으로 기록하고 id 를 반환. 설계서 9절 ③."""
        ...

    def update_order_log(
        self, *, client_ref: str, status: str, broker_order_id: str | None = None,
        fill_price: int | None = None, fill_qty: int | None = None,
        api_code: str | None = None, api_message: str | None = None,
        settled_at: datetime | None = None,
    ) -> None: ...

    def load_pending_orders(self) -> list[dict[str, object]]:
        """SENDING·ACCEPTED·UNKNOWN 상태의 주문. 재시작 복구가 쓴다."""
        ...

    def realized_pnl_for_cycle(self, cycle_id: int) -> int:
        """order_log 에서 집계한 실현손익(H5).

        도메인에는 이 값이 없다 — after_sell 이 fill_price·fill_qty 를 비우므로
        단계 상태만으로는 계산할 수 없다.
        """
        ...

    # ── 긴급청산·대사 이력 ──────────────────────────────────────────────
    def append_emergency_log(
        self, *, scope: str, stock_code: str | None, cycle_id: int | None,
        requested_at: datetime, reason: str | None, qty_before: int | None,
        qty_after: int | None, canceled_orders: int | None, result: str,
        detail_json: str | None, completed_at: datetime | None,
    ) -> int: ...

    def append_reconcile_log(
        self, *, checked_at: datetime, stock_code: str, internal_qty: int,
        broker_qty: int, verdict: str, action_taken: str | None,
    ) -> int: ...

    # ── 보유현황 뷰 ─────────────────────────────────────────────────────
    def holdings(self) -> list[HoldingRow]:
        """설계서 12.3절의 holdings 뷰. 현재가·평가손익은 UI 가 최신 틱과 결합한다."""
        ...
```

- [ ] **Step 4: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/ports/test_repository.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS. `ports/` 는 `domain/` 만 import 하므로
`mapping.py` 가 아직 없어도 import 오류가 나지 않는다.

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/ports/repository.py tests/ports/test_repository.py
git commit -m "$(printf 'feat: 리포지토리 포트 선언\n\n설계서 12절 스키마의 접근면. 메서드가 행이나 dict 가 아니라 도메인 객체를\n주고받으며, 변환은 어댑터의 매핑 계층이 한다 — 그곳이 Plan 1 이 넘긴 제약\n(완전한 단계 집합, trigger_price 대조, tz-aware datetime)을 강제하는 지점이다.\n\n메서드 목록을 집합으로 단정해 Task 8~10 의 진행 상황을 셀 수 있게 했다.\nSplitConfig 와 HoldingRow 도 여기 둔다. 포트의 계약이 그 두 타입으로 쓰여 있으므로\n포트와 함께 사는 것이 맞다 — 어댑터 층에 두면 포트를 쓰는 모든 코드가 DTO 하나를\n얻으려고 adapters/sqlite/ 를 import 해야 하고 화살표가 거꾸로 돈다.')"
```

---
### Task 4: SQLite 스키마와 마이그레이션

**Files:**
- Create: `src/autotrading7s/adapters/sqlite/__init__.py`, `schema.sql`, `migrations.py`
- Test: `tests/adapters/sqlite/__init__.py`, `tests/adapters/sqlite/test_migrations.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리 `sqlite3` 만)
- Produces:
  - `SCHEMA_VERSION: int` — 현재 스키마 버전 (1)
  - `apply_schema(conn: sqlite3.Connection) -> int` — 스키마를 적용하고 적용 후 버전을 반환. 이미 최신이면 아무것도 하지 않는다(멱등)
  - `connect(path: str | Path) -> sqlite3.Connection` — 외래키를 켜고 row_factory 를 설정한 연결

**스키마는 설계서 12.1절을 그대로 옮기고 D20 컬럼을 더한다.** 8개 테이블과
`holdings` 뷰다. 설계서에 없는 것을 추가하지 않는다 — 단 `schema_version` 테이블은
마이그레이션 추적용으로 필요하므로 예외다.

`PRAGMA foreign_keys = ON` 을 매 연결에서 켜야 한다. SQLite 는 기본이 꺼짐이며,
꺼진 상태에서는 `REFERENCES` 가 장식이 된다 — 사이클이 없는 단계 행이 들어갈 수 있고
그것은 H3(완전한 단계 집합)이 막으려는 것과 같은 부류의 손상이다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/sqlite/test_migrations.py`**

`mkdir -p tests/adapters/sqlite && touch tests/adapters/sqlite/__init__.py` 를 먼저 실행한다.

```python
from __future__ import annotations

import sqlite3

import pytest

from autotrading7s.adapters.sqlite.migrations import (
    SCHEMA_VERSION,
    apply_schema,
    connect,
)

EXPECTED_TABLES = {
    "split_config", "cycle", "stage_state", "order_log",
    "emergency_liquidation_log", "token_session", "reconcile_log",
    "schema_version",
}


@pytest.fixture()
def conn():
    c = connect(":memory:")
    apply_schema(c)
    yield c
    c.close()


def test_applies_every_table(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    assert {r["name"] for r in rows} == EXPECTED_TABLES


def test_creates_the_holdings_view(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    ).fetchall()
    assert {r["name"] for r in rows} == {"holdings"}


def test_holdings_view_is_queryable_when_empty(conn):
    """뷰가 문법적으로 유효한지 — 빈 상태에서도 실행되어야 한다."""
    assert conn.execute("SELECT * FROM holdings").fetchall() == []


def test_records_the_schema_version(conn):
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == SCHEMA_VERSION


def test_apply_is_idempotent(conn):
    """이미 최신이면 아무것도 하지 않는다 — 매 기동마다 호출해도 안전해야 한다."""
    assert apply_schema(conn) == SCHEMA_VERSION
    assert apply_schema(conn) == SCHEMA_VERSION
    rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    assert rows["n"] == 1


def test_foreign_keys_are_enforced(conn):
    """PRAGMA foreign_keys 가 꺼져 있으면 REFERENCES 가 장식이 된다."""
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage_state "
            "(cycle_id, stage_no, status, trigger_price, planned_qty) "
            "VALUES (999, 1, 'WAITING', 9000, 111)"
        )


def test_stage_state_uniqueness(conn):
    """같은 사이클에 같은 단계번호가 둘 있으면 decide() 가 중복으로 거부한다 —
    그 전에 스키마가 막아야 한다."""
    conn.execute(
        "INSERT INTO split_config "
        "(stock_code, max_stages, drop_pct, target_pct, amount_per_stage, "
        " total_limit, status, created_at, updated_at) "
        "VALUES ('005930', 7, '0.05', '0.05', 1000000, 7000000, 'IDLE', 'x', 'x')"
    )
    conn.execute(
        "INSERT INTO cycle (config_id, seq, status, started_at) "
        "VALUES (1, 1, 'STARTING', 'x')"
    )
    conn.execute(
        "INSERT INTO stage_state "
        "(cycle_id, stage_no, status, trigger_price, planned_qty) "
        "VALUES (1, 1, 'WAITING', 9000, 111)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stage_state "
            "(cycle_id, stage_no, status, trigger_price, planned_qty) "
            "VALUES (1, 1, 'WAITING', 9000, 111)"
        )


def test_order_log_client_ref_is_unique(conn):
    """client_ref 는 설계서 9절의 멱등성 키다 — 중복이면 UNKNOWN 대조가 무의미해진다."""
    cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(order_log)").fetchall()
    }
    assert "client_ref" in cols
    idx = conn.execute("PRAGMA index_list(order_log)").fetchall()
    assert any(r["unique"] for r in idx)


def test_cycle_carries_the_d20_columns(conn):
    """설계서 D20 — 강제 종료의 증언과 잔량."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cycle)").fetchall()}
    assert {"close_reason", "forced_close_reason", "forced_close_qty"} <= cols


def test_token_session_stores_no_token(conn):
    """설계서 13.1절 — 토큰 원문은 keyring 에 있고 DB 는 감사 목적만."""
    cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(token_session)").fetchall()
    }
    assert "token_enc" not in cols and "token" not in cols
    assert {"env", "app_key_hash", "issued_at", "expires_at"} <= cols
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/sqlite/test_migrations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.adapters.sqlite'`

- [ ] **Step 3: `src/autotrading7s/adapters/sqlite/schema.sql` 작성**

설계서 12.1절의 8개 테이블을 그대로 옮기고, `cycle` 에 D20 컬럼 두 개를 더하고,
`schema_version` 을 추가한다. 12.3절의 `holdings` 뷰도 포함한다.

```sql
-- AutoTrading 7s 스키마 v1 — 설계서 12절
-- 비율은 TEXT(Decimal 문자열), 시각은 TEXT(ISO 8601, tz-aware 필수).

CREATE TABLE schema_version (
  version INTEGER NOT NULL
);

CREATE TABLE split_config (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  label TEXT,
  max_stages INTEGER NOT NULL CHECK(max_stages BETWEEN 2 AND 7),
  drop_pct TEXT NOT NULL,
  target_pct TEXT NOT NULL,
  amount_per_stage INTEGER NOT NULL CHECK(amount_per_stage > 0),
  allow_rebuy INTEGER NOT NULL DEFAULT 1 CHECK(allow_rebuy IN (0, 1)),
  rebuy_cooldown_sec INTEGER NOT NULL DEFAULT 60 CHECK(rebuy_cooldown_sec >= 0),
  total_limit INTEGER NOT NULL CHECK(total_limit >= 0),
  status TEXT NOT NULL CHECK(status IN ('IDLE', 'ACTIVE')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(stock_code, label)
);

CREATE TABLE cycle (
  id INTEGER PRIMARY KEY,
  config_id INTEGER NOT NULL REFERENCES split_config(id),
  seq INTEGER NOT NULL CHECK(seq >= 1),
  status TEXT NOT NULL CHECK(status IN
    ('STARTING', 'RUNNING', 'PAUSED', 'LIQUIDATING', 'CLOSED')),
  anchor_price INTEGER CHECK(anchor_price IS NULL OR anchor_price > 0),
  ladder_json TEXT,
  realized_pnl INTEGER,
  close_reason TEXT CHECK(close_reason IS NULL OR close_reason IN
    ('NORMAL', 'EMERGENCY', 'FORCED')),
  forced_close_reason TEXT,
  forced_close_qty INTEGER CHECK(forced_close_qty IS NULL OR forced_close_qty > 0),
  started_at TEXT NOT NULL,
  closed_at TEXT,
  UNIQUE(config_id, seq),
  -- D20: FORCED 는 증언과 잔량이 둘 다 있어야 한다
  CHECK(close_reason IS NOT 'FORCED'
        OR (forced_close_reason IS NOT NULL AND forced_close_qty IS NOT NULL))
);

CREATE TABLE stage_state (
  id INTEGER PRIMARY KEY,
  cycle_id INTEGER NOT NULL REFERENCES cycle(id),
  stage_no INTEGER NOT NULL CHECK(stage_no BETWEEN 1 AND 7),
  status TEXT NOT NULL CHECK(status IN
    ('WAITING', 'BUY_PENDING', 'HOLDING', 'SELL_PENDING', 'SOLD')),
  trigger_price INTEGER NOT NULL CHECK(trigger_price > 0),
  planned_qty INTEGER NOT NULL CHECK(planned_qty >= 0),
  fill_price INTEGER CHECK(fill_price IS NULL OR fill_price > 0),
  fill_qty INTEGER CHECK(fill_qty IS NULL OR fill_qty > 0),
  bought_at TEXT,
  last_sold_at TEXT,
  rebuy_count INTEGER NOT NULL DEFAULT 0 CHECK(rebuy_count >= 0),
  UNIQUE(cycle_id, stage_no),
  -- 도메인의 StageState 불변식을 스키마에서도 강제한다
  CHECK(status NOT IN ('HOLDING', 'SELL_PENDING')
        OR (fill_price IS NOT NULL AND fill_qty IS NOT NULL))
);

CREATE TABLE order_log (
  id INTEGER PRIMARY KEY,
  client_ref TEXT NOT NULL UNIQUE,
  cycle_id INTEGER NOT NULL REFERENCES cycle(id),
  stage_state_id INTEGER REFERENCES stage_state(id),
  side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
  order_type TEXT NOT NULL CHECK(order_type IN ('LIMIT', 'MARKET')),
  path TEXT NOT NULL CHECK(path IN ('TRIGGER', 'EMERGENCY')),
  req_price INTEGER,
  req_qty INTEGER NOT NULL CHECK(req_qty > 0),
  fill_price INTEGER,
  fill_qty INTEGER,
  status TEXT NOT NULL CHECK(status IN
    ('SENDING', 'ACCEPTED', 'PARTIAL', 'FILLED', 'CANCELED', 'REJECTED', 'UNKNOWN')),
  broker_order_id TEXT,
  api_code TEXT,
  api_message TEXT,
  trigger_reason TEXT NOT NULL,
  tick_price INTEGER,
  tick_source TEXT CHECK(tick_source IS NULL OR tick_source IN ('WS', 'REST_POLL')),
  sent_at TEXT NOT NULL,
  settled_at TEXT,
  -- 자동 트리거 경로는 시장가를 낼 수 없다 (설계서 6절)
  CHECK(path IS NOT 'TRIGGER' OR order_type = 'LIMIT')
);

CREATE INDEX idx_order_log_cycle ON order_log(cycle_id);
CREATE INDEX idx_order_log_status ON order_log(status);

CREATE TABLE emergency_liquidation_log (
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL CHECK(scope IN ('SINGLE', 'ALL')),
  stock_code TEXT,
  cycle_id INTEGER REFERENCES cycle(id),
  requested_at TEXT NOT NULL,
  reason TEXT,
  qty_before INTEGER,
  qty_after INTEGER,
  canceled_orders INTEGER,
  result TEXT NOT NULL CHECK(result IN
    ('SUCCESS', 'PARTIAL', 'FAILED', 'REJECTED_CLOSED_MARKET', 'FORCED_CLOSE')),
  detail_json TEXT,
  completed_at TEXT
);

CREATE TABLE token_session (
  id INTEGER PRIMARY KEY,
  env TEXT NOT NULL CHECK(env IN ('MOCK', 'REAL')),
  app_key_hash TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE reconcile_log (
  id INTEGER PRIMARY KEY,
  checked_at TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  internal_qty INTEGER NOT NULL,
  broker_qty INTEGER NOT NULL,
  verdict TEXT NOT NULL CHECK(verdict IN
    ('MATCH', 'INTERNAL_LESS', 'INTERNAL_MORE')),
  action_taken TEXT
);

-- 설계서 12.3절. 현재가·평가손익은 실시간 값이므로 뷰에 담지 않는다.
CREATE VIEW holdings AS
SELECT cfg.stock_code,
       cfg.stock_name,
       cfg.label,
       cy.id                                                AS cycle_id,
       SUM(ss.fill_qty)                                     AS total_qty,
       SUM(ss.fill_price * ss.fill_qty) / SUM(ss.fill_qty)   AS avg_price,
       COUNT(*)                                             AS holding_stages,
       cfg.max_stages,
       cy.status                                            AS cycle_status
FROM stage_state ss
JOIN cycle cy         ON cy.id  = ss.cycle_id
JOIN split_config cfg ON cfg.id = cy.config_id
WHERE ss.status IN ('HOLDING', 'SELL_PENDING')
  AND cy.status != 'CLOSED'
GROUP BY cy.id;
```

설계서 12.3절의 뷰는 `COUNT(*) FILTER (WHERE ss.status = 'HOLDING')` 를 쓰지만,
`WHERE` 절이 이미 `HOLDING`·`SELL_PENDING` 으로 좁히므로 `COUNT(*)` 로 같은 결과가
나오고 SQLite 버전 의존성이 줄어든다. `FILTER` 는 SQLite 3.30+ 에서만 동작한다.

- [ ] **Step 4: `src/autotrading7s/adapters/sqlite/migrations.py` 작성**

```python
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
    with conn:  # 전체를 한 트랜잭션으로
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)",
                     (SCHEMA_VERSION,))
    return SCHEMA_VERSION
```

`executescript` 는 암묵적으로 커밋하므로 `with conn:` 과 함께 쓸 때 주의가 필요하다.
테스트가 멱등성과 버전 기록을 확인하므로, 실행해서 통과하는지 반드시 확인한다 —
통과하지 않으면 `executescript` 전에 `conn.commit()` 을 부르거나 트랜잭션 경계를
조정해야 한다. 그 조정이 필요했다면 보고서에 적는다.

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/sqlite/test_migrations.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (11 tests + 기존 전부)

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/adapters/sqlite/ tests/adapters/sqlite/
git commit -m "$(printf 'feat: SQLite 스키마와 멱등 마이그레이션\n\n설계서 12절의 8개 테이블과 holdings 뷰. D20 강제 종료 컬럼(close_reason=FORCED,\nforced_close_reason, forced_close_qty)을 포함하고, FORCED 일 때 증언과 잔량이\n둘 다 있어야 한다는 CHECK 를 걸었다.\n\n도메인 불변식 두 개를 스키마에서도 강제한다: HOLDING·SELL_PENDING 은 fill 정보\n필수, TRIGGER 경로는 order_type=LIMIT 만. 같은 규칙을 두 층에서 지키면 한쪽이\n무너져도 다른 쪽이 잡는다.\n\nPRAGMA foreign_keys 를 매 연결에서 켠다 — SQLite 는 기본이 꺼짐이며 꺼진 상태에서는\nREFERENCES 가 장식이 된다.\n\nholdings 뷰는 설계서의 COUNT(*) FILTER 대신 COUNT(*) 를 쓴다. WHERE 절이 이미\nHOLDING·SELL_PENDING 으로 좁히므로 결과가 같고 SQLite 3.30 미만에서도 동작한다.')"
```

---
### Task 5: 코덱 — `Decimal`·`datetime` 의 TEXT 왕복 (H2)

**Files:**
- Create: `src/autotrading7s/adapters/sqlite/codec.py`
- Test: `tests/adapters/sqlite/test_codec.py`

**Interfaces:**
- Consumes: `DomainInvariantError` (Task 1)
- Produces:
  - `ratio_to_text(value: Decimal) -> str`
  - `text_to_ratio(text: str) -> Decimal`
  - `dt_to_text(value: datetime) -> str`
  - `text_to_dt(text: str) -> datetime` — **tz-aware 가 아니면 `DomainInvariantError`**
  - `bool_to_int(value: bool) -> int`, `int_to_bool(value: int) -> bool`

**왜 별도 모듈인가.** 원시 타입 왕복의 정확성이 나머지 전부의 전제다. `Decimal("0.05")`
가 왕복에서 `Decimal("0.0500")` 이 되거나 tzinfo 를 잃으면, 그 위에 쌓인 도메인 객체
복원은 모두 무의미해진다. 그래서 도메인 객체 없이 단독으로 검증한다.

**H2 가 여기서 착륙한다.** Plan 1 의 Task 9 가 확인한 실패 모드는 이렇다 —
SQLite 의 TEXT 타임스탬프를 tzinfo 없이 파싱하면, 쿨다운 계산이
`aware_now - naive_last_sold_at` 을 시도해 엔진 틱 루프 안에서 `TypeError` 를 낸다.
Plan 1 은 그것을 Task 9 의 쿨다운 검사에서 막았지만, **읽는 쪽에서 애초에 naive 를
만들지 않는 것**이 근본 대응이다. 두 층이 함께 지킨다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/sqlite/test_codec.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.codec import (
    bool_to_int,
    dt_to_text,
    int_to_bool,
    ratio_to_text,
    text_to_dt,
    text_to_ratio,
)
from autotrading7s.domain.errors import DomainInvariantError

KST = timezone(timedelta(hours=9))


@pytest.mark.parametrize(
    "value",
    [Decimal("0.05"), Decimal("0.1666"), Decimal("0.25"), Decimal("0.5"),
     Decimal("0.0001")],
)
def test_ratio_round_trip_is_exact(value: Decimal):
    """Decimal("0.05") 가 Decimal("0.0500") 이 되면 target_pct 비교가 어긋난다."""
    assert text_to_ratio(ratio_to_text(value)) == value
    assert str(text_to_ratio(ratio_to_text(value))) == str(value)


def test_ratio_text_is_not_scientific_notation():
    """지수 표기가 되면 사람이 DB 를 읽을 때 혼란스럽고 비교도 흔들린다."""
    assert "E" not in ratio_to_text(Decimal("0.0001")).upper()


def test_ratio_rejects_float():
    with pytest.raises(TypeError):
        ratio_to_text(0.05)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 9, 30, 15, 123456, tzinfo=timezone.utc),
        datetime(2026, 9, 1, 18, 30, tzinfo=KST),
    ],
)
def test_datetime_round_trip_preserves_the_instant(value: datetime):
    assert text_to_dt(dt_to_text(value)) == value


def test_datetime_round_trip_preserves_awareness():
    value = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    restored = text_to_dt(dt_to_text(value))
    assert restored.tzinfo is not None
    assert restored.tzinfo.utcoffset(restored) is not None


def test_writing_a_naive_datetime_is_refused():
    """도메인의 모든 datetime 은 tz-aware 여야 한다 — 쓰는 쪽에서도 막는다."""
    with pytest.raises(DomainInvariantError, match="timezone-aware"):
        dt_to_text(datetime(2026, 9, 1, 9, 30))


def test_reading_a_naive_text_is_refused():
    """H2 의 핵심. 오프셋 없는 TEXT 는 naive datetime 을 만들고, 그것이 엔진 틱
    루프 안에서 TypeError 를 낸다 — 읽는 쪽에서 애초에 만들지 않는다."""
    with pytest.raises(DomainInvariantError, match="timezone-aware"):
        text_to_dt("2026-09-01T09:30:00")


def test_reading_garbage_is_refused():
    with pytest.raises(DomainInvariantError):
        text_to_dt("not a timestamp")


def test_kst_and_utc_texts_compare_as_the_same_instant():
    """저장 시각대가 달라도 같은 순간이면 같아야 한다 — 쿨다운 산술의 전제."""
    utc = text_to_dt("2026-09-01T09:30:00+00:00")
    kst = text_to_dt("2026-09-01T18:30:00+09:00")
    assert utc == kst
    assert (utc - kst).total_seconds() == 0


@pytest.mark.parametrize(("value", "expected"), [(True, 1), (False, 0)])
def test_bool_round_trip(value: bool, expected: int):
    assert bool_to_int(value) == expected
    assert int_to_bool(bool_to_int(value)) is value


def test_bool_to_int_rejects_non_bool():
    """allow_rebuy 가 진리값 해석으로 켜지는 것을 Plan 1 이 막았다 — 여기서도 막는다."""
    with pytest.raises(TypeError):
        bool_to_int(1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [2, -1])
def test_int_to_bool_rejects_values_outside_zero_and_one(value: int):
    with pytest.raises(DomainInvariantError):
        int_to_bool(value)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/sqlite/test_codec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.adapters.sqlite.codec'`

- [ ] **Step 3: `src/autotrading7s/adapters/sqlite/codec.py` 작성**

```python
"""원시 타입의 TEXT 왕복.

SQLite 에는 `Decimal` 이 없고, `REAL` 로 저장하면 float 가 되어 전역 제약
("금액·가격은 원 단위 int, 비율만 Decimal, float 금지")을 어긴다. 그래서 비율은
TEXT 로 저장한다(설계서 12.1절).

시각도 TEXT 다. 여기서 지켜야 하는 것이 H2 이며, Plan 1 의 Task 9 가 그 실패
모드를 확인했다 — tzinfo 없이 파싱된 시각이 쿨다운 계산에서 aware 시각과 만나
엔진 틱 루프 안에서 `TypeError` 를 낸다. 이 모듈은 **쓸 때도 읽을 때도** naive 를
거부하므로 그런 값이 도메인에 도달하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from autotrading7s.domain.errors import DomainInvariantError


def ratio_to_text(value: Decimal) -> str:
    """비율을 TEXT 로. 지수 표기를 쓰지 않는다."""
    if not isinstance(value, Decimal):
        raise TypeError(f"ratio must be Decimal, not {type(value).__name__}")
    if not value.is_finite():
        raise DomainInvariantError(
            f"ratio must be finite, not {value!r} (NaN or Infinity)"
        )
    # format(value, "f") 는 지수 표기를 쓰지 않고 유효자리를 보존한다.
    return format(value, "f")


def text_to_ratio(text: str) -> Decimal:
    if not isinstance(text, str):
        raise TypeError(f"ratio text must be str, not {type(text).__name__}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise DomainInvariantError(f"not a valid ratio: {text!r}") from exc
    if not value.is_finite():
        raise DomainInvariantError(
            f"ratio must be finite, not {value!r} (NaN or Infinity)"
        )
    return value


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DomainInvariantError(
            f"{label} must be timezone-aware, got naive datetime: {value!r}"
        )


def dt_to_text(value: datetime) -> str:
    """시각을 ISO 8601 TEXT 로. naive 는 거부한다."""
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, not {type(value).__name__}")
    _require_aware(value, "datetime being stored")
    return value.isoformat()


def text_to_dt(text: str) -> datetime:
    """ISO 8601 TEXT 를 시각으로. 오프셋이 없으면 거부한다(H2)."""
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DomainInvariantError(f"not a valid timestamp: {text!r}") from exc
    _require_aware(value, f"timestamp {text!r}")
    return value


def bool_to_int(value: bool) -> int:
    """SQLite 에는 BOOLEAN 이 없다. 0/1 변환을 저장소 경계에서 명시적으로 한다.

    Plan 1 은 `allow_rebuy` 가 진리값 해석으로 켜지는 것을 막았다(`"false"` 가
    재매수를 켜면 투입이 늘어나는 방향이다). 그 엄격함을 여기서도 유지한다.
    """
    if not isinstance(value, bool):
        raise TypeError(f"expected bool, not {type(value).__name__}")
    return 1 if value else 0


def int_to_bool(value: int) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected int, not {type(value).__name__}")
    if value not in (0, 1):
        raise DomainInvariantError(f"boolean column must be 0 or 1, got {value}")
    return value == 1
```

**실행 중 수정됨 (커밋 07871d1).** 위 블록의 초안에는 세 가지 결함이 있었고 리뷰가
전부 잡았다. 기록해 둔다 — 같은 부류가 뒤 태스크에서도 나올 수 있다:

1. `text_to_ratio` 에 타입 가드가 없어 `text_to_ratio(0.05)` 가 58자의 이진
   부동소수 잡음을 조용히 통과시켰다. **쓰는 쪽 `ratio_to_text` 는 막고 읽는 쪽만
   안 막는 비대칭**이었다.
2. `text_to_dt` 가 `except (ValueError, TypeError)` 로 묶어 호출자의 타입 버그를
   `DomainInvariantError` 로 바꿨다. Task 6 이 그것을 `CorruptRowError` 로 감싸므로
   매핑의 널 검사 누락이 "corrupt row in cycle (id=3)" 로 위장한다. nullable 시각
   컬럼의 `None if x is None else text_to_dt(x)` 가드가 그래서 하중을 받는다.
3. 유한하지 않은 `Decimal` 이 그대로 통과했다. `Infinity` 는 하류의
   `LadderConfigError` 가 잡지만 **`NaN` 은 `decimal.InvalidOperation` 을 내며 그것은
   `ValueError` 의 하위가 아니다**(MRO: `InvalidOperation` → `DecimalException` →
   `ArithmeticError`). 그래서 Task 6 의 `except ValueError` 를 통과해 어느 행인지
   모르는 맨 예외로 표면화된다.

- [ ] **Step 4: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/sqlite/test_codec.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/adapters/sqlite/codec.py tests/adapters/sqlite/test_codec.py
git commit -m "$(printf 'feat: Decimal·datetime 의 TEXT 왕복 코덱\n\nSQLite 에 Decimal 이 없고 REAL 로 저장하면 float 가 되어 전역 제약을 어긴다.\n비율은 TEXT 로 저장하며 지수 표기를 쓰지 않는다.\n\nH2 를 여기서 강제한다 — 쓸 때도 읽을 때도 naive datetime 을 거부한다. Plan 1 의\nTask 9 가 확인한 실패 모드는 tzinfo 없이 파싱된 시각이 쿨다운 계산에서 aware\n시각과 만나 엔진 틱 루프 안에서 TypeError 를 내는 것이었다. Plan 1 은 쿨다운\n검사에서 막았고, 이 모듈은 그런 값이 애초에 만들어지지 않게 한다.\n\nbool 변환도 명시적이다. Plan 1 이 allow_rebuy 의 진리값 해석을 막았으므로\n(false 문자열이 재매수를 켜면 투입이 늘어나는 방향) 저장소 경계에서도 유지한다.')"
```

---

### Task 6: 매핑 — `split_config` 과 `cycle`

**Files:**
- Create: `src/autotrading7s/adapters/sqlite/mapping.py`
- Modify: `src/autotrading7s/domain/types.py` — `CloseReason` 에 `FORCED` 멤버 추가
- Test: `tests/adapters/sqlite/test_mapping_config_cycle.py`

**`CloseReason.FORCED` 를 여기서 추가한다.** Task 4 의 스키마가 이미
`close_reason IN ('NORMAL','EMERGENCY','FORCED')` 를 허용하고 설계서 D20 이 그 값을
정의하는데, 도메인 enum 에는 없었다 — 이 계획의 초안이 빠뜨린 것이다. 저장된
`FORCED` 행을 복원하려면 매핑 계층에 그 멤버가 있어야 한다. 그 값을 *만드는* 상태
전이(`force_close`)는 Plan 2B 에 남는다 — 여기서 추가하는 것은 멤버뿐이며 새 동작이
없다. 정상 `close()` 경로로 `FORCED` 를 만드는 오용은 D20 의 스키마 CHECK
(`FORCED` 는 `forced_close_reason`·`forced_close_qty` 가 둘 다 있어야 한다)가 막는다.

**Interfaces:**
- Consumes: `codec` (Task 5), `SplitConfig` (Task 3 — `ports/repository.py`), `Ladder`·`LadderConfigError` (`domain/ladder.py`), `Cycle`·`CycleStatus`·`CloseReason` (`domain/cycle.py`, `domain/types.py`), `DomainInvariantError` (Task 1)
- Produces:
  - `CorruptRowError(DomainInvariantError)` — 어느 테이블의 어느 행인지 지목한다
  - `config_to_row(config) -> dict`, `row_to_config(row) -> SplitConfig`
  - `ladder_to_json(ladder) -> str`, `json_to_ladder(text) -> Ladder`
  - `cycle_to_row(cycle) -> dict`, `row_to_cycle(row) -> Cycle`

**`CorruptRowError` 가 H1 의 소비자다.** 도메인 객체를 복원하다 `DomainInvariantError`
가 나면, 그것은 호출자 버그가 아니라 **그 행이 손상된 것**이다. 그대로 올리면 사용자는
"stage_no must be >= 1" 만 보고 어느 행인지 모른다. 감싸서 테이블과 rowid 를 붙인다.

호출자 버그(`ValueError`, `TypeError`)는 **감싸지 않는다** — 그것은 개발 중에 그대로
드러나야 한다. Task 1 이 두 범주를 나눈 목적이 바로 이 구분이다.

**`ladder_json` 은 사다리의 스냅샷이다.** 설계서 12.2절이 그 이유를 적었다 — 사용자가
나중에 하락률을 바꿔도 과거 사이클의 주문이 왜 그 가격에 나갔는지 재현할 수 있어야
한다. 그래서 `split_config` 을 참조해 재계산하지 않고 사이클에 박제한다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/sqlite/test_mapping_config_cycle.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import (
    CorruptRowError,
    config_to_row,
    cycle_to_row,
    json_to_ladder,
    ladder_to_json,
    row_to_config,
    row_to_cycle,
)
from autotrading7s.ports.repository import SplitConfig
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import CloseReason, CycleStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_config(**over) -> SplitConfig:
    kwargs = dict(
        config_id=1, stock_code="005930", stock_name="삼성전자", label="기본",
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=True, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="IDLE", created_at=T0, updated_at=T0,
    )
    kwargs.update(over)
    return SplitConfig(**kwargs)  # type: ignore[arg-type]


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def test_config_round_trip():
    original = a_config()
    restored = row_to_config(config_to_row(original) | {"id": 1})
    assert restored == original


def test_config_round_trip_preserves_decimal_exactly():
    """0.1666 이 0.1666 으로 돌아와야 한다 — 사다리 계산이 이 값에 달려 있다."""
    original = a_config(drop_pct=Decimal("0.1666"))
    restored = row_to_config(config_to_row(original) | {"id": 1})
    assert restored.drop_pct == Decimal("0.1666")
    assert str(restored.drop_pct) == "0.1666"


def test_config_round_trip_preserves_bool():
    for value in (True, False):
        restored = row_to_config(config_to_row(a_config(allow_rebuy=value))
                                 | {"id": 1})
        assert restored.allow_rebuy is value


def test_config_row_stores_ratios_as_text():
    row = config_to_row(a_config())
    assert isinstance(row["drop_pct"], str)
    assert isinstance(row["target_pct"], str)
    assert row["allow_rebuy"] in (0, 1)


def test_row_to_config_wraps_a_corrupt_row():
    """max_stages=9 는 도메인이 거부한다 — 어느 행인지 알려줘야 한다."""
    row = config_to_row(a_config()) | {"id": 42, "max_stages": 9}
    with pytest.raises(CorruptRowError) as exc:
        row_to_config(row)
    assert "split_config" in str(exc.value)
    assert "42" in str(exc.value)


def test_corrupt_row_error_is_a_domain_invariant_error():
    assert issubclass(CorruptRowError, DomainInvariantError)


def test_row_to_config_refuses_a_naive_timestamp():
    row = config_to_row(a_config()) | {"id": 1, "created_at": "2026-09-01T09:00:00"}
    with pytest.raises(CorruptRowError):
        row_to_config(row)


def test_ladder_json_round_trip():
    original = a_ladder()
    restored = json_to_ladder(ladder_to_json(original))
    assert restored == original
    assert restored.trigger_price(7) == original.trigger_price(7)


def test_ladder_json_stores_ratios_as_text():
    import json

    payload = json.loads(ladder_to_json(a_ladder()))
    assert payload["drop_pct"] == "0.05"
    assert payload["anchor_price"] == 10_000


def test_json_to_ladder_wraps_a_corrupt_snapshot():
    with pytest.raises(CorruptRowError, match="ladder_json"):
        json_to_ladder('{"anchor_price": 10000, "drop_pct": "0.05", '
                       '"target_pct": "0.05", "max_stages": 9, '
                       '"amount_per_stage": 1000000}')


def test_json_to_ladder_wraps_malformed_json():
    with pytest.raises(CorruptRowError, match="ladder_json"):
        json_to_ladder("{not json")


def test_cycle_round_trip_running():
    lad = a_ladder()
    original = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
                     anchor_price=10_000, ladder=lad, started_at=T0)
    restored = row_to_cycle(cycle_to_row(original) | {"id": 1})
    assert restored == original


def test_cycle_round_trip_idle_with_no_anchor():
    original = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE,
                     started_at=T0)
    restored = row_to_cycle(cycle_to_row(original) | {"id": 1})
    assert restored == original
    assert restored.anchor_price is None and restored.ladder is None


def test_cycle_round_trip_closed_forced():
    """D20 — 강제 종료의 증언과 잔량이 왕복해야 한다."""
    lad = a_ladder()
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=10_000, ladder=lad, started_at=T0)
    ) | {
        "id": 1, "status": "CLOSED", "close_reason": "FORCED",
        "forced_close_reason": "거래정지로 청산 불가", "forced_close_qty": 40,
        "closed_at": "2026-09-01T15:30:00+00:00",
    }
    restored = row_to_cycle(row)
    assert restored.status is CycleStatus.CLOSED
    assert restored.close_reason is CloseReason.FORCED


def test_row_to_cycle_wraps_an_anchor_ladder_mismatch():
    """설계서 4.2절이 같은 숫자를 두 곳에 쓰므로 복원 시 어긋날 수 있다."""
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.RUNNING,
              anchor_price=10_000, ladder=a_ladder(), started_at=T0)
    ) | {"id": 7, "anchor_price": 9_000}
    with pytest.raises(CorruptRowError) as exc:
        row_to_cycle(row)
    assert "cycle" in str(exc.value) and "7" in str(exc.value)


def test_row_to_cycle_wraps_an_unknown_status():
    row = cycle_to_row(
        Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE, started_at=T0)
    ) | {"id": 3, "status": "BOGUS"}
    with pytest.raises(CorruptRowError):
        row_to_cycle(row)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/sqlite/test_mapping_config_cycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.adapters.sqlite.mapping'`

- [ ] **Step 3: `mapping.py` 의 `CorruptRowError` 와 설정 변환 작성**

```python
"""행 ↔ 도메인 객체 변환.

Plan 1 이 Plan 2 로 넘긴 제약이 이 모듈에서 착륙한다 — H1(복원 실패를 지목),
H3(완전한 단계 집합), H4(trigger_price 대조). H2(tz-aware)는 codec 이 담당한다.

**감싸는 것과 감싸지 않는 것.** 도메인 객체를 복원하다 `DomainInvariantError` 가
나면 그것은 그 행이 손상된 것이므로 `CorruptRowError` 로 감싸 테이블과 rowid 를
붙인다. `ValueError`·`TypeError` 는 호출자 버그이므로 감싸지 않고 그대로 올린다 —
개발 중에 드러나야 한다. Task 1 이 두 범주를 나눈 목적이 이 구분이다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from autotrading7s.adapters.sqlite.codec import (
    bool_to_int,
    dt_to_text,
    int_to_bool,
    ratio_to_text,
    text_to_dt,
    text_to_ratio,
)
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import CloseReason, CycleStatus
from autotrading7s.ports.repository import SplitConfig


class CorruptRowError(DomainInvariantError):
    """복원된 행이 도메인 불변식을 만족하지 않을 때. 어느 행인지 지목한다."""


def _corrupt(table: str, rowid: object, cause: Exception) -> CorruptRowError:
    return CorruptRowError(f"corrupt row in {table} (id={rowid}): {cause}")

```

- [ ] **Step 4: `config_to_row` / `row_to_config` 작성**

```python
def config_to_row(config: SplitConfig) -> dict[str, Any]:
    return {
        "stock_code": config.stock_code,
        "stock_name": config.stock_name,
        "label": config.label,
        "max_stages": config.max_stages,
        "drop_pct": ratio_to_text(config.drop_pct),
        "target_pct": ratio_to_text(config.target_pct),
        "amount_per_stage": config.amount_per_stage,
        "allow_rebuy": bool_to_int(config.allow_rebuy),
        "rebuy_cooldown_sec": config.rebuy_cooldown_sec,
        "total_limit": config.total_limit,
        "status": config.status,
        "created_at": dt_to_text(config.created_at),
        "updated_at": dt_to_text(config.updated_at),
    }


def row_to_config(row: Mapping[str, Any]) -> SplitConfig:
    rowid = row.get("id")
    try:
        config = SplitConfig(
            config_id=rowid,
            stock_code=row["stock_code"],
            stock_name=row["stock_name"],
            label=row["label"],
            max_stages=row["max_stages"],
            drop_pct=text_to_ratio(row["drop_pct"]),
            target_pct=text_to_ratio(row["target_pct"]),
            amount_per_stage=row["amount_per_stage"],
            allow_rebuy=int_to_bool(row["allow_rebuy"]),
            rebuy_cooldown_sec=row["rebuy_cooldown_sec"],
            total_limit=row["total_limit"],
            status=row["status"],
            created_at=text_to_dt(row["created_at"]),
            updated_at=text_to_dt(row["updated_at"]),
        )
    except DomainInvariantError as exc:
        raise _corrupt("split_config", rowid, exc) from exc
    # SplitConfig 자체에는 불변식이 없다(저장 형태다). 실행 가능성은 Ladder 가
    # 판단하므로, 복원 시점에 사다리를 만들어 검증한다 — 앵커는 임의값을 쓴다.
    # max_stages 범위·비율 범위·1주 미달을 여기서 잡는다.
    try:
        config.to_ladder(anchor_price=10_000)
    except DomainInvariantError as exc:
        raise _corrupt("split_config", rowid, exc) from exc
    return config
```

`to_ladder(anchor_price=10_000)` 로 검증하는 것에는 한계가 있다 — 실제 앵커가
10,000 이 아니면 1주 미달 판정이 달라질 수 있다. 그래도 `max_stages` 범위와 비율
범위는 앵커와 무관하게 잡히며, 그것이 복원 시점에 잡고 싶은 손상이다. 앵커
의존적인 검증은 사이클 시작 시 실제 앵커로 다시 이루어진다. 이 한계를 코드 주석에
적는다.

- [ ] **Step 5: `ladder_to_json` / `json_to_ladder` / `cycle_to_row` / `row_to_cycle` 작성**

```python
def ladder_to_json(ladder: Ladder) -> str:
    """사다리 스냅샷. 설계서 12.2절 — 설정이 변해도 과거 사이클을 재현할 수 있다."""
    return json.dumps(
        {
            "anchor_price": ladder.anchor_price,
            "drop_pct": ratio_to_text(ladder.drop_pct),
            "target_pct": ratio_to_text(ladder.target_pct),
            "max_stages": ladder.max_stages,
            "amount_per_stage": ladder.amount_per_stage,
        },
        ensure_ascii=False,
    )


def json_to_ladder(text: str) -> Ladder:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CorruptRowError(f"corrupt ladder_json: {exc}") from exc
    try:
        return Ladder(
            anchor_price=payload["anchor_price"],
            drop_pct=text_to_ratio(payload["drop_pct"]),
            target_pct=text_to_ratio(payload["target_pct"]),
            max_stages=payload["max_stages"],
            amount_per_stage=payload["amount_per_stage"],
        )
    except KeyError as exc:
        raise CorruptRowError(f"corrupt ladder_json: missing key {exc}") from exc
    except DomainInvariantError as exc:
        raise CorruptRowError(f"corrupt ladder_json: {exc}") from exc


def cycle_to_row(cycle: Cycle) -> dict[str, Any]:
    return {
        "config_id": cycle.config_id,
        "seq": cycle.seq,
        "status": cycle.status.value,
        "anchor_price": cycle.anchor_price,
        "ladder_json": None if cycle.ladder is None else ladder_to_json(cycle.ladder),
        "close_reason": None if cycle.close_reason is None else cycle.close_reason.value,
        "started_at": None if cycle.started_at is None else dt_to_text(cycle.started_at),
        "closed_at": None if cycle.closed_at is None else dt_to_text(cycle.closed_at),
    }


def row_to_cycle(row: Mapping[str, Any]) -> Cycle:
    rowid = row.get("id")
    try:
        status = CycleStatus(row["status"])
        reason_text = row["close_reason"]
        close_reason = None if reason_text is None else CloseReason(reason_text)
        ladder_text = row["ladder_json"]
        ladder = None if ladder_text is None else json_to_ladder(ladder_text)
        started = row["started_at"]
        closed = row["closed_at"]
        return Cycle(
            cycle_id=rowid,
            config_id=row["config_id"],
            seq=row["seq"],
            status=status,
            anchor_price=row["anchor_price"],
            ladder=ladder,
            close_reason=close_reason,
            started_at=None if started is None else text_to_dt(started),
            closed_at=None if closed is None else text_to_dt(closed),
        )
    except ValueError as exc:
        # CycleStatus·CloseReason 의 알 수 없는 값도 ValueError 이며, 그것 역시
        # 행 손상이다. DomainInvariantError 는 ValueError 의 하위이므로 함께 잡힌다.
        raise _corrupt("cycle", rowid, exc) from exc
```

`except ValueError` 가 `DomainInvariantError` 와 알 수 없는 enum 값을 함께 잡는다.
enum 변환 실패도 행 손상이므로 같은 처리가 맞다. 다만 이 넓은 catch 는 `row["..."]`
의 `KeyError` 는 잡지 않으므로(그것은 호출자가 잘못된 행을 넘긴 것) 의도한 경계가
유지된다.

- [ ] **Step 6: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/sqlite/test_mapping_config_cycle.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (16 tests + 기존 전부)

- [ ] **Step 7: 커밋**

```bash
git add src/autotrading7s/adapters/sqlite/mapping.py tests/adapters/sqlite/test_mapping_config_cycle.py
git commit -m "$(printf 'feat: split_config·cycle 매핑과 CorruptRowError\n\nH1 의 소비자가 여기다. 도메인 객체를 복원하다 DomainInvariantError 가 나면 그 행이\n손상된 것이므로 CorruptRowError 로 감싸 테이블과 rowid 를 붙인다. ValueError·\nTypeError 는 호출자 버그이므로 감싸지 않고 그대로 올린다 — Task 1 이 두 범주를\n나눈 목적이 이 구분이다.\n\nladder_json 은 사다리 스냅샷이다(설계서 12.2절). 사용자가 하락률을 바꿔도 과거\n사이클의 주문이 왜 그 가격에 나갔는지 재현할 수 있어야 하므로 split_config 을\n참조해 재계산하지 않고 사이클에 박제한다.\n\nSplitConfig 는 도메인이 아니라 어댑터 층에 둔다. 설정은 사용자 입력의 저장\n형태이고 도메인이 쓰는 것은 그것으로 만든 Ladder 와 TriggerParams 다.')"
```

---
### Task 7: 매핑 — `stage_state` 와 두 제약 (H3·H4)

**Files:**
- Modify: `src/autotrading7s/adapters/sqlite/mapping.py`
- Test: `tests/adapters/sqlite/test_mapping_stage.py`

**Interfaces:**
- Consumes: Task 6의 `CorruptRowError`·`_corrupt`, `StageState`·`StageStatus`, `Ladder`
- Produces:
  - `stage_to_row(cycle_id: int, stage: StageState) -> dict`
  - `row_to_stage(row) -> StageState`
  - `rows_to_stages(rows, *, cycle_id: int, ladder: Ladder | None) -> list[StageState]` — **H3·H4를 강제한다**

**이 태스크가 Plan 2A의 핵심이다.** 개별 행 변환은 Task 6과 같은 모양이지만,
`rows_to_stages` 가 두 제약을 강제한다.

**H3 — 완전한 단계 집합.** Plan 1의 Task 7에서 나는 "정확히 1..max_stages 전부"를
도메인에서 요구하지 않기로 판단했다. 이유는 Task 8·9의 테스트가 부분 목록을
광범위하게 쓰고, 그것이 "호출자가 판정받고 싶은 단계를 넘긴다"는 정당한 용법이기
때문이다. 그 판단의 비용으로 남은 것이 이것이다 — `decide()`의 `by_no.get(n)` 이
`None` 이면 그 단계를 조용히 건너뛰고, 결과적으로 사다리 순서가 어긋난다.

그래서 **리포지토리가 그 제약을 진다.** `load_stages` 는 항상 완전한 집합을 반환하며,
행 개수가 `max_stages` 와 다르거나 단계번호가 1..max_stages 를 정확히 덮지 않으면
거부한다. 도메인은 부분 목록을 계속 허용하고, 리포지토리는 완전한 것만 준다.

**H4 — `trigger_price` 대조.** 설계서 4.2절이 같은 숫자를 `cycle.ladder_json` 과
`stage_state.trigger_price` 두 곳에 쓴다. 스키마에는 둘을 묶는 제약이 없으므로
복원 시점에 대조해야 한다. Plan 1의 최종 리뷰가 재현한 것은 `trigger_price=999_999`
인 행이 앵커보다 높은 가격의 매수를 만드는 것이었다. Plan 1은 `decide()` 안에
대조를 넣었고, 이 계획은 **로드 시점에도** 넣는다 — `decide()` 의 대조는 이미 메모리에
있는 상태를 보호하고, 이 대조는 손상된 행이 메모리에 들어오는 것을 막는다.

`ladder` 가 `None` 인 경우(STARTING 사이클)에는 H4를 검사할 수 없다. 그때는 H3만
검사하며, 그 사실을 함수 docstring 에 적는다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/sqlite/test_mapping_stage.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import (
    CorruptRowError,
    row_to_stage,
    rows_to_stages,
    stage_to_row,
)
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import StageStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def waiting(lad: Ladder, n: int) -> StageState:
    return StageState(stage_no=n, status=StageStatus.WAITING,
                      trigger_price=lad.trigger_price(n),
                      planned_qty=lad.planned_qty(n))


def holding(lad: Ladder, n: int, fill: int, qty: int) -> StageState:
    return StageState(stage_no=n, status=StageStatus.HOLDING,
                      trigger_price=lad.trigger_price(n),
                      planned_qty=lad.planned_qty(n),
                      fill_price=fill, fill_qty=qty, bought_at=T0)


def complete_rows(lad: Ladder, *, id_base: int = 1) -> list[dict]:
    rows = []
    for n in range(1, lad.max_stages + 1):
        rows.append(stage_to_row(1, waiting(lad, n)) | {"id": id_base + n - 1})
    return rows


def test_stage_round_trip_waiting():
    lad = a_ladder()
    original = waiting(lad, 3)
    restored = row_to_stage(stage_to_row(1, original) | {"id": 3})
    assert restored == original


def test_stage_round_trip_holding_with_timestamps():
    lad = a_ladder()
    original = holding(lad, 3, fill=8_950, qty=111)
    restored = row_to_stage(stage_to_row(1, original) | {"id": 3})
    assert restored == original
    assert restored.bought_at == T0
    assert restored.bought_at.tzinfo is not None


def test_stage_round_trip_after_rebuy():
    """last_sold_at 과 rebuy_count 가 왕복해야 쿨다운이 복원 후에도 동작한다."""
    lad = a_ladder()
    original = StageState(stage_no=2, status=StageStatus.WAITING,
                          trigger_price=lad.trigger_price(2),
                          planned_qty=lad.planned_qty(2),
                          last_sold_at=T0, rebuy_count=3)
    restored = row_to_stage(stage_to_row(1, original) | {"id": 2})
    assert restored == original
    assert restored.last_sold_at == T0 and restored.rebuy_count == 3


def test_row_to_stage_wraps_a_corrupt_row():
    lad = a_ladder()
    row = stage_to_row(1, waiting(lad, 3)) | {"id": 9, "trigger_price": -500}
    with pytest.raises(CorruptRowError) as exc:
        row_to_stage(row)
    assert "stage_state" in str(exc.value) and "9" in str(exc.value)


def test_row_to_stage_refuses_a_naive_timestamp():
    lad = a_ladder()
    row = stage_to_row(1, holding(lad, 3, 8_950, 111)) | {
        "id": 3, "bought_at": "2026-09-01T09:00:00"
    }
    with pytest.raises(CorruptRowError):
        row_to_stage(row)


# ── H3: 완전한 단계 집합 ──────────────────────────────────────────────────

def test_complete_set_is_accepted():
    lad = a_ladder()
    stages = rows_to_stages(complete_rows(lad), cycle_id=1, ladder=lad)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]


def test_stages_are_returned_in_stage_order():
    """DB 가 ORDER BY 없이 주더라도 매핑이 순서를 보장해야 한다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    shuffled = [rows[4], rows[0], rows[6], rows[2], rows[1], rows[5], rows[3]]
    stages = rows_to_stages(shuffled, cycle_id=1, ladder=lad)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]


def test_a_missing_stage_row_is_refused():
    """H3. decide() 는 없는 단계를 조용히 건너뛴다 — 리포지토리가 막는다."""
    lad = a_ladder()
    rows = [r for r in complete_rows(lad) if r["stage_no"] != 4]
    with pytest.raises(CorruptRowError, match="incomplete"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_the_error_names_the_missing_stage():
    lad = a_ladder()
    rows = [r for r in complete_rows(lad) if r["stage_no"] != 4]
    with pytest.raises(CorruptRowError) as exc:
        rows_to_stages(rows, cycle_id=1, ladder=lad)
    assert "4" in str(exc.value)


def test_a_duplicate_stage_row_is_refused():
    lad = a_ladder()
    rows = complete_rows(lad)
    rows.append(stage_to_row(1, waiting(lad, 3)) | {"id": 99})
    with pytest.raises(CorruptRowError, match="duplicate"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_an_out_of_range_stage_row_is_refused():
    """max_stages=7 인 사다리에 8단계 행이 있으면 손상이다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows.append(
        {"id": 99, "cycle_id": 1, "stage_no": 8, "status": "WAITING",
         "trigger_price": 6_000, "planned_qty": 166, "fill_price": None,
         "fill_qty": None, "bought_at": None, "last_sold_at": None,
         "rebuy_count": 0}
    )
    with pytest.raises(CorruptRowError):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_an_empty_row_list_is_refused():
    lad = a_ladder()
    with pytest.raises(CorruptRowError, match="incomplete"):
        rows_to_stages([], cycle_id=1, ladder=lad)


# ── H4: trigger_price 대조 ────────────────────────────────────────────────

def test_a_trigger_price_mismatch_is_refused():
    """H4. Plan 1 의 최종 리뷰가 재현한 것: trigger_price=999_999 인 행이
    앵커보다 높은 가격의 매수를 만든다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": 999_999}
    with pytest.raises(CorruptRowError, match="trigger_price"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_the_mismatch_error_names_both_values():
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": 999_999}
    with pytest.raises(CorruptRowError) as exc:
        rows_to_stages(rows, cycle_id=1, ladder=lad)
    message = str(exc.value)
    assert "999999" in message.replace(",", "")
    assert str(lad.trigger_price(2)) in message.replace(",", "")


def test_a_one_won_mismatch_is_still_refused():
    """호가 정규화 때문에 1원 차이가 우연히 나올 수 있다 — 그래도 손상이다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": lad.trigger_price(2) + 1}
    with pytest.raises(CorruptRowError, match="trigger_price"):
        rows_to_stages(rows, cycle_id=1, ladder=lad)


def test_h4_is_skipped_when_the_cycle_has_no_ladder():
    """STARTING 사이클은 앵커가 없어 사다리도 없다 — H3 만 검사한다."""
    lad = a_ladder()
    rows = complete_rows(lad)
    rows[1] = rows[1] | {"trigger_price": 999_999}
    stages = rows_to_stages(rows, cycle_id=1, ladder=None)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]
    assert stages[1].trigger_price == 999_999


def test_h3_still_applies_when_there_is_no_ladder():
    """사다리가 없어도 완전성은 검사한다 — 단, 기대 개수를 알 수 없으므로
    연속성과 중복만 본다."""
    lad = a_ladder()
    rows = [r for r in complete_rows(lad) if r["stage_no"] != 4]
    with pytest.raises(CorruptRowError, match="incomplete"):
        rows_to_stages(rows, cycle_id=1, ladder=None)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/sqlite/test_mapping_stage.py -v`
Expected: FAIL — `ImportError: cannot import name 'stage_to_row'`

- [ ] **Step 3: `stage_to_row` / `row_to_stage` 를 `mapping.py` 에 추가**

`row_to_cycle` 아래에 넣는다. `StageState`·`StageStatus` import 를 추가한다.

```python
def stage_to_row(cycle_id: int, stage: StageState) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "stage_no": stage.stage_no,
        "status": stage.status.value,
        "trigger_price": stage.trigger_price,
        "planned_qty": stage.planned_qty,
        "fill_price": stage.fill_price,
        "fill_qty": stage.fill_qty,
        "bought_at": None if stage.bought_at is None else dt_to_text(stage.bought_at),
        "last_sold_at": (
            None if stage.last_sold_at is None else dt_to_text(stage.last_sold_at)
        ),
        "rebuy_count": stage.rebuy_count,
    }


def row_to_stage(row: Mapping[str, Any]) -> StageState:
    rowid = row.get("id")
    try:
        bought = row["bought_at"]
        sold = row["last_sold_at"]
        return StageState(
            stage_no=row["stage_no"],
            status=StageStatus(row["status"]),
            trigger_price=row["trigger_price"],
            planned_qty=row["planned_qty"],
            fill_price=row["fill_price"],
            fill_qty=row["fill_qty"],
            bought_at=None if bought is None else text_to_dt(bought),
            last_sold_at=None if sold is None else text_to_dt(sold),
            rebuy_count=row["rebuy_count"],
        )
    except ValueError as exc:
        raise _corrupt("stage_state", rowid, exc) from exc
```

- [ ] **Step 4: `rows_to_stages` 작성 — H3·H4 강제**

```python
def rows_to_stages(
    rows: Sequence[Mapping[str, Any]],
    *,
    cycle_id: int,
    ladder: Ladder | None,
) -> list[StageState]:
    """사이클의 단계 집합을 복원한다. 항상 완전한 집합만 반환한다.

    **H3 — 완전성.** `decide()` 는 없는 단계를 조용히 건너뛰므로(Plan 1 Task 7 의
    판단), 리포지토리가 완전성을 진다. 도메인은 부분 목록을 계속 허용하고 이
    함수는 완전한 것만 준다. `ladder` 가 있으면 기대 개수는 `ladder.max_stages`
    이고, 없으면(STARTING 사이클) 1부터의 연속성과 중복 부재만 본다.

    **H4 — trigger_price 대조.** 설계서 4.2절이 같은 숫자를 `cycle.ladder_json` 과
    `stage_state.trigger_price` 두 곳에 쓰지만 스키마가 둘을 묶지 않는다. Plan 1 의
    최종 리뷰가 재현한 손상은 `trigger_price=999_999` 인 행이 앵커보다 높은 가격의
    매수를 만드는 것이었다. `decide()` 의 대조는 이미 메모리에 있는 상태를
    보호하고, 이 대조는 손상된 행이 메모리에 들어오는 것을 막는다.

    `ladder` 가 `None` 이면 H4 는 검사할 수 없다 — 대조 기준이 없다. 그때는 H3 만
    적용한다.

    반환 순서는 항상 `stage_no` 오름차순이다. DB 가 `ORDER BY` 없이 주더라도
    호출부가 순서에 의존할 수 있어야 한다.
    """
    stages = [row_to_stage(row) for row in rows]

    seen: dict[int, StageState] = {}
    for stage in stages:
        if stage.stage_no in seen:
            raise CorruptRowError(
                f"duplicate stage_no {stage.stage_no} in cycle {cycle_id}"
            )
        seen[stage.stage_no] = stage

    if ladder is not None:
        expected = set(range(1, ladder.max_stages + 1))
    else:
        expected = set(range(1, len(seen) + 1)) if seen else set()

    actual = set(seen)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise CorruptRowError(
            f"incomplete stage set for cycle {cycle_id}: "
            f"missing {missing}, unexpected {extra}"
        )

    if ladder is not None:
        for stage_no in sorted(seen):
            stage = seen[stage_no]
            expected_trigger = ladder.trigger_price(stage_no)
            if stage.trigger_price != expected_trigger:
                raise CorruptRowError(
                    f"trigger_price mismatch on stage {stage_no} of cycle "
                    f"{cycle_id}: row has {stage.trigger_price}, ladder computes "
                    f"{expected_trigger}"
                )

    return [seen[n] for n in sorted(seen)]
```

`Sequence` 를 `collections.abc` import 에 추가한다.

빈 목록의 처리에 주의한다. `ladder` 가 없고 `rows` 도 비면 `expected` 와 `actual` 이
둘 다 빈 집합이 되어 통과해버린다. 테스트 `test_an_empty_row_list_is_refused` 는
`ladder` 를 주므로 `expected` 가 1..7 이어서 거부된다. `ladder` 없이 빈 목록을 넘기는
호출부는 없으므로(사이클이 있으면 단계도 있다) 이 경로를 특별히 막지 않지만, 그
사실을 코드 주석에 적는다.

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/sqlite/test_mapping_stage.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (18 tests + 기존 전부)

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/adapters/sqlite/mapping.py tests/adapters/sqlite/test_mapping_stage.py
git commit -m "$(printf 'feat: stage_state 매핑과 완전성·trigger_price 대조 (H3·H4)\n\nPlan 1 이 Plan 2 로 넘긴 두 제약을 리포지토리 경계에서 강제한다.\n\nH3 — decide() 는 없는 단계를 조용히 건너뛰고 사다리 순서가 어긋난다. Plan 1 은\n도메인에서 막지 않기로 판단했다(Task 8·9 의 테스트가 부분 목록을 정당하게 쓴다).\n그 비용을 리포지토리가 진다 — rows_to_stages 는 완전한 집합만 반환한다.\n\nH4 — 설계서 4.2절이 같은 숫자를 ladder_json 과 stage_state.trigger_price 두 곳에\n쓰지만 스키마가 둘을 묶지 않는다. Plan 1 의 최종 리뷰가 재현한 손상은\ntrigger_price=999_999 인 행이 앵커보다 높은 가격의 매수를 만드는 것이었다.\ndecide() 의 대조는 메모리에 있는 상태를 보호하고, 이 대조는 손상된 행이 메모리에\n들어오는 것을 막는다.\n\n반환 순서를 stage_no 오름차순으로 보장한다 — DB 가 ORDER BY 없이 주더라도\n호출부가 순서에 의존할 수 있어야 한다.')"
```

---
### Task 8: `SqliteRepository` — 설정·사이클·단계

**Files:**
- Create: `src/autotrading7s/adapters/sqlite/repository.py`
- Test: `tests/adapters/sqlite/test_repository_core.py`

**Interfaces:**
- Consumes: `connect`·`apply_schema` (Task 4), `codec` (Task 5), `mapping` 전체 (Task 6·7), `RepositoryPort` (Task 3)
- Produces: `SqliteRepository(conn: sqlite3.Connection)` — `RepositoryPort` 의 설정·사이클·단계 부분

**구현할 메서드:** `save_config`, `load_config`, `list_configs`, `set_config_status`,
`create_cycle`, `load_cycle`, `save_cycle`, `load_active_cycles`, `load_stages`,
`save_stage`. 나머지는 Task 9·10 이 채운다 — 그때까지 `RepositoryPort` 를 만족하지
않는 것이 정상이며, Task 10 의 끝에서 `isinstance(repo, RepositoryPort)` 가 참이 된다.

**`load_stages` 가 Task 7 을 호출하는 방식이 중요하다.** 사이클을 먼저 로드해
사다리를 얻고, 그것을 `rows_to_stages` 에 넘긴다. 사이클 없이 단계만 로드하는
경로를 두지 않는다 — 그러면 H4 를 검사할 기준이 없다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/sqlite/test_repository_core.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import CorruptRowError
from autotrading7s.ports.repository import SplitConfig
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import Cycle, confirm_anchor, start
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState, to_buy_pending, to_holding
from autotrading7s.domain.types import CycleStatus, StageStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


@pytest.fixture()
def repo():
    conn = connect(":memory:")
    apply_schema(conn)
    yield SqliteRepository(conn)
    conn.close()


def a_config(**over) -> SplitConfig:
    kwargs = dict(
        config_id=None, stock_code="005930", stock_name="삼성전자", label="기본",
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=True, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="IDLE", created_at=T0, updated_at=T0,
    )
    kwargs.update(over)
    return SplitConfig(**kwargs)  # type: ignore[arg-type]


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def a_running_cycle(repo, config_id: int) -> Cycle:
    lad = a_ladder()
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=lad, at=T0)
    repo.save_cycle(cycle)
    for n in range(1, 8):
        repo.save_stage(cycle.cycle_id, StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=lad.trigger_price(n), planned_qty=lad.planned_qty(n)))
    return cycle


def test_config_save_and_load(repo):
    config_id = repo.save_config(a_config())
    loaded = repo.load_config(config_id)
    assert loaded.config_id == config_id
    assert loaded.stock_code == "005930"
    assert loaded.drop_pct == FIVE


def test_duplicate_stock_code_and_label_is_refused(repo):
    """설계서 1.1절이 종목별 복수 설정을 허용하지만 label 로 구분한다."""
    import sqlite3

    repo.save_config(a_config())
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_config(a_config())


def test_same_stock_with_a_different_label_is_allowed(repo):
    repo.save_config(a_config(label="기본"))
    repo.save_config(a_config(label="공격형"))
    assert len(repo.list_configs()) == 2


def test_set_config_status(repo):
    config_id = repo.save_config(a_config())
    repo.set_config_status(config_id, "ACTIVE")
    assert repo.load_config(config_id).status == "ACTIVE"


def test_create_cycle_starts_at_seq_one_and_status_starting(repo):
    config_id = repo.save_config(a_config())
    cycle = repo.create_cycle(config_id, started_at=T0)
    assert cycle.seq == 1
    assert cycle.status is CycleStatus.STARTING
    assert cycle.anchor_price is None and cycle.ladder is None


def test_create_cycle_increments_seq(repo):
    """사이클 이력이 보존되어야 종목별 누적 성과를 조회할 수 있다(설계서 D14)."""
    config_id = repo.save_config(a_config())
    first = repo.create_cycle(config_id, started_at=T0)
    second = repo.create_cycle(config_id, started_at=T0 + timedelta(days=1))
    assert (first.seq, second.seq) == (1, 2)
    assert first.cycle_id != second.cycle_id


def test_cycle_round_trip_through_the_database(repo):
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    loaded = repo.load_cycle(cycle.cycle_id)
    assert loaded.status is CycleStatus.RUNNING
    assert loaded.anchor_price == 10_000
    assert loaded.ladder is not None
    assert loaded.ladder.trigger_price(7) == a_ladder().trigger_price(7)


def test_load_active_cycles_excludes_closed(repo):
    from autotrading7s.domain.cycle import close
    from autotrading7s.domain.types import CloseReason

    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    assert [c.cycle_id for c in repo.load_active_cycles()] == [cycle.cycle_id]

    sold = [StageState(stage_no=n, status=StageStatus.SOLD,
                       trigger_price=a_ladder().trigger_price(n), planned_qty=1)
            for n in range(1, 8)]
    repo.save_cycle(close(cycle, reason=CloseReason.NORMAL, at=T0, states=sold))
    assert repo.load_active_cycles() == []


def test_load_stages_returns_the_complete_set_in_order(repo):
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    stages = repo.load_stages(cycle.cycle_id)
    assert [s.stage_no for s in stages] == [1, 2, 3, 4, 5, 6, 7]


def test_load_stages_refuses_an_incomplete_set(repo):
    """H3. 행을 직접 지워 리포지토리 밖의 손상을 시뮬레이션한다."""
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    repo._conn.execute(  # noqa: SLF001 — 손상 시뮬레이션이므로 의도적
        "DELETE FROM stage_state WHERE cycle_id = ? AND stage_no = 4",
        (cycle.cycle_id,))
    repo._conn.commit()
    with pytest.raises(CorruptRowError, match="incomplete"):
        repo.load_stages(cycle.cycle_id)


def test_load_stages_refuses_a_trigger_price_mismatch(repo):
    """H4. 같은 방식으로 컬럼을 직접 바꿔 손상을 시뮬레이션한다."""
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    repo._conn.execute(  # noqa: SLF001
        "UPDATE stage_state SET trigger_price = 999999 "
        "WHERE cycle_id = ? AND stage_no = 2", (cycle.cycle_id,))
    repo._conn.commit()
    with pytest.raises(CorruptRowError, match="trigger_price"):
        repo.load_stages(cycle.cycle_id)


def test_save_stage_upserts(repo):
    """같은 (cycle_id, stage_no) 를 두 번 저장하면 갱신이어야 한다 —
    UNIQUE 제약이 있으므로 INSERT 만 하면 두 번째가 실패한다."""
    config_id = repo.save_config(a_config())
    cycle = a_running_cycle(repo, config_id)
    lad = a_ladder()
    filled = to_holding(
        to_buy_pending(StageState(stage_no=2, status=StageStatus.WAITING,
                                  trigger_price=lad.trigger_price(2),
                                  planned_qty=lad.planned_qty(2))),
        fill_price=9_480, fill_qty=105, at=T0)
    repo.save_stage(cycle.cycle_id, filled)
    stages = repo.load_stages(cycle.cycle_id)
    assert stages[1].status is StageStatus.HOLDING
    assert stages[1].fill_price == 9_480
    assert len(stages) == 7


def test_load_stages_of_a_starting_cycle_skips_h4(repo):
    """STARTING 사이클은 사다리가 없으므로 대조 기준이 없다."""
    config_id = repo.save_config(a_config())
    cycle = repo.create_cycle(config_id, started_at=T0)
    lad = a_ladder()
    for n in range(1, 8):
        repo.save_stage(cycle.cycle_id, StageState(
            stage_no=n, status=StageStatus.WAITING,
            trigger_price=lad.trigger_price(n), planned_qty=lad.planned_qty(n)))
    stages = repo.load_stages(cycle.cycle_id)
    assert len(stages) == 7
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/sqlite/test_repository_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.adapters.sqlite.repository'`

**실행 중 발견된 빈틈 (커밋 e6ab49c).** 아래 코드 블록의 초안은 실제 SQLite 연결에서
동작하지 않았다. `mapping.py` 가 오류 귀속을 위해 `row.get("id")` 를 쓰는데
`connect()` 가 설정하는 `sqlite3.Row` 에는 `.get()` 이 없다
(`issubclass(sqlite3.Row, Mapping)` 은 `False`). 이 계획의 매핑 테스트는 평범한
`dict` 만 넘기므로 그 이음새가 검증되지 않았고, Task 8 이 첫 실제 통합 지점이었다.

**모든 fetch 지점에서 `dict(row)` 로 변환해 `mapping` 에 넘긴다.** 이것은 우회가
아니라 더 나은 선택이다 — `sqlite3.Row["없는키"]` 는 `IndexError` 를,
`dict["없는키"]` 는 `KeyError` 를 낸다. 변환하지 않으면 테스트와 운영의 예외
클래스가 달라져 테스트로 재현할 수 없는 실패 모드가 생긴다. Tasks 9·10 의 새 fetch
지점도 같이 변환해야 한다(잊으면 `AttributeError` 가 즉시 나므로 조용하지는 않다).

- [ ] **Step 3: `repository.py` 의 설정 부분 작성**

```python
"""SQLite 리포지토리 — `RepositoryPort` 의 구현.

메서드가 도메인 객체를 주고받는다. 변환과 제약 강제는 `mapping` 이 하며, 이 모듈은
SQL 과 트랜잭션 경계만 다룬다.

`load_stages` 는 사이클을 먼저 로드해 사다리를 얻은 뒤 `rows_to_stages` 에 넘긴다.
사이클 없이 단계만 로드하는 경로를 두지 않는다 — 그러면 H4 를 검사할 기준이 없다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from autotrading7s.adapters.sqlite.codec import dt_to_text
from autotrading7s.adapters.sqlite.mapping import (
    config_to_row,
    cycle_to_row,
    row_to_config,
    row_to_cycle,
    rows_to_stages,
    stage_to_row,
)
from autotrading7s.ports.repository import SplitConfig
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus


class SqliteRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ── 설정 ────────────────────────────────────────────────────────────
    def save_config(self, config: SplitConfig) -> int:
        row = config_to_row(config)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        with self._conn:
            cursor = self._conn.execute(
                f"INSERT INTO split_config ({columns}) VALUES ({placeholders})", row
            )
        return int(cursor.lastrowid)

    def load_config(self, config_id: int) -> SplitConfig:
        row = self._conn.execute(
            "SELECT * FROM split_config WHERE id = ?", (config_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no split_config with id {config_id}")
        return row_to_config(row)

    def list_configs(self) -> list[SplitConfig]:
        rows = self._conn.execute(
            "SELECT * FROM split_config ORDER BY id"
        ).fetchall()
        return [row_to_config(r) for r in rows]

    def set_config_status(self, config_id: int, status: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE split_config SET status = ?, updated_at = ? WHERE id = ?",
                (status, dt_to_text(datetime.now().astimezone()), config_id),
            )
```

`datetime.now().astimezone()` 이 tz-aware 를 보장한다. `datetime.now()` 만 쓰면
naive 가 되어 `dt_to_text` 가 거부한다 — 그것이 H2 가 의도한 동작이며, 여기서
`astimezone()` 을 붙이는 것이 올바른 대응이다.

- [ ] **Step 4: 사이클과 단계 부분 작성**

```python
    # ── 사이클 ──────────────────────────────────────────────────────────
    def create_cycle(self, config_id: int, started_at: datetime) -> Cycle:
        with self._conn:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM cycle "
                "WHERE config_id = ?", (config_id,)
            ).fetchone()
            seq = int(row["next_seq"])
            cursor = self._conn.execute(
                "INSERT INTO cycle (config_id, seq, status, started_at) "
                "VALUES (?, ?, ?, ?)",
                (config_id, seq, CycleStatus.STARTING.value, dt_to_text(started_at)),
            )
        return Cycle(
            cycle_id=int(cursor.lastrowid), config_id=config_id, seq=seq,
            status=CycleStatus.STARTING, started_at=started_at,
        )

    def load_cycle(self, cycle_id: int) -> Cycle:
        row = self._conn.execute(
            "SELECT * FROM cycle WHERE id = ?", (cycle_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no cycle with id {cycle_id}")
        return row_to_cycle(row)

    def save_cycle(self, cycle: Cycle) -> None:
        row = cycle_to_row(cycle)
        assignments = ", ".join(f"{k} = :{k}" for k in row)
        with self._conn:
            self._conn.execute(
                f"UPDATE cycle SET {assignments} WHERE id = :id",
                row | {"id": cycle.cycle_id},
            )

    def load_active_cycles(self) -> list[Cycle]:
        rows = self._conn.execute(
            "SELECT * FROM cycle WHERE status != ? ORDER BY id",
            (CycleStatus.CLOSED.value,),
        ).fetchall()
        return [row_to_cycle(r) for r in rows]

    # ── 단계 ────────────────────────────────────────────────────────────
    def load_stages(self, cycle_id: int) -> list[StageState]:
        cycle = self.load_cycle(cycle_id)
        rows = self._conn.execute(
            "SELECT * FROM stage_state WHERE cycle_id = ? ORDER BY stage_no",
            (cycle_id,),
        ).fetchall()
        return rows_to_stages(rows, cycle_id=cycle_id, ladder=cycle.ladder)

    def save_stage(self, cycle_id: int, stage: StageState) -> None:
        row = stage_to_row(cycle_id, stage)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        updates = ", ".join(
            f"{k} = :{k}" for k in row if k not in ("cycle_id", "stage_no")
        )
        with self._conn:
            self._conn.execute(
                f"INSERT INTO stage_state ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(cycle_id, stage_no) DO UPDATE SET {updates}",
                row,
            )
```

`ON CONFLICT ... DO UPDATE` 가 upsert 다. `UNIQUE(cycle_id, stage_no)` 가 있으므로
`INSERT` 만 하면 두 번째 저장이 실패한다. 단계 상태는 사이클 동안 여러 번 갱신되므로
upsert 가 맞다. `save_cycle` 은 `create_cycle` 이 먼저 행을 만들므로 `UPDATE` 로
충분하다.

`save_cycle` 이 `cycle_to_row` 에 없는 컬럼(`realized_pnl`, `forced_close_reason`,
`forced_close_qty`)을 건드리지 않는다. `realized_pnl` 은 Task 9 가, D20 컬럼들은
Plan 2B 의 강제 종료가 채운다. 그 사실을 `save_cycle` 의 docstring 에 적는다.

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/sqlite/test_repository_core.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (14 tests + 기존 전부)

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/adapters/sqlite/repository.py tests/adapters/sqlite/test_repository_core.py
git commit -m "$(printf 'feat: SqliteRepository 의 설정·사이클·단계\n\nload_stages 는 사이클을 먼저 로드해 사다리를 얻은 뒤 rows_to_stages 에 넘긴다.\n사이클 없이 단계만 로드하는 경로를 두지 않는다 — 그러면 H4 를 검사할 기준이 없다.\n\nH3·H4 를 리포지토리 경계에서 실제로 검증하는 테스트를 넣었다. 행을 직접 지우고\n컬럼을 직접 바꿔 리포지토리 밖의 손상을 시뮬레이션한다 — Plan 2 에서 실제로\n일어날 수 있는 것은 그런 종류이며, 리포지토리를 통한 정상 경로로는 만들 수 없다.\n\nsave_stage 는 upsert 다. UNIQUE(cycle_id, stage_no) 가 있고 단계 상태는 사이클\n동안 여러 번 갱신되므로 INSERT 만으로는 두 번째가 실패한다.')"
```

---

### Task 9: `SqliteRepository` — 주문 이력과 실현손익 (H5)

**Files:**
- Modify: `src/autotrading7s/adapters/sqlite/repository.py`
- Test: `tests/adapters/sqlite/test_repository_orders.py`

**Interfaces:**
- Consumes: Task 8의 `SqliteRepository`
- Produces: `append_order_log`, `update_order_log`, `load_pending_orders`, `realized_pnl_for_cycle`

**H5 가 여기서 착륙한다.** Plan 1의 최종 리뷰가 지적한 것: `cycle.realized_pnl` 이
스키마에 있지만 `after_sell` 이 `fill_price`·`fill_qty` 를 비우므로 **단계 상태만으로는
실현손익을 계산할 수 없다.** `order_log` 에서 집계해야 한다.

집계 규칙: 한 사이클의 `FILLED`·`PARTIAL` 주문 중 `side=SELL` 의 체결금액 합에서
`side=BUY` 의 체결금액 합을 뺀다. `path` 는 구분하지 않는다 — 긴급청산 매도도 실현이다.
수수료와 세금은 범위 밖이다(설계서 1.3절이 세금 계산 자동화를 배제했다).

**주의: 이 계산은 사이클이 완결되지 않아도 값을 낸다.** 보유가 남은 사이클의
`realized_pnl_for_cycle` 은 "지금까지 실현된 손익"이며 최종값이 아니다. 그 사실을
docstring 에 적는다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/sqlite/test_repository_orders.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from autotrading7s.ports.repository import SplitConfig
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import confirm_anchor
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import OrderPath, Side

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
    assert pending[0]["client_ref"] == ref
    assert pending[0]["status"] == "SENDING"


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
    assert [p["status"] for p in repo.load_pending_orders()] == ["UNKNOWN"]


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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/sqlite/test_repository_orders.py -v`
Expected: FAIL — `AttributeError: 'SqliteRepository' object has no attribute 'append_order_log'`

- [ ] **Step 3: 주문 이력 메서드 작성**

`SqliteRepository` 에 추가한다.

```python
    # ── 주문 이력 ───────────────────────────────────────────────────────
    _PENDING_STATUSES = ("SENDING", "ACCEPTED", "UNKNOWN")

    def append_order_log(
        self, *, client_ref: str, cycle_id: int, stage_state_id: int | None,
        side: Side, order_type: str, path: OrderPath, req_price: int | None,
        req_qty: int, trigger_reason: str, tick_price: int | None,
        tick_source: str | None, sent_at: datetime,
    ) -> int:
        """status=SENDING 으로 기록한다. 설계서 9절 ③ — 발주보다 먼저 커밋한다.

        순서를 뒤집으면 발주와 기록 사이에 프로세스가 죽었을 때 브로커에는 주문이
        있는데 우리는 모르는 고아 주문이 생기고, 다음 실행에서 중복 발주가 된다.
        """
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO order_log (client_ref, cycle_id, stage_state_id, "
                " side, order_type, path, req_price, req_qty, status, "
                " trigger_reason, tick_price, tick_source, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SENDING', ?, ?, ?, ?)",
                (client_ref, cycle_id, stage_state_id, side.value, order_type,
                 path.value, req_price, req_qty, trigger_reason, tick_price,
                 tick_source, dt_to_text(sent_at)),
            )
        return int(cursor.lastrowid)

    def update_order_log(
        self, *, client_ref: str, status: str, broker_order_id: str | None = None,
        fill_price: int | None = None, fill_qty: int | None = None,
        api_code: str | None = None, api_message: str | None = None,
        settled_at: datetime | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE order_log SET status = ?, "
                " broker_order_id = COALESCE(?, broker_order_id), "
                " fill_price = COALESCE(?, fill_price), "
                " fill_qty = COALESCE(?, fill_qty), "
                " api_code = COALESCE(?, api_code), "
                " api_message = COALESCE(?, api_message), "
                " settled_at = COALESCE(?, settled_at) "
                "WHERE client_ref = ?",
                (status, broker_order_id, fill_price, fill_qty, api_code,
                 api_message,
                 None if settled_at is None else dt_to_text(settled_at),
                 client_ref),
            )

    def load_pending_orders(self) -> list[dict[str, object]]:
        """SENDING·ACCEPTED·UNKNOWN 상태의 주문. 재시작 복구가 결말을 확인한다."""
        placeholders = ", ".join("?" for _ in self._PENDING_STATUSES)
        rows = self._conn.execute(
            f"SELECT * FROM order_log WHERE status IN ({placeholders}) ORDER BY id",
            self._PENDING_STATUSES,
        ).fetchall()
        return [dict(r) for r in rows]
```

`COALESCE(?, column)` 을 쓰는 이유는 부분 갱신을 지원하기 위해서다. `UNKNOWN` 으로
바꿀 때는 체결 정보가 없으므로 기존 값을 유지해야 한다.

`Side`·`OrderPath` import 를 추가한다.

- [ ] **Step 4: `realized_pnl_for_cycle` 작성**

```python
    def realized_pnl_for_cycle(self, cycle_id: int) -> int:
        """order_log 에서 집계한 실현손익 (H5).

        도메인에는 이 값이 없고 있을 수 없다 — `after_sell` 이 `fill_price` 와
        `fill_qty` 를 비우므로 단계 상태만으로는 계산할 수 없다(Plan 1 최종 리뷰
        handover 7). 그래서 주문 이력이 유일한 근거다.

        체결된 매도 금액 합에서 체결된 매수 금액 합을 뺀다. `PARTIAL` 도 센다 —
        부분 체결된 수량은 실제로 오간 것이다. `path` 는 구분하지 않는다: 긴급청산
        매도도 실현이다.

        수수료와 세금은 포함하지 않는다. 설계서 1.3절이 세금 계산 자동화를
        범위에서 배제했다.

        **보유가 남은 사이클에도 값을 낸다.** 그때 이 값은 "지금까지 실현된 손익"
        이며 최종값이 아니다. 사이클 종료 시점에 이 값을 `cycle.realized_pnl` 에
        기록하는 것은 호출자(Plan 2B 의 엔진)의 몫이다.
        """
        row = self._conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN side = 'SELL' "
            "                         THEN fill_price * fill_qty ELSE 0 END), 0) "
            "     - COALESCE(SUM(CASE WHEN side = 'BUY' "
            "                         THEN fill_price * fill_qty ELSE 0 END), 0) "
            "       AS pnl "
            "FROM order_log "
            "WHERE cycle_id = ? AND status IN ('FILLED', 'PARTIAL') "
            "  AND fill_price IS NOT NULL AND fill_qty IS NOT NULL",
            (cycle_id,),
        ).fetchone()
        return int(row["pnl"])
```

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/sqlite/test_repository_orders.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (14 tests + 기존 전부)

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/adapters/sqlite/repository.py tests/adapters/sqlite/test_repository_orders.py
git commit -m "$(printf 'feat: 주문 이력과 실현손익 집계 (H5)\n\nPlan 1 최종 리뷰의 handover 7. cycle.realized_pnl 이 스키마에 있지만 after_sell 이\nfill_price·fill_qty 를 비우므로 단계 상태만으로는 실현손익을 계산할 수 없다.\norder_log 가 유일한 근거다.\n\n체결된 매도 금액 합에서 체결된 매수 금액 합을 뺀다. PARTIAL 도 세고 path 는\n구분하지 않는다 — 긴급청산 매도도 실현이다. 수수료와 세금은 설계서 1.3절이\n범위에서 배제했다.\n\n보유가 남은 사이클에도 값을 내며 그때는 최종값이 아니다. 그 사실을 docstring 에\n적었다 — 호출자가 사이클 종료 시점에 기록해야 한다.\n\nappend_order_log 는 status=SENDING 으로 기록한다. 설계서 9절 ③ 이 발주보다 먼저\n커밋하라고 규정한 이유는, 순서를 뒤집으면 프로세스가 죽었을 때 브로커에는 주문이\n있는데 우리는 모르는 고아 주문이 생기고 다음 실행에서 중복 발주가 되기 때문이다.')"
```

---
### Task 10: `SqliteRepository` — 이력 로그와 `holdings` 뷰

**Files:**
- Modify: `src/autotrading7s/adapters/sqlite/repository.py`
- Test: `tests/adapters/sqlite/test_repository_logs.py`

**Interfaces:**
- Consumes: Task 8·9의 `SqliteRepository`, `HoldingRow` (Task 6)
- Produces: `append_emergency_log`, `append_reconcile_log`, `holdings` — 이 태스크의 끝에서 `isinstance(repo, RepositoryPort)` 가 참이 된다

**`holdings` 는 뷰를 읽어 `HoldingRow` 로 변환한다.** 현재가와 평가손익률은 없다 —
실시간 값이므로 UI 가 최신 틱과 결합해 `domain/pnl.py` 로 계산한다(설계서 12.3절).

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/sqlite/test_repository_logs.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.ports.repository import SplitConfig
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import confirm_anchor
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus
from autotrading7s.ports.repository import RepositoryPort

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def a_ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


@pytest.fixture()
def repo():
    conn = connect(":memory:")
    apply_schema(conn)
    yield SqliteRepository(conn)
    conn.close()


def seed(repo, *, stock_code="005930", label="기본", holdings=()) -> int:
    """설정과 RUNNING 사이클을 만들고, holdings 에 (stage_no, fill, qty) 를 채운다."""
    config_id = repo.save_config(SplitConfig(
        config_id=None, stock_code=stock_code, stock_name="삼성전자", label=label,
        max_stages=7, drop_pct=FIVE, target_pct=FIVE, amount_per_stage=1_000_000,
        allow_rebuy=True, rebuy_cooldown_sec=60, total_limit=7_000_000,
        status="ACTIVE", created_at=T0, updated_at=T0))
    lad = a_ladder()
    cycle = repo.create_cycle(config_id, started_at=T0)
    cycle = confirm_anchor(cycle, anchor_price=10_000, ladder=lad, at=T0)
    repo.save_cycle(cycle)
    held = {n: (fill, qty) for n, fill, qty in holdings}
    for n in range(1, 8):
        if n in held:
            fill, qty = held[n]
            stage = StageState(stage_no=n, status=StageStatus.HOLDING,
                               trigger_price=lad.trigger_price(n),
                               planned_qty=lad.planned_qty(n),
                               fill_price=fill, fill_qty=qty, bought_at=T0)
        else:
            stage = StageState(stage_no=n, status=StageStatus.WAITING,
                               trigger_price=lad.trigger_price(n),
                               planned_qty=lad.planned_qty(n))
        repo.save_stage(cycle.cycle_id, stage)
    return cycle.cycle_id


def test_repository_satisfies_the_port(repo):
    """Task 3 이 고정한 목록을 이제 전부 채웠다."""
    assert isinstance(repo, RepositoryPort)


def test_emergency_log_round_trip(repo):
    cycle_id = seed(repo, holdings=[(1, 10_000, 100)])
    log_id = repo.append_emergency_log(
        scope="SINGLE", stock_code="005930", cycle_id=cycle_id, requested_at=T0,
        reason="실적 쇼크", qty_before=100, qty_after=0, canceled_orders=2,
        result="SUCCESS", detail_json=None, completed_at=T0)
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT * FROM emergency_liquidation_log WHERE id = ?", (log_id,)
    ).fetchone()
    assert row["reason"] == "실적 쇼크"
    assert row["result"] == "SUCCESS"
    assert row["qty_before"] == 100


def test_emergency_log_accepts_forced_close_result(repo):
    """D20 — 강제 종료가 이 이력에 result=FORCED_CLOSE 로 기록된다."""
    cycle_id = seed(repo, holdings=[(1, 10_000, 100)])
    repo.append_emergency_log(
        scope="SINGLE", stock_code="005930", cycle_id=cycle_id, requested_at=T0,
        reason="거래정지로 청산 불가, 잔량 40주 직접 처리 예정", qty_before=100,
        qty_after=40, canceled_orders=1, result="FORCED_CLOSE",
        detail_json='{"attempts": 3}', completed_at=T0)
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT result, qty_after FROM emergency_liquidation_log"
    ).fetchone()
    assert (row["result"], row["qty_after"]) == ("FORCED_CLOSE", 40)


def test_emergency_log_refuses_an_unknown_result(repo):
    import sqlite3

    cycle_id = seed(repo)
    with pytest.raises(sqlite3.IntegrityError):
        repo.append_emergency_log(
            scope="SINGLE", stock_code="005930", cycle_id=cycle_id,
            requested_at=T0, reason=None, qty_before=None, qty_after=None,
            canceled_orders=None, result="BOGUS", detail_json=None,
            completed_at=None)


def test_reconcile_log_round_trip(repo):
    seed(repo)
    repo.append_reconcile_log(
        checked_at=T0, stock_code="005930", internal_qty=316, broker_qty=316,
        verdict="MATCH", action_taken=None)
    repo.append_reconcile_log(
        checked_at=T0, stock_code="005930", internal_qty=316, broker_qty=200,
        verdict="INTERNAL_MORE", action_taken="PAUSED")
    rows = repo._conn.execute(  # noqa: SLF001
        "SELECT verdict, action_taken FROM reconcile_log ORDER BY id").fetchall()
    assert [(r["verdict"], r["action_taken"]) for r in rows] == [
        ("MATCH", None), ("INTERNAL_MORE", "PAUSED")]


def test_holdings_is_empty_when_nothing_is_held(repo):
    seed(repo)
    assert repo.holdings() == []


def test_holdings_aggregates_one_stock():
    """설계서 14.1절 목업의 삼성전자: 3단계 보유, 316주, 평단 9,458원."""
    conn = connect(":memory:")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    seed(repo, holdings=[(1, 10_000, 100), (2, 9_480, 105), (3, 8_950, 111)])
    rows = repo.holdings()
    assert len(rows) == 1
    row = rows[0]
    assert row.stock_code == "005930"
    assert row.total_qty == 316
    # 9,458 — 이 목업은 소수부가 0.386 이라 절사와 반올림이 같다.
    # 절사를 실제로 가르는 것은 아래 test_holdings_avg_price_truncates 다.
    assert row.avg_price == 2_988_850 // 316
    assert row.holding_stages == 3
    assert row.max_stages == 7
    assert row.cycle_status is CycleStatus.RUNNING


def test_holdings_avg_price_truncates(repo):
    """뷰의 평단은 SQL 정수 나눗셈이라 절사다 — 도메인의 half-up 반올림과 다르다."""
    seed(repo, holdings=[(1, 10_000, 100), (2, 9_400, 103)])
    invested, qty = 10_000 * 100 + 9_400 * 103, 203
    # 이 조합은 소수부가 0.5 를 넘으므로 절사와 반올림이 1원 갈린다.
    assert invested / qty > invested // qty + 0.5
    assert repo.holdings()[0].avg_price == 9_695   # 반올림이면 9,696 이다


def test_holdings_counts_sell_pending_as_held():
    """매도 주문이 나갔어도 체결 전까지는 보유다."""
    from autotrading7s.domain.stage import to_sell_pending

    conn = connect(":memory:")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    cycle_id = seed(repo, holdings=[(1, 10_000, 100)])
    lad = a_ladder()
    held = StageState(stage_no=1, status=StageStatus.HOLDING,
                      trigger_price=lad.trigger_price(1),
                      planned_qty=lad.planned_qty(1),
                      fill_price=10_000, fill_qty=100, bought_at=T0)
    repo.save_stage(cycle_id, to_sell_pending(held))
    rows = repo.holdings()
    assert len(rows) == 1 and rows[0].total_qty == 100
    conn.close()


def test_holdings_lists_multiple_stocks():
    conn = connect(":memory:")
    apply_schema(conn)
    repo = SqliteRepository(conn)
    seed(repo, stock_code="005930", label="기본", holdings=[(1, 10_000, 100)])
    seed(repo, stock_code="035720", label="공격형", holdings=[(1, 10_000, 100)])
    assert {r.stock_code for r in repo.holdings()} == {"005930", "035720"}
    conn.close()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/sqlite/test_repository_logs.py -v`
Expected: FAIL — `AttributeError: 'SqliteRepository' object has no attribute 'append_emergency_log'`

- [ ] **Step 3: 이력 로그와 뷰 메서드 작성**

```python
    # ── 이력 로그 ───────────────────────────────────────────────────────
    def append_emergency_log(
        self, *, scope: str, stock_code: str | None, cycle_id: int | None,
        requested_at: datetime, reason: str | None, qty_before: int | None,
        qty_after: int | None, canceled_orders: int | None, result: str,
        detail_json: str | None, completed_at: datetime | None,
    ) -> int:
        """긴급청산 이력. 설계서 11.1절 ⑥ 과 D20 의 강제 종료(result=FORCED_CLOSE)."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO emergency_liquidation_log (scope, stock_code, "
                " cycle_id, requested_at, reason, qty_before, qty_after, "
                " canceled_orders, result, detail_json, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (scope, stock_code, cycle_id, dt_to_text(requested_at), reason,
                 qty_before, qty_after, canceled_orders, result, detail_json,
                 None if completed_at is None else dt_to_text(completed_at)),
            )
        return int(cursor.lastrowid)

    def append_reconcile_log(
        self, *, checked_at: datetime, stock_code: str, internal_qty: int,
        broker_qty: int, verdict: str, action_taken: str | None,
    ) -> int:
        """대사 이력. 설계서 10.2절 — 일치는 로그 없음이 원칙이지만, 이력
        테이블에는 남겨 사후에 대사가 실제로 돌았는지 확인할 수 있게 한다."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO reconcile_log (checked_at, stock_code, "
                " internal_qty, broker_qty, verdict, action_taken) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (dt_to_text(checked_at), stock_code, internal_qty, broker_qty,
                 verdict, action_taken),
            )
        return int(cursor.lastrowid)

    # ── 보유현황 뷰 ─────────────────────────────────────────────────────
    def holdings(self) -> list[HoldingRow]:
        """설계서 12.3절의 뷰를 읽어 HoldingRow 로 변환한다.

        현재가와 평가손익률은 없다 — 실시간 값이므로 UI 가 최신 틱과 결합해
        `domain/pnl.py` 의 순수 함수로 계산한다.
        """
        rows = self._conn.execute(
            "SELECT * FROM holdings ORDER BY stock_code, label"
        ).fetchall()
        return [
            HoldingRow(
                stock_code=r["stock_code"],
                stock_name=r["stock_name"],
                label=r["label"],
                cycle_id=r["cycle_id"],
                total_qty=int(r["total_qty"]),
                avg_price=int(r["avg_price"]),
                holding_stages=int(r["holding_stages"]),
                max_stages=int(r["max_stages"]),
                cycle_status=CycleStatus(r["cycle_status"]),
            )
            for r in rows
        ]
```

`HoldingRow` import 를 추가한다.

뷰의 `avg_price` 는 SQL 의 정수 나눗셈이므로 절사다. `domain/pnl.py` 의 `avg_price` 는
half-up 반올림이다. **투입금액을 수량으로 나눈 소수부가 0.5 이상일 때 두 값이 1원
갈린다.** 설계서 14.1절의 목업(삼성전자 2,988,850/316, 카카오 6,982,500/833)은 소수부가
각각 0.386·0.353 이라 **두 방식의 결과가 같다** — 그래서 목업 어서션만으로는 절사가
고정되지 않고, `test_holdings_avg_price_truncates` 가 소수부 0.566 인 조합으로 그것을
가른다. UI 는 뷰의 `avg_price` 를 표시용으로 쓰되 손익 계산에는 `domain/pnl.py` 를 써야
한다. 이 차이를 `holdings` 의 docstring 에 적는다.

- [ ] **Step 4: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/sqlite/test_repository_logs.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (10 tests + 기존 전부). 특히 `test_repository_satisfies_the_port` 가
통과해야 한다 — Task 3 이 고정한 17개 메서드를 전부 채웠다는 뜻이다.

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/adapters/sqlite/repository.py tests/adapters/sqlite/test_repository_logs.py
git commit -m "$(printf 'feat: 긴급청산·대사 이력과 holdings 뷰\n\nTask 3 이 고정한 17개 메서드를 전부 채웠다 — isinstance(repo, RepositoryPort) 가\n이제 참이다.\n\n긴급청산 이력은 D20 의 강제 종료를 result=FORCED_CLOSE 로 받는다. 스키마의 CHECK\n제약이 알 수 없는 result 를 거부하는 것도 테스트로 고정했다.\n\nholdings 뷰의 avg_price 는 SQL 정수 나눗셈이므로 절사이고 domain/pnl.py 의\navg_price 는 half-up 반올림이다 — 소수부가 0.5 이상일 때 1원 갈린다. 설계서\n목업은 소수부가 0.5 미만이라 두 방식이 같으므로, 절사를 실제로 가르는 테스트를\n따로 두었다. UI 는 뷰의 값을\n표시용으로 쓰되 손익 계산에는 도메인 함수를 써야 한다. 그 차이를 docstring 에\n적고 테스트도 절사를 기대한다.')"
```

---

### Task 11: `FakeBroker` — 시세 재생과 체결 모드

**Files:**
- Create: `src/autotrading7s/adapters/fake/broker.py`
- Test: `tests/adapters/test_fake_broker.py`

**Interfaces:**
- Consumes: `BrokerPort` (Task 2), `Tick`·`OrderAck`·`OrderStatus`·`Balance`·`Holding`·`CancelAck`·`FillState` (`domain/types.py`)
- Produces:
  - `FillMode` — `INSTANT | DELAYED | PARTIAL | NEVER`
  - `FakeBroker(script: list[int], *, code: str = "005930", fill_mode: FillMode = FillMode.INSTANT, partial_ratio: Decimal = Decimal("0.4"), delay_ticks: int = 3, cash: int = 100_000_000)`
  - `BrokerPort` 의 8개 메서드 전부

**설계서 8.5절이 이 클래스의 목적을 적었다.** API 키 부재라는 임시 사정이 아니라,
**모의투자로는 재현할 수 없는 실패 경로**를 검증하는 것이다 — 갭하락을 주문해서 만들 수
없고, 응답 타임아웃을 유발할 수 없고, WebSocket 을 끊었다 붙일 수도 없다.

**결정론적이어야 한다.** 같은 스크립트에 같은 모드면 항상 같은 결과가 나와야 한다.
난수를 쓰지 않고, 시간에 의존하지 않는다 — `delay_ticks` 는 실제 시간이 아니라
**소비된 틱 수**로 센다.

이 태스크는 체결 모드만 다룬다. 실패 모드는 Task 12 다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/test_fake_broker.py`**

```python
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.domain.types import (
    FillState,
    LimitOrderRequest,
    MarketSellRequest,
    Side,
)
from autotrading7s.ports.broker import BrokerPort

pytestmark = pytest.mark.asyncio


def a_buy(price: int = 9_500, qty: int = 105) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.BUY, qty=qty, price=price,
                             client_ref=uuid4())


def a_sell(price: int = 10_500, qty: int = 100) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.SELL, qty=qty, price=price,
                             client_ref=uuid4())


def test_satisfies_the_broker_port():
    assert isinstance(FakeBroker([9_500]), BrokerPort)


async def test_subscribe_replays_the_script_in_order():
    broker = FakeBroker([9_500, 9_000, 8_500])
    prices = [tick.price async for tick in broker.subscribe_quotes(["005930"])]
    assert prices == [9_500, 9_000, 8_500]


async def test_ticks_carry_the_code_and_an_aware_timestamp():
    broker = FakeBroker([9_500])
    ticks = [t async for t in broker.subscribe_quotes(["005930"])]
    assert ticks[0].code == "005930"
    assert ticks[0].at.tzinfo is not None


async def test_get_price_returns_the_last_replayed_tick():
    broker = FakeBroker([9_500, 9_000])
    async for _ in broker.subscribe_quotes(["005930"]):
        pass
    assert await broker.get_price("005930") == 9_000


async def test_get_price_before_any_tick_uses_the_first_script_entry():
    broker = FakeBroker([9_500, 9_000])
    assert await broker.get_price("005930") == 9_500


async def test_instant_fill_completes_immediately():
    broker = FakeBroker([9_500], fill_mode=FillMode.INSTANT)
    req = a_buy()
    ack = await broker.place_limit_order(req)
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.FILLED
    assert status.filled_qty == req.qty
    assert status.filled_price == req.price
    assert status.client_ref == req.client_ref


async def test_instant_fill_updates_the_balance():
    broker = FakeBroker([9_500], fill_mode=FillMode.INSTANT, cash=10_000_000)
    await broker.place_limit_order(a_buy(price=9_500, qty=105))
    balance = await broker.get_balance()
    assert balance.qty_of("005930") == 105
    assert balance.cash == 10_000_000 - 9_500 * 105


async def test_selling_reduces_the_position_and_adds_cash():
    broker = FakeBroker([10_500], fill_mode=FillMode.INSTANT, cash=10_000_000)
    await broker.place_limit_order(a_buy(price=9_500, qty=105))
    await broker.place_limit_order(a_sell(price=10_500, qty=105))
    balance = await broker.get_balance()
    assert balance.qty_of("005930") == 0
    assert balance.cash == 10_000_000 - 9_500 * 105 + 10_500 * 105


async def test_never_mode_leaves_the_order_open():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    ack = await broker.place_limit_order(a_buy())
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.OPEN
    assert status.filled_qty == 0


async def test_partial_mode_fills_the_configured_ratio():
    broker = FakeBroker([9_500], fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"))
    ack = await broker.place_limit_order(a_buy(qty=105))
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.PARTIAL
    assert status.filled_qty == 42          # floor(105 × 0.4)
    assert status.filled_qty < 105


async def test_partial_mode_never_fills_zero():
    """수량이 작아 floor 가 0 이 되면 최소 1주는 체결한다 — 0주 부분체결은
    도메인의 StageState 불변식이 거부하는 상태를 만든다."""
    broker = FakeBroker([9_500], fill_mode=FillMode.PARTIAL,
                        partial_ratio=Decimal("0.4"))
    ack = await broker.place_limit_order(a_buy(qty=1))
    status = await broker.get_order(ack.broker_order_id)
    assert status.filled_qty == 1
    assert status.state is FillState.FILLED


async def test_delayed_mode_fills_after_the_configured_tick_count():
    """delay_ticks 는 실제 시간이 아니라 소비된 틱 수로 센다 — 결정론을 위해서다."""
    broker = FakeBroker([9_500, 9_400, 9_300, 9_200],
                        fill_mode=FillMode.DELAYED, delay_ticks=2)
    ack = await broker.place_limit_order(a_buy())
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.OPEN
    consumed = 0
    async for _ in broker.subscribe_quotes(["005930"]):
        consumed += 1
        if consumed == 2:
            break
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.FILLED


async def test_cancel_moves_an_open_order_to_canceled():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    ack = await broker.place_limit_order(a_buy())
    cancel = await broker.cancel_order(ack.broker_order_id)
    assert cancel.broker_order_id == ack.broker_order_id
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.CANCELED


async def test_cancel_of_a_filled_order_is_refused():
    broker = FakeBroker([9_500], fill_mode=FillMode.INSTANT)
    ack = await broker.place_limit_order(a_buy())
    with pytest.raises(ValueError, match="already"):
        await broker.cancel_order(ack.broker_order_id)


async def test_market_sell_fills_at_the_current_price():
    broker = FakeBroker([9_340], fill_mode=FillMode.INSTANT, cash=0)
    await broker.place_limit_order(a_buy(price=10_000, qty=100))
    ack = await broker.place_market_sell(MarketSellRequest(
        code="005930", qty=100, client_ref=uuid4(), reason="긴급"))
    status = await broker.get_order(ack.broker_order_id)
    assert status.state is FillState.FILLED
    assert status.filled_price == 9_340


async def test_list_orders_today_includes_every_order():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    first = await broker.place_limit_order(a_buy())
    second = await broker.place_limit_order(a_buy(price=9_000))
    orders = await broker.list_orders_today("005930")
    assert {o.broker_order_id for o in orders} == {
        first.broker_order_id, second.broker_order_id}


async def test_list_orders_today_filters_by_code():
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    await broker.place_limit_order(a_buy())
    assert await broker.list_orders_today("035720") == []
    assert len(await broker.list_orders_today(None)) == 1


async def test_client_ref_survives_so_unknown_reconciliation_works():
    """설계서 9절 ⑤ — 응답 타임아웃 후 client_ref 로 접수 여부를 확인한다."""
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER)
    req = a_buy()
    await broker.place_limit_order(req)
    orders = await broker.list_orders_today("005930")
    assert [o.client_ref for o in orders] == [req.client_ref]


async def test_the_same_script_and_mode_gives_the_same_result_twice():
    """결정론 — 난수도 시간 의존도 없다."""
    async def run() -> tuple[int, ...]:
        broker = FakeBroker([9_500, 9_000], fill_mode=FillMode.PARTIAL)
        ack = await broker.place_limit_order(a_buy(qty=105))
        status = await broker.get_order(ack.broker_order_id)
        return (status.filled_qty, status.filled_price or 0)

    assert await run() == await run()
```

`pytest-asyncio` 가 이미 `.venv` 에 설치되어 있다. `pyproject.toml` 의
`[tool.pytest.ini_options]` 에 `asyncio_mode = "strict"` 를 추가하고 위처럼
`pytestmark = pytest.mark.asyncio` 를 쓴다. `strict` 를 쓰는 이유는 `auto` 가 동기
테스트까지 감싸려 시도해 기존 453개에 영향을 줄 수 있기 때문이다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/test_fake_broker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.adapters.fake.broker'`

- [ ] **Step 3: `pyproject.toml` 에 asyncio 모드 추가**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q --strict-markers"
asyncio_mode = "strict"
```

- [ ] **Step 4: `src/autotrading7s/adapters/fake/broker.py` 작성**

```python
"""시뮬레이션 브로커 — 설계서 8.5절.

목적은 API 키 부재라는 임시 사정이 아니다. **모의투자로는 재현할 수 없는 실패
경로**를 검증하는 것이다 — 갭하락을 주문해서 만들 수 없고, 응답 타임아웃을 유발할
수 없고, WebSocket 을 끊었다 붙일 수도 없다.

그래서 검증이 두 층으로 나뉜다: 로직 정확성은 이 브로커로, 사양 적합성은 모의투자로.

**결정론적이다.** 같은 스크립트에 같은 모드면 항상 같은 결과가 나온다. 난수를 쓰지
않고 시간에 의존하지 않는다 — `delay_ticks` 는 실제 시간이 아니라 소비된 틱 수로
센다. 시간에 의존하면 느린 CI 에서 테스트가 흔들린다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from autotrading7s.domain.types import (
    Balance,
    CancelAck,
    FillState,
    Holding,
    LimitOrderRequest,
    MarketSellRequest,
    OrderAck,
    OrderStatus,
    Side,
    Tick,
    TickSource,
)

_EPOCH = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


class FillMode(Enum):
    INSTANT = "INSTANT"
    DELAYED = "DELAYED"
    PARTIAL = "PARTIAL"
    NEVER = "NEVER"


@dataclass
class _Order:
    broker_order_id: str
    client_ref: UUID
    code: str
    side: Side
    qty: int
    price: int | None          # None 이면 시장가
    state: FillState
    filled_qty: int = 0
    filled_price: int | None = None
    fill_at_tick: int | None = None   # DELAYED 모드에서 체결될 틱 번호


class FakeBroker:
    def __init__(
        self,
        script: list[int],
        *,
        code: str = "005930",
        fill_mode: FillMode = FillMode.INSTANT,
        partial_ratio: Decimal = Decimal("0.4"),
        delay_ticks: int = 3,
        cash: int = 100_000_000,
    ) -> None:
        if not script:
            raise ValueError("script must not be empty")
        self._script = list(script)
        self._code = code
        self._fill_mode = fill_mode
        self._partial_ratio = partial_ratio
        self._delay_ticks = delay_ticks
        self._cash = cash
        self._orders: dict[str, _Order] = {}
        self._positions: dict[str, tuple[int, int]] = {}   # code → (qty, 취득원가합)
        self._ticks_consumed = 0
        self._next_id = 1

    # ── 시세 ────────────────────────────────────────────────────────────
    def subscribe_quotes(self, codes: list[str]) -> AsyncIterator[Tick]:
        return self._replay()

    async def _replay(self) -> AsyncIterator[Tick]:
        for price in self._script[self._ticks_consumed:]:
            self._ticks_consumed += 1
            self._settle_delayed()
            yield Tick(
                code=self._code,
                price=price,
                at=_EPOCH + timedelta(seconds=self._ticks_consumed),
                source=TickSource.WS,
            )

    async def get_price(self, code: str) -> int:
        """마지막으로 재생된 틱. 아직 없으면 스크립트의 첫 값."""
        index = max(0, self._ticks_consumed - 1)
        return self._script[index]

    def _current_price(self) -> int:
        index = max(0, self._ticks_consumed - 1)
        return self._script[index]

    # ── 주문 ────────────────────────────────────────────────────────────
    async def place_limit_order(self, req: LimitOrderRequest) -> OrderAck:
        return self._accept(req.client_ref, req.code, req.side, req.qty, req.price)

    async def place_market_sell(self, req: MarketSellRequest) -> OrderAck:
        return self._accept(req.client_ref, req.code, Side.SELL, req.qty, None)

    def _accept(
        self, client_ref: UUID, code: str, side: Side, qty: int, price: int | None
    ) -> OrderAck:
        broker_order_id = f"FAKE-{self._next_id}"
        self._next_id += 1
        order = _Order(
            broker_order_id=broker_order_id, client_ref=client_ref, code=code,
            side=side, qty=qty, price=price, state=FillState.OPEN,
        )
        self._orders[broker_order_id] = order

        if price is None or self._fill_mode is FillMode.INSTANT:
            # 시장가는 모드와 무관하게 즉시 체결한다 — 실제 시장가의 성질이다.
            self._fill(order, qty)
        elif self._fill_mode is FillMode.PARTIAL:
            partial = int(Decimal(qty) * self._partial_ratio)
            # 0주 부분체결은 도메인의 StageState 불변식이 거부하는 상태를 만든다.
            self._fill(order, max(1, partial))
        elif self._fill_mode is FillMode.DELAYED:
            order.fill_at_tick = self._ticks_consumed + self._delay_ticks
        # NEVER 는 아무것도 하지 않는다 — OPEN 으로 남는다.

        return OrderAck(client_ref=client_ref, broker_order_id=broker_order_id,
                        accepted_at=_EPOCH)

    def _fill(self, order: _Order, qty: int) -> None:
        price = order.price if order.price is not None else self._current_price()
        order.filled_qty = qty
        order.filled_price = price
        order.state = FillState.FILLED if qty == order.qty else FillState.PARTIAL

        held_qty, held_cost = self._positions.get(order.code, (0, 0))
        if order.side is Side.BUY:
            self._cash -= price * qty
            self._positions[order.code] = (held_qty + qty, held_cost + price * qty)
        else:
            self._cash += price * qty
            unit_cost = 0 if held_qty == 0 else held_cost // held_qty
            remaining = max(0, held_qty - qty)
            self._positions[order.code] = (remaining, unit_cost * remaining)

    def _settle_delayed(self) -> None:
        for order in self._orders.values():
            if (order.state is FillState.OPEN
                    and order.fill_at_tick is not None
                    and self._ticks_consumed >= order.fill_at_tick):
                self._fill(order, order.qty)

    async def cancel_order(self, broker_order_id: str) -> CancelAck:
        order = self._orders[broker_order_id]
        if order.state in (FillState.FILLED, FillState.CANCELED):
            raise ValueError(
                f"order {broker_order_id} is already {order.state.value}"
            )
        order.state = FillState.CANCELED
        return CancelAck(broker_order_id=broker_order_id, canceled_at=_EPOCH)

    async def get_order(self, broker_order_id: str) -> OrderStatus:
        order = self._orders[broker_order_id]
        return OrderStatus(
            client_ref=order.client_ref, broker_order_id=order.broker_order_id,
            state=order.state, filled_qty=order.filled_qty,
            filled_price=order.filled_price,
        )

    async def list_orders_today(self, code: str | None) -> list[OrderStatus]:
        return [
            await self.get_order(o.broker_order_id)
            for o in self._orders.values()
            if code is None or o.code == code
        ]

    # ── 잔고 ────────────────────────────────────────────────────────────
    async def get_balance(self) -> Balance:
        holdings = tuple(
            Holding(code=code, qty=qty,
                    avg_price=0 if qty == 0 else cost // qty)
            for code, (qty, cost) in sorted(self._positions.items())
        )
        return Balance(cash=self._cash, holdings=holdings)
```

`Holding` 의 `qty=0` 인 항목이 잔고에 남는 것이 실제 증권사 응답과 다를 수 있다.
`Balance.qty_of` 가 없는 종목에 0 을 반환하므로(Plan 1 최종 리뷰의 handover 5)
구별이 안 되는데, **여기서는 오히려 그 구별을 테스트할 수 있게 남겨둔다** — Plan 2B 가
"응답에 없음"과 "보유 0"을 구분해야 하고, 이 브로커가 두 상황을 다 만들 수 있어야 한다.
`_positions` 에서 항목을 지우면 "응답에 없음"이 되고, `qty=0` 으로 두면 "보유 0"이 된다.
현재 구현은 후자를 만든다. 그 사실을 `get_balance` 의 docstring 에 적는다.

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/test_fake_broker.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (19 tests + 기존 전부). `asyncio_mode = "strict"` 추가가 기존 453개에
영향을 주지 않아야 한다 — 하나라도 깨지면 멈추고 보고한다.

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/adapters/fake/broker.py tests/adapters/test_fake_broker.py pyproject.toml
git commit -m "$(printf 'feat: 시뮬레이션 브로커 — 시세 재생과 체결 모드\n\n설계서 8.5절. 목적은 API 키 부재가 아니라 모의투자로는 재현할 수 없는 실패 경로다 —\n갭하락을 주문해서 만들 수 없고 응답 타임아웃을 유발할 수 없다.\n\n결정론적이다. delay_ticks 를 실제 시간이 아니라 소비된 틱 수로 센다 — 시간에\n의존하면 느린 CI 에서 테스트가 흔들린다. 같은 스크립트에 같은 모드면 항상 같은\n결과가 나오는 것을 테스트로 고정했다.\n\nPARTIAL 모드가 0주를 체결하지 않는다. floor 가 0 이 되면 최소 1주를 체결한다 —\n0주 부분체결은 도메인의 StageState 불변식이 거부하는 상태를 만든다.\n\n시장가는 fill_mode 와 무관하게 즉시 체결한다. 실제 시장가의 성질이며, 긴급청산\n경로를 NEVER 모드에서도 검증할 수 있어야 한다.')"
```

---
### Task 12: `FakeBroker` — 실패 모드

**Files:**
- Modify: `src/autotrading7s/adapters/fake/broker.py`
- Test: `tests/adapters/test_fake_broker_failures.py`

**Interfaces:**
- Consumes: Task 11의 `FakeBroker`·`FillMode`
- Produces:
  - `FailMode` — `NONE | TIMEOUT | REJECT | DISCONNECT`
  - `BrokerTimeout(Exception)` — 응답이 오지 않음. `TimeoutError` 를 상속하지 않는다(아래 근거)
  - `BrokerRejected(Exception)` — 브로커가 명시적으로 거부. `code`·`message` 속성
  - `BrokerDisconnected(Exception)` — 스트림 끊김
  - `FakeBroker(..., fail_mode: FailMode = FailMode.NONE, fail_after: int = 0)`

**이 태스크가 설계서 9절 ⑤ 를 검증 가능하게 만든다.** 그 분기는 이 프로젝트에서
가장 중요한 다섯 줄이다 — 주문을 보냈는데 응답이 오지 않았을 때 **재발주하지 않고
조회로 사실을 확인한다.** 그것을 테스트하려면 응답이 오지 않는 상황을 만들 수 있어야
하고, 모의투자로는 만들 수 없다.

**핵심 설계: `TIMEOUT` 은 주문을 접수한 뒤 예외를 던진다.** 실제 타임아웃의 성질이
그렇다 — 요청이 서버에 도달했는지 알 수 없고, 도달했을 수도 있다. 그래서 예외를
던지면서도 내부적으로는 주문을 등록해, `list_orders_today` 가 그것을 보여준다.
이것이 설계서 9절 ⑤ 의 "접수됨" 분기를 테스트할 수 있게 하는 장치다.

`fail_after` 로 "N번째 호출부터 실패"를 만들 수 있다. 첫 주문은 성공하고 두 번째가
타임아웃하는 시나리오가 필요하기 때문이다.

**`BrokerTimeout` 이 `TimeoutError` 를 상속하지 않는 이유:** `asyncio.wait_for` 가
`TimeoutError` 를 던지므로, 상속하면 엔진의 `except BrokerTimeout` 이 asyncio 자체의
타임아웃까지 잡는다. 둘은 다른 사건이다 — 전자는 브로커가 답하지 않은 것이고 후자는
우리 쪽 대기 한도를 넘긴 것이다. 구분이 필요하므로 별도 계층으로 둔다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/test_fake_broker_failures.py`**

```python
from __future__ import annotations

from uuid import uuid4

import pytest

from autotrading7s.adapters.fake.broker import (
    BrokerDisconnected,
    BrokerRejected,
    BrokerTimeout,
    FailMode,
    FakeBroker,
    FillMode,
)
from autotrading7s.domain.types import FillState, LimitOrderRequest, Side

pytestmark = pytest.mark.asyncio


def a_buy(price: int = 9_500, qty: int = 105) -> LimitOrderRequest:
    return LimitOrderRequest(code="005930", side=Side.BUY, qty=qty, price=price,
                             client_ref=uuid4())


async def test_timeout_raises():
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(a_buy())


async def test_timeout_does_not_inherit_timeout_error():
    """asyncio.wait_for 의 TimeoutError 와 구분되어야 한다 — 다른 사건이다."""
    assert not issubclass(BrokerTimeout, TimeoutError)


async def test_a_timed_out_order_was_still_accepted():
    """설계서 9절 ⑤ 의 핵심. 응답이 없어도 서버에 도달했을 수 있다 —
    그래서 재발주하지 않고 조회로 확인한다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT,
                        fill_mode=FillMode.NEVER)
    req = a_buy()
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(req)
    orders = await broker.list_orders_today("005930")
    assert [o.client_ref for o in orders] == [req.client_ref]


async def test_client_ref_lets_us_tell_accepted_from_not_accepted():
    """두 주문 중 하나만 타임아웃했을 때 어느 것이 접수됐는지 구별할 수 있어야 한다."""
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        fail_mode=FailMode.TIMEOUT, fail_after=1)
    first = a_buy()
    second = a_buy(price=9_000)
    await broker.place_limit_order(first)      # 성공
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(second)  # 타임아웃 — 그래도 접수됨
    refs = {o.client_ref for o in await broker.list_orders_today("005930")}
    assert refs == {first.client_ref, second.client_ref}


async def test_fail_after_lets_the_first_n_calls_succeed():
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT, fail_after=2)
    await broker.place_limit_order(a_buy())
    await broker.place_limit_order(a_buy())
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(a_buy())


async def test_reject_raises_and_does_not_accept_the_order():
    """명시적 거부는 타임아웃과 다르다 — 주문이 접수되지 않았음이 확실하다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.REJECT)
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(a_buy())
    assert await broker.list_orders_today("005930") == []


async def test_reject_carries_a_code_and_message():
    """설계서 12.1절의 order_log.api_code·api_message 에 기록될 값이다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.REJECT)
    with pytest.raises(BrokerRejected) as exc:
        await broker.place_limit_order(a_buy())
    assert exc.value.code
    assert exc.value.message


async def test_reject_leaves_the_balance_untouched():
    broker = FakeBroker([9_500], fail_mode=FailMode.REJECT, cash=10_000_000)
    with pytest.raises(BrokerRejected):
        await broker.place_limit_order(a_buy())
    balance = await broker.get_balance()
    assert balance.cash == 10_000_000
    assert balance.qty_of("005930") == 0


async def test_disconnect_ends_the_stream_with_an_exception():
    """설계서 8.4절 — WebSocket 끊김. 모의투자로는 만들 수 없다."""
    broker = FakeBroker([9_500, 9_000, 8_500], fail_mode=FailMode.DISCONNECT,
                        fail_after=2)
    seen = []
    with pytest.raises(BrokerDisconnected):
        async for tick in broker.subscribe_quotes(["005930"]):
            seen.append(tick.price)
    assert seen == [9_500, 9_000]


async def test_resubscribing_after_a_disconnect_resumes_the_script():
    """재연결 후 구독 복원. 설계서 8.3절이 이것을 빠뜨리면 조용한 실패가 된다고 적었다."""
    broker = FakeBroker([9_500, 9_000, 8_500], fail_mode=FailMode.DISCONNECT,
                        fail_after=2)
    with pytest.raises(BrokerDisconnected):
        async for _ in broker.subscribe_quotes(["005930"]):
            pass
    broker.clear_failure()
    rest = [t.price async for t in broker.subscribe_quotes(["005930"])]
    assert rest == [8_500]


async def test_clear_failure_restores_normal_ordering():
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT,
                        fill_mode=FillMode.INSTANT)
    with pytest.raises(BrokerTimeout):
        await broker.place_limit_order(a_buy())
    broker.clear_failure()
    ack = await broker.place_limit_order(a_buy())
    assert (await broker.get_order(ack.broker_order_id)).state is FillState.FILLED


async def test_get_balance_can_also_fail():
    """대사(설계서 10.2절)가 잔고 조회 실패를 다뤄야 하므로 만들 수 있어야 한다."""
    broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT)
    with pytest.raises(BrokerTimeout):
        await broker.get_balance()


async def test_failure_modes_are_deterministic():
    async def run() -> int:
        broker = FakeBroker([9_500], fail_mode=FailMode.TIMEOUT, fail_after=1)
        await broker.place_limit_order(a_buy())
        try:
            await broker.place_limit_order(a_buy())
        except BrokerTimeout:
            return len(await broker.list_orders_today("005930"))
        return -1

    assert await run() == await run() == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/adapters/test_fake_broker_failures.py -v`
Expected: FAIL — `ImportError: cannot import name 'BrokerTimeout'`

- [ ] **Step 3: 예외 계층과 `FailMode` 를 `broker.py` 에 추가**

```python
class BrokerTimeout(Exception):
    """브로커가 응답하지 않았다.

    `TimeoutError` 를 상속하지 않는다. `asyncio.wait_for` 가 `TimeoutError` 를
    던지므로, 상속하면 엔진의 `except BrokerTimeout` 이 asyncio 자체의 타임아웃까지
    잡는다. 둘은 다른 사건이다 — 이것은 브로커가 답하지 않은 것이고, 그것은 우리
    쪽 대기 한도를 넘긴 것이다.

    **이 예외를 받았을 때 재발주해서는 안 된다.** 요청이 서버에 도달했는지 알 수
    없고, 도달했을 수도 있다. 설계서 9절 ⑤ 가 규정한 유일한 안전한 행동은
    `list_orders_today` 로 `client_ref` 를 대조해 사실을 확인하는 것이다.
    """


class BrokerRejected(Exception):
    """브로커가 명시적으로 거부했다. 타임아웃과 달리 미접수가 확실하다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BrokerDisconnected(Exception):
    """시세 스트림이 끊겼다. 설계서 8.4절의 REST 폴백이 여기서 시작된다."""


class FailMode(Enum):
    NONE = "NONE"
    TIMEOUT = "TIMEOUT"
    REJECT = "REJECT"
    DISCONNECT = "DISCONNECT"
```

- [ ] **Step 4: `FakeBroker` 에 실패 모드를 배선**

`__init__` 에 `fail_mode: FailMode = FailMode.NONE`, `fail_after: int = 0` 을 더하고
`self._calls = 0` 을 초기화한다. 그리고 다음을 추가한다.

```python
    def clear_failure(self) -> None:
        """실패 모드를 해제한다. 재연결·재시도 시나리오에 쓴다."""
        self._fail_mode = FailMode.NONE
        self._calls = 0

    def _should_fail(self) -> bool:
        """fail_after 번째 호출까지는 통과시키고 그 다음부터 실패한다."""
        if self._fail_mode is FailMode.NONE:
            return False
        self._calls += 1
        return self._calls > self._fail_after
```

`_accept` 의 맨 앞에 실패 처리를 넣는다.

```python
    def _accept(
        self, client_ref: UUID, code: str, side: Side, qty: int, price: int | None
    ) -> OrderAck:
        if self._should_fail():
            if self._fail_mode is FailMode.REJECT:
                # 명시적 거부는 주문을 등록하지 않는다 — 미접수가 확실하다.
                raise BrokerRejected("40510", "주문 거부 (시뮬레이션)")
            if self._fail_mode is FailMode.TIMEOUT:
                # 타임아웃은 등록한 뒤 던진다. 실제 타임아웃의 성질이 그렇고,
                # 설계서 9절 ⑤ 의 "접수됨" 분기를 테스트할 수 있게 하는 장치다.
                self._register(client_ref, code, side, qty, price)
                raise BrokerTimeout("no response from broker (simulated)")
            if self._fail_mode is FailMode.DISCONNECT:
                raise BrokerDisconnected("stream lost (simulated)")
        return OrderAck(
            client_ref=client_ref,
            broker_order_id=self._register(client_ref, code, side, qty, price),
            accepted_at=_EPOCH,
        )
```

Task 11 의 `_accept` 본문에서 주문 등록과 체결 결정 부분을 `_register` 로 빼내고,
`broker_order_id` 를 반환하게 한다. `_register` 는 실패 처리를 하지 않는다 —
`_accept` 만 한다.

`get_balance` 앞에도 실패 처리를 넣는다.

```python
    async def get_balance(self) -> Balance:
        if self._should_fail() and self._fail_mode is FailMode.TIMEOUT:
            raise BrokerTimeout("no response from broker (simulated)")
        ...
```

`_replay` 에 끊김을 넣는다.

```python
    async def _replay(self) -> AsyncIterator[Tick]:
        for price in self._script[self._ticks_consumed:]:
            if (self._fail_mode is FailMode.DISCONNECT
                    and self._ticks_consumed >= self._fail_after):
                raise BrokerDisconnected("stream lost (simulated)")
            self._ticks_consumed += 1
            self._settle_delayed()
            yield Tick(code=self._code, price=price,
                       at=_EPOCH + timedelta(seconds=self._ticks_consumed),
                       source=TickSource.WS)
```

끊김은 `_should_fail` 을 쓰지 않는다 — 틱 소비 수를 기준으로 해야 결정론적이고,
`test_resubscribing_after_a_disconnect_resumes_the_script` 가 재구독 후 남은 틱을
기대하기 때문이다.

- [ ] **Step 5: 테스트 통과와 회귀 확인**

Run:
```bash
.venv/bin/python -m pytest tests/adapters/test_fake_broker_failures.py -v
.venv/bin/python -m pytest tests/adapters/test_fake_broker.py -v
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS. Task 11 의 19개가 하나도 깨지지 않아야 한다 — `FailMode.NONE` 이
기본값이므로 기존 동작이 유지된다. 깨지면 `_should_fail` 이 `NONE` 에서도 호출
카운터를 올리는지 확인한다(올리면 안 된다).

- [ ] **Step 6: 커밋**

```bash
git add src/autotrading7s/adapters/fake/broker.py tests/adapters/test_fake_broker_failures.py
git commit -m "$(printf 'feat: 시뮬레이션 브로커의 실패 모드\n\n설계서 9절 ⑤ 를 검증 가능하게 만든다. 그 분기는 이 프로젝트에서 가장 중요한\n다섯 줄이다 — 주문을 보냈는데 응답이 오지 않았을 때 재발주하지 않고 조회로 사실을\n확인한다. 그것을 테스트하려면 응답이 오지 않는 상황을 만들 수 있어야 하고,\n모의투자로는 만들 수 없다.\n\nTIMEOUT 은 주문을 접수한 뒤 예외를 던진다. 실제 타임아웃의 성질이 그렇다 —\n요청이 서버에 도달했는지 알 수 없고 도달했을 수도 있다. 그래서 list_orders_today\n가 그 주문을 보여주며, 이것이 9절 ⑤ 의 "접수됨" 분기를 테스트할 수 있게 한다.\nREJECT 는 반대로 등록하지 않는다 — 미접수가 확실하다.\n\nBrokerTimeout 은 TimeoutError 를 상속하지 않는다. asyncio.wait_for 가\nTimeoutError 를 던지므로 상속하면 엔진의 except 가 asyncio 자체의 타임아웃까지\n잡는다. 브로커가 답하지 않은 것과 우리 쪽 대기 한도를 넘긴 것은 다른 사건이다.\n\n끊김은 틱 소비 수를 기준으로 한다 — 결정론적이어야 하고, 재구독 후 남은 틱이\n이어지는 것을 테스트로 고정했다(설계서 8.3절의 구독 복원).')"
```

---

### Task 13: G2a 게이트

**Files:**
- Create: `tests/test_g2a_gate.py`
- Modify: `README.md`
- Test: 위 파일 자체

**Interfaces:**
- Consumes: Task 1~12 전부
- Produces: G2a 통과 증거

**G1 이 도메인 계약의 조합을 검증했듯, G2a 는 영속성 계약의 조합을 검증한다.**
개별 태스크는 각자 리뷰를 통과했지만, **도메인 객체가 DB 를 한 바퀴 돌아 돌아왔을 때
G1 이 통과했던 시나리오가 여전히 통과하는지는 아무도 확인하지 않았다.**

이 게이트의 핵심 테스트는 그것이다 — G1 의 전 사이클 시나리오를 매 결정마다 저장하고
다시 읽어서 돌린다. 도메인만으로 돌린 결과와 DB 를 경유한 결과가 같아야 한다.

- [ ] **Step 1: G2a 게이트 테스트 작성 — `tests/test_g2a_gate.py`**

```python
"""G2a 게이트 — 영속성 계약의 조합 검증.

개별 태스크는 각자 리뷰를 통과했다. 이 파일이 확인하는 것은 그것들의 조합이다 —
도메인 객체가 DB 를 한 바퀴 돌아 돌아왔을 때 G1 이 통과했던 시나리오가 여전히
통과하는가.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.sqlite.mapping import CorruptRowError
from autotrading7s.ports.repository import SplitConfig
from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.domain.cycle import (
    close,
    confirm_anchor,
    is_cycle_complete,
)
from autotrading7s.domain.guards import GuardContext, check_buy, check_sell
from autotrading7s.domain.ladder import Ladder
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

    # 1단계 체결로 앵커를 확정한다.
    stages = repo.load_stages(cycle.cycle_id)
    first = to_holding(to_buy_pending(stages[0]), fill_price=10_000,
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
                total_limit=21_000_000, orders_last_minute=orders % 10)
            index = decision.stage_no - 1
            if isinstance(decision, BuyStage):
                assert check_buy(decision, ctx).allowed
                updated = to_holding(to_buy_pending(live_stages[index]),
                                     fill_price=decision.limit_price,
                                     fill_qty=decision.qty, at=at)
                side = Side.BUY
            else:
                assert check_sell(decision, ctx).allowed
                updated = after_sell(to_sell_pending(live_stages[index]), at=at,
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
    """도메인은 부분 목록을 허용하고 리포지토리는 완전한 것만 준다."""
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

    repo._conn.execute(  # noqa: SLF001 — 리포지토리 밖의 손상을 시뮬레이션
        "DELETE FROM stage_state WHERE cycle_id = ? AND stage_no = 4",
        (cycle.cycle_id,))
    repo._conn.commit()
    with pytest.raises(CorruptRowError, match="incomplete"):
        repo.load_stages(cycle.cycle_id)


def test_ports_and_adapters_import_only_inward():
    """설계서 7.2절 — 화살표는 항상 안쪽을 향한다.

    `domain/` 은 `tests/test_g1_gate.py` 가 이미 검사한다. 이 테스트는
    `ports/` 와 `adapters/` 가 서로를 잘못 참조하지 않는지 본다.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).parent.parent / "src" / "autotrading7s"
    offenders: list[str] = []

    for layer, forbidden in (("domain", ("ports", "adapters")),
                             ("ports", ("adapters",))):
        for path in (root / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = node.names[0].name
                else:
                    continue
                for banned in forbidden:
                    if f"autotrading7s.{banned}" in module:
                        offenders.append(f"{path.name}: {module}")

    assert offenders == [], f"의존 방향 위반: {offenders}"
```

`tests/test_g1_gate.py` 의 AST 테스트는 `domain/` 만 본다. 이 테스트는 `ports/` 가
`adapters/` 를 참조하지 않는지 추가로 검사한다.

**`TYPE_CHECKING` 예외 처리는 두지 않는다.** `ports/repository.py` 가 계약 DTO 를
직접 정의하므로(Task 3) `ports/` 는 `adapters/` 를 어떤 형태로도 참조하지 않는다 —
`TYPE_CHECKING` 안이든 밖이든. 테스트를 느슨하게 만들 이유가 없으므로 모든
`Import`·`ImportFrom` 노드를 그대로 검사한다. 만약 나중에 누군가 `ports/` 에서
`adapters/` 를 `TYPE_CHECKING` 으로 참조하고 싶어진다면, 그것은 타입이 잘못된 층에
있다는 신호이므로 이 테스트가 실패하는 것이 옳다.

- [ ] **Step 2: 테스트 실행 — 실패를 관찰하고 AST 예외 처리를 구현**

Run: `.venv/bin/python -m pytest tests/test_g2a_gate.py -v`
Expected: PASS — `test_ports_and_adapters_import_only_inward` 를 포함해 전부.
만약 이 테스트가 실패한다면 `ports/` 나 `domain/` 어딘가가 바깥 층을 참조하고 있는
것이므로, 테스트를 고치지 말고 그 import 를 고쳐라.

- [ ] **Step 3: G2a 게이트 실행 — 전체 스위트와 커버리지**

Run:
```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m pytest tests/ --cov=autotrading7s --cov-report=term-missing
```

Expected:
- 전체 PASS, 출력 청결
- `autotrading7s.domain` 은 여전히 95% 이상 (`fail_under` 가 강제)
- `autotrading7s.adapters.sqlite` 와 `autotrading7s.adapters.fake` 가 90% 이상.
  미달이면 빠진 분기에 테스트를 추가하고, 어느 분기를 덮었는지 보고서에 적는다.

`--cov=autotrading7s.domain` 이 아니라 `--cov=autotrading7s` 로 바꾸면 `fail_under=95`
가 전체에 적용되어 어댑터의 미달이 게이트를 막을 수 있다. `pyproject.toml` 의
`[tool.coverage.run] source` 는 `autotrading7s.domain` 으로 두고, 어댑터 커버리지는
명령행으로 따로 측정한다. 그 이유를 보고서에 적는다.

- [ ] **Step 4: `README.md` 갱신**

"현재 상태" 절을 바꾼다.

```markdown
## 현재 상태

**Plan 1 (도메인 코어, G1) 완료.** 사다리 계산·호가 단위·상태기계·트리거 판정·
안전장치가 구현되어 있으며, 네트워크·DB·GUI 없이 전부 테스트로 검증된다.

**Plan 2A (영속성 + 브로커 포트, G2a) 완료.** SQLite 리포지토리가 도메인 객체를
저장·복원하며, Plan 1 이 넘긴 제약 다섯 건을 리포지토리 경계에서 강제한다 —
복원 실패의 지목(`CorruptRowError`), tz-aware 시각, 완전한 단계 집합,
`trigger_price` 대조, `order_log` 기반 실현손익. 시뮬레이션 브로커가 체결·실패
모드를 재생해 모의투자로는 만들 수 없는 실패 경로를 검증한다.

미구현: 엔진(Plan 2B), 키움 어댑터(Plan 3), GUI(Plan 4).
```

- [ ] **Step 5: 커밋**

```bash
git add tests/test_g2a_gate.py README.md
git commit -m "$(printf 'test: G2a 게이트 — 영속성 계약의 조합 검증\n\nG1 이 도메인 계약의 조합을 검증했듯, 이 게이트는 영속성 계약의 조합을 검증한다.\n핵심 테스트는 G1 의 전 사이클 시나리오를 매 결정마다 저장하고 다시 읽어서 돌리는\n것이다 — 도메인만으로 돌린 결과와 DB 를 경유한 결과가 같아야 한다(보유 433주,\n매도 순서 [4,3,2,1], 총 주문 7건).\n\nH1~H5 를 각각 게이트에서 확인한다. 특히 H2 는 쿨다운 산술이 복원된 시각으로\n성립하는지 직접 검사한다 — naive 로 돌아오면 엔진 틱 루프 안에서 TypeError 가\n난다는 것이 Plan 1 Task 9 가 확인한 실패 모드다.\n\n의존 방향 테스트를 ports 까지 확장했다. 계약 DTO 가 포트에 있으므로 ports 는\nadapters 를 어떤 형태로도 참조하지 않으며, 예외 처리 없이 모든 import 를 검사한다.')"
```

---

## G2a 게이트 통과 기준

Plan 2A 완료 시 다음이 모두 통과해야 한다.

- [ ] `DomainInvariantError` 가 복원 실패와 호출자 버그를 구분한다 (H1)
- [ ] `Decimal` 이 TEXT 왕복에서 정확히 보존된다 — `0.1666` 이 같은 발동가를 낸다
- [ ] 모든 `datetime` 이 tz-aware 로 왕복하며, 쓸 때도 읽을 때도 naive 를 거부한다 (H2)
- [ ] 8개 테이블과 `holdings` 뷰가 생성되고, 마이그레이션이 멱등하다
- [ ] 외래키가 강제되고, `stage_state` 의 `UNIQUE(cycle_id, stage_no)` 와 `order_log` 의 `client_ref` UNIQUE 가 동작한다
- [ ] 도메인 불변식 두 개가 스키마에서도 강제된다 — `HOLDING`·`SELL_PENDING` 의 fill 필수, `TRIGGER` 경로의 `LIMIT` 전용
- [ ] `load_stages` 가 불완전한 집합을 거부한다 (H3)
- [ ] `load_stages` 가 `trigger_price` 불일치를 거부한다 (H4)
- [ ] `realized_pnl_for_cycle` 이 `order_log` 에서 집계한다 (H5)
- [ ] `SqliteRepository` 가 `RepositoryPort` 를 만족한다 (17개 메서드)
- [ ] `FakeBroker` 가 `BrokerPort` 를 만족하고 결정론적이다
- [ ] 네 체결 모드(`INSTANT`·`DELAYED`·`PARTIAL`·`NEVER`)가 동작하고, `PARTIAL` 이 0주를 체결하지 않는다
- [ ] 세 실패 모드(`TIMEOUT`·`REJECT`·`DISCONNECT`)가 동작하고, `TIMEOUT` 이 주문을 접수한 뒤 던진다
- [ ] G1 의 전 사이클 시나리오가 DB 를 경유해도 같은 결과를 낸다
- [ ] 의존 방향이 `domain ← ports ← adapters` 로 유지된다
- [ ] `autotrading7s.domain` 커버리지 95% 이상, 어댑터 90% 이상

---

## Plan 2A 이후

**Plan 2B (엔진 + G2)** 로 진행한다. 범위:

- `domain/cycle.py` 에 D20 강제 종료 (`force_close`) — 설계서 11.4절. 이 계획에 넣지 않은 이유는 그것을 쓰는 Emergency Control Handler 와 함께 설계해야 Plan 1 에서 겪은 "계약이 소비자보다 먼저 정해져 어긋나는" 문제를 피하기 때문이다. 스키마는 이 계획이 이미 준비했다.
- `engine/executor.py` — 설계서 9절 주문 실행 파이프라인. `FakeBroker` 의 `TIMEOUT` 모드가 ⑤ 의 UNKNOWN 분기를 검증한다.
- `engine/orchestrator.py` — asyncio 태스크, `priority_q` 우선 소비
- `engine/reconciler.py`, `engine/recovery.py`, `engine/emergency.py`
- `app/commands.py`, `app/events.py`, `app/engine_thread.py`
- `cli.py` — headless 기동
- 설계서 15.2절 G2 시나리오 12건

**Plan 1·2A 가 Plan 2B 로 넘기는 제약** (원장과 이 계획에서):

1. 긴급청산은 `guards.check_sell` 을 거쳐서는 안 된다. `max_orders_per_minute=0` 이 매도를 막으며, 그것은 손절 없는 전략의 유일한 탈출구에 레이트 리미터를 거는 것이다.
2. 한 틱이 여러 매도를 낼 수 있으므로 결정 사이에 guard 컨텍스트를 증가시켜야 한다. `check_buy`·`check_sell` 은 상태 없는 술어다.
3. `Balance.qty_of` 는 응답에 없는 종목에 0 을 반환한다. 긴급청산 경로는 "응답에 없음"과 "보유 0"을 구분해야 한다. `FakeBroker` 가 두 상황을 다 만들 수 있다.
4. `stage_no > max_stages` 는 매수에서 무시되고 매도는 된다. `load_stages` 가 이제 그것을 거부하지만, 도메인에 직접 넣는 경로가 남아 있다.
5. `is_cycle_complete([])` 가 `DomainInvariantError` 를 던진다. 엔진이 이 예외를 대사·정지 경로로 흡수해야 한다.
6. 사이클 종료 시 `realized_pnl_for_cycle` 의 값을 `cycle.realized_pnl` 에 기록하는 것은 엔진의 몫이다. 리포지토리는 집계만 한다.
7. D20 강제 종료가 대사를 영구히 깨뜨린다. `forced_close_qty` 를 종목별로 누적해 대사 기준선으로 삼고, 사용자가 그 주식을 처리한 뒤 기준선을 초기화하는 수단이 필요하다(설계서 11.4절 설계 제약).
