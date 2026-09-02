# AutoTrading 7s — Plan 1: 도메인 코어 (G1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 세븐스플릿 전략의 모든 판정 로직(사다리 계산·호가 단위·상태기계·트리거 규칙·안전장치)을 네트워크·DB·GUI 의존 없는 순수 도메인 코어로 구현하고, 설계서의 검증 게이트 G1을 통과한다.

**Architecture:** 헥사고날 구조의 가장 안쪽 층만 만든다. `domain/` 은 표준 라이브러리 외 어떤 것도 import 하지 않는 순수 계산 코드이며, 모든 함수는 부작용이 없다. 시간은 `ClockPort` 로 주입하여 테스트에서 재현 가능하게 한다. 이 계층이 완성되면 이후 모든 계획(엔진·어댑터·UI)이 여기에 의존하고 여기를 수정하지 않는다.

**Tech Stack:** Python 3.12 (표준 라이브러리 `dataclasses` / `decimal` / `enum` / `datetime`), pytest, pytest-cov. 런타임 외부 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`

## Global Constraints

설계서에서 그대로 옮긴 프로젝트 전역 요구사항. 모든 태스크의 요구사항에 암묵적으로 포함된다.

- **Python 3.12** 이상. `from __future__ import annotations` 를 모든 모듈 첫 줄에 둔다.
- **`domain/` 패키지는 표준 라이브러리 외 어떤 것도 import 하지 않는다.** `httpx`·`sqlite3`·`tkinter` 모두 금지. (설계서 7.2절 의존 규칙)
- **금액·가격은 원 단위 `int`, 비율만 `Decimal`.** `float` 를 금액 계산에 쓰는 것을 금지하며, `float` 를 받는 함수는 `TypeError` 를 던진다. (설계서 3.1절 — float 오차가 주문 수량을 바꿀 수 있음)
- **주문 요청 타입에 신용·미수 관련 필드를 정의하지 않는다.** (설계서 6절 — 원칙을 타입으로 강제)
- **`decide()` 에 하락 조건 매도 분기를 두지 않는다.** 자동 손절매 배제 원칙. (설계서 6절)
- **자동 트리거 경로는 시장가를 표현할 수 없다.** `LimitOrderRequest` 는 `price` 가 필수이며 `None` 을 허용하지 않는다. (설계서 8.2절)
- 분할 단계 수는 **2~7**. (설계서 3.1절)
- 매수 트리거 기준점은 **1단계 체결가 대비 누적** (`anchor × (1 - drop×(n-1))`). (설계서 D3)
- 호가 단위 정규화 방향: **매수 발동가는 내림, 목표 매도가는 올림.** (설계서 3.2절)
- 개발·테스트는 Linux EC2에서 수행한다. 이 계획의 모든 코드는 GUI 없이 동작하며 `pytest` 만으로 검증된다.
- 커밋 메시지는 한국어 본문 + Conventional Commits 접두어(`feat:` / `test:` / `chore:` / `docs:`).

## 참조 규칙 번호

설계서 5절의 트리거 판정 규칙. 태스크에서 이 번호로 참조한다.

| 규칙 | 내용 |
|---|---|
| 규칙 1 | 한 틱에서 매도를 매수보다 먼저 평가 |
| 규칙 2 | 한 틱에 매수는 1단계씩만, 낮은 번호부터 |
| 규칙 3 | 재매수 쿨다운 (기본 60초) |
| 규칙 4 | 장 운영시간 밖에서는 어떤 결정도 내리지 않음 |
| 규칙 5 | PENDING 상태 단계는 판정 대상에서 제외 |

---

## File Structure

| 파일 | 책임 |
|---|---|
| `pyproject.toml` | 패키지 메타·pytest·커버리지 설정 |
| `src/autotrading7s/domain/types.py` | 열거형과 값 객체. 다른 domain 모듈이 공유하는 어휘 |
| `src/autotrading7s/domain/tick_size.py` | 호가 단위 표와 정규화 (내림/올림) |
| `src/autotrading7s/domain/ladder.py` | 사다리 생성(발동가·수량·투입금액), 목표가 계산, 설정 검증 |
| `src/autotrading7s/domain/stage.py` | 단계 상태와 전이 함수. 긴급청산용 우회 전이 포함 |
| `src/autotrading7s/domain/cycle.py` | 사이클 상태와 전이 함수. 트리거 허용 여부 판정 |
| `src/autotrading7s/domain/pnl.py` | 평가·실현손익, 평균단가 집계 |
| `src/autotrading7s/domain/rules.py` | `decide()` — 트리거 판정. 규칙 1~5 전부 |
| `src/autotrading7s/domain/guards.py` | 총한도·주문빈도 안전장치 |
| `src/autotrading7s/ports/clock.py` | `ClockPort` Protocol |
| `src/autotrading7s/adapters/fake/clock.py` | `FakeClock` — 시간을 조작 가능한 구현 |
| `tests/domain/*` | 위 각 모듈의 단위 테스트 |
| `tests/test_spec_regression.py` | 설계서에 실린 예시 수치를 고정하는 회귀 테스트 |

`rules.py` 는 태스크 7·8·9에서 세 번에 걸쳐 완성된다. 규칙마다 독립적인 테스트 사이클을 갖기 위한 분할이다.

---

### Task 1: 프로젝트 스캐폴딩 + 값 객체와 열거형

**Files:**
- Create: `pyproject.toml`
- Create: `src/autotrading7s/__init__.py`
- Create: `src/autotrading7s/domain/__init__.py`
- Create: `src/autotrading7s/domain/types.py`
- Create: `tests/__init__.py`
- Create: `tests/domain/__init__.py`
- Test: `tests/domain/test_types.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `Side` (BUY, SELL), `TickSource` (WS, REST_POLL), `OrderPath` (TRIGGER, EMERGENCY)
  - `StageStatus` (WAITING, BUY_PENDING, HOLDING, SELL_PENDING, SOLD)
  - `CycleStatus` (IDLE, STARTING, RUNNING, PAUSED, LIQUIDATING, CLOSED)
  - `CloseReason` (NORMAL, EMERGENCY), `FillState` (OPEN, PARTIAL, FILLED, CANCELED, REJECTED)
  - `Tick(code: str, price: int, at: datetime, source: TickSource)`
  - `LimitOrderRequest(code: str, side: Side, qty: int, price: int, client_ref: UUID)`
  - `MarketSellRequest(code: str, qty: int, client_ref: UUID, reason: str)`
  - `OrderAck(client_ref: UUID, broker_order_id: str, accepted_at: datetime)`
  - `OrderStatus(client_ref, broker_order_id, state: FillState, filled_qty: int, filled_price: int | None, api_code: str | None, api_message: str | None)`
  - `Holding(code: str, qty: int, avg_price: int)`, `Balance(cash: int, holdings: tuple[Holding, ...])` with `Balance.qty_of(code) -> int`

- [ ] **Step 1: 디렉터리와 패키지 파일 생성**

```bash
mkdir -p src/autotrading7s/domain src/autotrading7s/ports src/autotrading7s/adapters/fake
mkdir -p tests/domain
touch src/autotrading7s/__init__.py src/autotrading7s/domain/__init__.py
touch src/autotrading7s/ports/__init__.py src/autotrading7s/adapters/__init__.py
touch src/autotrading7s/adapters/fake/__init__.py
touch tests/__init__.py tests/domain/__init__.py
```

- [ ] **Step 2: `pyproject.toml` 작성**

```toml
[project]
name = "autotrading7s"
version = "0.1.0"
description = "세븐스플릿(7-Split) 자동투자 프로그램 — 키움증권 REST API 기반"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24", "pytest-cov>=5.0"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q --strict-markers"

[tool.coverage.run]
source = ["autotrading7s.domain"]
```

- [ ] **Step 3: 실패하는 테스트 작성 — `tests/domain/test_types.py`**

```python
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from autotrading7s.domain.types import (
    Balance,
    FillState,
    Holding,
    LimitOrderRequest,
    MarketSellRequest,
    Side,
    Tick,
    TickSource,
)


def _now() -> datetime:
    return datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def test_tick_is_frozen():
    tick = Tick(code="005930", price=9340, at=_now(), source=TickSource.WS)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tick.price = 9350  # type: ignore[misc]


def test_limit_order_request_has_no_credit_fields():
    """설계서 6절: 신용·미수 필드가 타입에 존재하지 않아야 한다."""
    names = {f.name for f in dataclasses.fields(LimitOrderRequest)}
    assert names == {"code", "side", "qty", "price", "client_ref"}
    forbidden = {"credit", "credit_type", "loan", "loan_type", "margin", "misu"}
    assert names & forbidden == set()


def test_limit_order_request_price_is_mandatory():
    """설계서 8.2절: 자동 트리거 경로는 시장가를 표현할 수 없다."""
    with pytest.raises(TypeError):
        LimitOrderRequest(  # type: ignore[call-arg]
            code="005930", side=Side.BUY, qty=100, client_ref=uuid4()
        )


def test_limit_order_request_rejects_nonpositive():
    with pytest.raises(ValueError):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=0, price=9340,
                          client_ref=uuid4())
    with pytest.raises(ValueError):
        LimitOrderRequest(code="005930", side=Side.BUY, qty=100, price=0,
                          client_ref=uuid4())


def test_market_sell_request_requires_reason():
    """설계서 8.2절: 사유 필드가 필수여서 로깅을 빼먹을 수 없다."""
    with pytest.raises(TypeError):
        MarketSellRequest(code="005930", qty=316, client_ref=uuid4())  # type: ignore[call-arg]

    req = MarketSellRequest(code="005930", qty=316, client_ref=uuid4(),
                            reason="실적 쇼크")
    assert req.reason == "실적 쇼크"


def test_market_sell_request_allows_empty_reason():
    """사용자 입력은 선택이므로 빈 문자열은 허용한다 (설계서 14.3절)."""
    req = MarketSellRequest(code="005930", qty=1, client_ref=uuid4(), reason="")
    assert req.reason == ""


def test_balance_qty_of():
    bal = Balance(cash=1_000_000, holdings=(
        Holding(code="005930", qty=316, avg_price=9458),
        Holding(code="035720", qty=833, avg_price=8382),
    ))
    assert bal.qty_of("005930") == 316
    assert bal.qty_of("035720") == 833
    assert bal.qty_of("035420") == 0


def test_fill_state_members():
    assert {s.name for s in FillState} == {
        "OPEN", "PARTIAL", "FILLED", "CANCELED", "REJECTED"
    }
```

- [ ] **Step 4: 테스트가 실패하는 것을 확인**

Run: `python -m pytest tests/domain/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.types'`

- [ ] **Step 5: `src/autotrading7s/domain/types.py` 구현**

```python
"""도메인 어휘 — 열거형과 값 객체.

설계서 3.3절. 이 모듈은 표준 라이브러리 외 어떤 것도 import 하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class TickSource(Enum):
    """시세 출처. WebSocket 끊김 구간을 사후에 식별하기 위해 기록한다."""

    WS = "WS"
    REST_POLL = "REST_POLL"


class OrderPath(Enum):
    """주문 경로. 자동 판단과 수동 개입을 데이터에 영구 구분한다(설계서 12.2절)."""

    TRIGGER = "TRIGGER"
    EMERGENCY = "EMERGENCY"


class StageStatus(Enum):
    WAITING = "WAITING"
    BUY_PENDING = "BUY_PENDING"
    HOLDING = "HOLDING"
    SELL_PENDING = "SELL_PENDING"
    SOLD = "SOLD"


class CycleStatus(Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    LIQUIDATING = "LIQUIDATING"
    CLOSED = "CLOSED"


class CloseReason(Enum):
    NORMAL = "NORMAL"
    EMERGENCY = "EMERGENCY"


class FillState(Enum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class Tick:
    code: str
    price: int
    at: datetime
    source: TickSource


@dataclass(frozen=True, slots=True)
class LimitOrderRequest:
    """자동 트리거 경로 전용 주문 요청.

    설계서 6절·8.2절: 신용·미수 필드가 존재하지 않으며, ``price`` 가 필수이므로
    시장가를 표현할 방법이 없다. 원칙을 문서가 아니라 타입으로 강제한다.
    """

    code: str
    side: Side
    qty: int
    price: int
    client_ref: UUID

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"qty must be positive: {self.qty}")
        if self.price <= 0:
            raise ValueError(f"price must be positive: {self.price}")


@dataclass(frozen=True, slots=True)
class MarketSellRequest:
    """긴급청산 경로 전용 주문 요청.

    ``reason`` 은 필수 필드다. 사용자 입력 자체는 선택이므로 빈 문자열을
    허용하지만, 필드가 필수여서 사유 기록을 구조적으로 빼먹을 수 없다.
    """

    code: str
    qty: int
    client_ref: UUID
    reason: str

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"qty must be positive: {self.qty}")


@dataclass(frozen=True, slots=True)
class OrderAck:
    client_ref: UUID
    broker_order_id: str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class OrderStatus:
    client_ref: UUID
    broker_order_id: str
    state: FillState
    filled_qty: int
    filled_price: int | None
    api_code: str | None = None
    api_message: str | None = None


@dataclass(frozen=True, slots=True)
class Holding:
    code: str
    qty: int
    avg_price: int


@dataclass(frozen=True, slots=True)
class Balance:
    cash: int
    holdings: tuple[Holding, ...]

    def qty_of(self, code: str) -> int:
        for holding in self.holdings:
            if holding.code == code:
                return holding.qty
        return 0
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_types.py -v`
Expected: PASS (8 tests)

- [ ] **Step 7: 커밋**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: 프로젝트 스캐폴딩과 도메인 값 객체 추가

설계서 3.3절의 열거형·값 객체를 구현했다. LimitOrderRequest에는 신용·미수
필드가 없고 price가 필수이므로 자동 트리거 경로에서 시장가를 표현할 수 없다.
MarketSellRequest는 reason을 필수 필드로 두어 사유 기록을 강제한다."
```

---

### Task 2: 호가 단위 정규화

**Files:**
- Create: `src/autotrading7s/domain/tick_size.py`
- Test: `tests/domain/test_tick_size.py`

**Interfaces:**
- Consumes: `autotrading7s.domain.types.Side`
- Produces:
  - `tick_unit(price: int) -> int` — 가격 구간의 호가 단위
  - `normalize_tick(raw: Decimal | int, side: Side) -> int` — 유효 호가로 정규화. BUY는 내림, SELL은 올림. `float` 입력은 `TypeError`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_tick_size.py`**

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.tick_size import normalize_tick, tick_unit
from autotrading7s.domain.types import Side


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (1, 1), (1_999, 1),
        (2_000, 5), (4_999, 5),
        (5_000, 10), (9_340, 10), (19_999, 10),
        (20_000, 50), (49_999, 50),
        (50_000, 100), (161_200, 100), (199_999, 100),
        (200_000, 500), (499_999, 500),
        (500_000, 1_000), (1_000_000, 1_000),
    ],
)
def test_tick_unit_boundaries(price: int, expected: int):
    assert tick_unit(price) == expected


def test_tick_unit_rejects_nonpositive():
    with pytest.raises(ValueError):
        tick_unit(0)
    with pytest.raises(ValueError):
        tick_unit(-100)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (9_340, 9_340),          # 이미 유효 호가
        (8_873, 8_870),          # 설계서 3.1절 2단계
        (8_406, 8_400),          # 3단계
        (7_939, 7_930),          # 4단계
        (7_472, 7_470),          # 5단계
        (7_005, 7_000),          # 6단계
        (6_538, 6_530),          # 7단계
    ],
)
def test_normalize_buy_floors(raw: int, expected: int):
    """매수 발동가는 내림 — 설계서 3.2절."""
    assert normalize_tick(Decimal(raw), Side.BUY) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("9807"), 9_810),     # 9,340 × 1.05
        (Decimal("9954"), 9_960),     # 9,480 × 1.05
        (Decimal("9397.5"), 9_400),   # 8,950 × 1.05
        (Decimal("10500"), 10_500),   # 이미 유효 호가면 그대로
    ],
)
def test_normalize_sell_ceils(raw: Decimal, expected: int):
    """목표 매도가는 올림 — 목표수익률 미달 방지. 설계서 3.2절."""
    assert normalize_tick(raw, Side.SELL) == expected


def test_normalize_sell_crossing_unit_boundary_stays_valid():
    """올림이 구간 경계를 넘어도 결과는 유효 호가여야 한다."""
    assert normalize_tick(Decimal("19998"), Side.SELL) == 20_000
    assert normalize_tick(Decimal("4999"), Side.SELL) == 5_000


def test_normalize_rejects_float():
    """설계서 3.1절: float 은 금액 계산에서 금지한다."""
    with pytest.raises(TypeError):
        normalize_tick(9340.5, Side.BUY)  # type: ignore[arg-type]


def test_normalize_rejects_nonpositive():
    with pytest.raises(ValueError):
        normalize_tick(Decimal(0), Side.BUY)
    with pytest.raises(ValueError):
        normalize_tick(Decimal(-1), Side.SELL)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_tick_size.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.tick_size'`

- [ ] **Step 3: `src/autotrading7s/domain/tick_size.py` 구현**

```python
"""호가 단위(tick size) 정규화 — 설계서 3.2절.

한국거래소에는 가격 구간별 호가 단위가 있어 유효 호가가 아닌 가격으로
주문하면 거부된다. 구간표는 2023년 KRX 호가 단위 개편 기준이며, 설계서
18.2절에 따라 구현 0단계에서 현행 값과 코스피·코스닥 차이를 재확인해야 한다.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from autotrading7s.domain.types import Side

# (상한(배타), 호가 단위) — 오름차순
_TICK_TABLE: tuple[tuple[int, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
_TICK_ABOVE_TABLE = 1_000


def tick_unit(price: int) -> int:
    """``price`` 가 속한 구간의 호가 단위."""
    if price <= 0:
        raise ValueError(f"price must be positive: {price}")
    for upper, unit in _TICK_TABLE:
        if price < upper:
            return unit
    return _TICK_ABOVE_TABLE


def normalize_tick(raw: Decimal | int, side: Side) -> int:
    """유효 호가로 정규화한다.

    BUY  → 내림. 발동가는 판정 기준선이므로 유효 호가 이하로 맞춘다.
    SELL → 올림. 내림하면 설정한 목표수익률에 미달한 채로 팔린다.

    구간 경계는 다음 구간 단위의 배수이므로(예: 20,000 은 50의 배수) 올림이
    경계를 넘어도 결과는 항상 유효 호가다.
    """
    if isinstance(raw, float):
        raise TypeError(
            "float 은 금액 계산에서 금지한다 — Decimal 또는 int 를 쓸 것 (설계서 3.1절)"
        )
    value = Decimal(raw)
    if value <= 0:
        raise ValueError(f"price must be positive: {value}")

    unit = tick_unit(int(value))
    rounding = ROUND_FLOOR if side is Side.BUY else ROUND_CEILING
    quotient = (value / unit).to_integral_value(rounding=rounding)
    return int(quotient) * unit
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_tick_size.py -v`
Expected: PASS (32 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/tick_size.py tests/domain/test_tick_size.py
git commit -m "feat: 호가 단위 정규화 추가

설계서 3.2절. 매수 발동가는 내림, 목표 매도가는 올림으로 정규화한다.
올림 방향을 택한 이유는 내림하면 설정한 목표수익률에 미달한 채로 팔리기
때문이다. 구간표는 18.2절에 따라 구현 중 현행 값 재확인이 필요하다."
```

---

### Task 3: 사다리 계산

**Files:**
- Create: `src/autotrading7s/domain/ladder.py`
- Test: `tests/domain/test_ladder.py`

**Interfaces:**
- Consumes: `normalize_tick`, `Side`
- Produces:
  - `MIN_STAGES = 2`, `MAX_STAGES = 7`
  - `LadderConfigError(ValueError)`
  - `Ladder(anchor_price: int, drop_pct: Decimal, target_pct: Decimal, max_stages: int, amount_per_stage: int)` — frozen. 메서드: `trigger_price(stage) -> int`, `planned_qty(stage) -> int`, `planned_investment(stage) -> int`, `total_planned_investment() -> int`
  - `target_price(fill_price: int, target_pct: Decimal) -> int`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_ladder.py`**

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.ladder import Ladder, LadderConfigError, target_price

FIVE = Decimal("0.05")


def make_ladder(**over) -> Ladder:
    kwargs = dict(
        anchor_price=9_340,
        drop_pct=FIVE,
        target_pct=FIVE,
        max_stages=7,
        amount_per_stage=1_000_000,
    )
    kwargs.update(over)
    return Ladder(**kwargs)  # type: ignore[arg-type]


# 설계서 3.1절 예시 표를 그대로 고정한다.
# (단계, 발동가, 수량, 투입금액, 누적투입)
SPEC_TABLE = [
    (1, 9_340, 107, 999_380, 999_380),
    (2, 8_870, 112, 993_440, 1_992_820),
    (3, 8_400, 119, 999_600, 2_992_420),
    (4, 7_930, 126, 999_180, 3_991_600),
    (5, 7_470, 133, 993_510, 4_985_110),
    (6, 7_000, 142, 994_000, 5_979_110),
    (7, 6_530, 153, 999_090, 6_978_200),
]


@pytest.mark.parametrize(("stage", "trigger", "qty", "invest", "cum"), SPEC_TABLE)
def test_matches_spec_table(stage: int, trigger: int, qty: int, invest: int, cum: int):
    ladder = make_ladder()
    assert ladder.trigger_price(stage) == trigger
    assert ladder.planned_qty(stage) == qty
    assert ladder.planned_investment(stage) == invest


def test_total_planned_investment_matches_spec():
    assert make_ladder().total_planned_investment() == 6_978_200


def test_total_quantity_matches_spec():
    ladder = make_ladder()
    assert sum(ladder.planned_qty(s) for s in range(1, 8)) == 892


def test_stage_one_trigger_equals_anchor_when_tick_aligned():
    assert make_ladder(anchor_price=10_000).trigger_price(1) == 10_000


def test_trigger_price_is_monotonically_decreasing():
    ladder = make_ladder()
    prices = [ladder.trigger_price(s) for s in range(1, 8)]
    assert prices == sorted(prices, reverse=True)


def test_stage_out_of_range():
    ladder = make_ladder()
    with pytest.raises(ValueError):
        ladder.trigger_price(0)
    with pytest.raises(ValueError):
        ladder.trigger_price(8)


def test_rejects_stage_count_out_of_range():
    with pytest.raises(LadderConfigError):
        make_ladder(max_stages=1)
    with pytest.raises(LadderConfigError):
        make_ladder(max_stages=8)


def test_rejects_when_first_stage_cannot_buy_one_share():
    """설계서 3.1절: 1주도 살 수 없는 설정은 등록 시점에 거부한다."""
    with pytest.raises(LadderConfigError, match="1주도 매수 불가"):
        make_ladder(anchor_price=161_200, amount_per_stage=100_000)


def test_rejects_drop_pct_that_drives_price_nonpositive():
    with pytest.raises(LadderConfigError):
        make_ladder(drop_pct=Decimal("0.20"), max_stages=7)


@pytest.mark.parametrize(
    ("drop", "stages"), [(Decimal("0"), 7), (Decimal("1"), 7), (Decimal("-0.05"), 7)]
)
def test_rejects_invalid_drop_pct(drop: Decimal, stages: int):
    with pytest.raises(LadderConfigError):
        make_ladder(drop_pct=drop, max_stages=stages)


def test_rejects_total_drop_exactly_one_boundary():
    """total_drop == 1 경계도 거부한다 (`>=` 비교)."""
    with pytest.raises(LadderConfigError):
        make_ladder(drop_pct=Decimal("0.25"), max_stages=5)


@pytest.mark.parametrize(
    ("anchor", "drop", "stages", "amount"),
    [
        (3, Decimal("0.4"), 3, 3),                 # 마지막 단계 원시값 0.6
        (10, Decimal("0.16"), 7, 1_000_000),       # 원시값 0.4
        (1_000, Decimal("0.1666"), 7, 1_000_000),  # 원시값 0.4 — 현실적 가격대
    ],
)
def test_rejects_last_stage_raw_price_below_one_won(
    anchor: int, drop: Decimal, stages: int, amount: int
):
    """정규화 내림이 0을 만드는 설정은 생성 시점에 거부한다.

    이 가드가 없으면 생성은 성공하고 trigger_price(마지막) 호출이 bare
    ValueError 로 터진다 — 검증을 통과한 객체가 나중에 터지는 것이다.
    """
    with pytest.raises(LadderConfigError, match="below 1 won"):
        make_ladder(anchor_price=anchor, drop_pct=drop, max_stages=stages,
                    amount_per_stage=amount)


def test_accepts_last_stage_raw_price_exactly_one_won():
    """경계에서 거부 방향 off-by-one 이 없어야 한다.

    anchor 10 × (1 - 0.15×6) = 10 × 0.10 = 1.0 → 정확히 1원.
    """
    ladder = make_ladder(anchor_price=10, drop_pct=Decimal("0.15"), max_stages=7,
                         amount_per_stage=1_000_000)
    assert ladder.trigger_price(7) == 1


def test_rejects_nonpositive_amounts():
    with pytest.raises(LadderConfigError):
        make_ladder(amount_per_stage=0)
    with pytest.raises(LadderConfigError):
        make_ladder(anchor_price=0)


@pytest.mark.parametrize(
    ("fill", "expected"),
    [
        (10_000, 10_500),   # 설계서 14.1절 1단계
        (9_480, 9_960),     # 2단계 (9,954 → 올림)
        (8_950, 9_400),     # 3단계 (9,397.5 → 올림)
        (8_400, 8_820),     # 설계서 규칙2 갭하락 예시
    ],
)
def test_target_price_uses_fill_price_and_ceils(fill: int, expected: int):
    """목표가는 발동가가 아니라 실제 체결가 기준 — 설계서 3.1절."""
    assert target_price(fill, FIVE) == expected


def test_target_price_rejects_nonpositive():
    with pytest.raises(ValueError):
        target_price(0, FIVE)


def test_ladder_is_frozen():
    import dataclasses

    ladder = make_ladder()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ladder.anchor_price = 1  # type: ignore[misc]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_ladder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.ladder'`

- [ ] **Step 3: `src/autotrading7s/domain/ladder.py` 구현**

```python
"""사다리 계산 — 설계서 3.1절.

D3에 따라 매수 트리거 기준점은 1단계 체결가 대비 누적이다. 앵커가 확정되면
사다리 전체가 사전에 결정되므로 자금계획과 총한도를 미리 계산할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autotrading7s.domain.tick_size import normalize_tick
from autotrading7s.domain.types import Side

MIN_STAGES = 2
MAX_STAGES = 7


class LadderConfigError(ValueError):
    """사다리 설정이 실행 불가능할 때. 설정 등록 시점에 던진다."""


@dataclass(frozen=True, slots=True)
class Ladder:
    """사이클 시작 시 1회 계산되어 사이클 종료까지 불변인 매수 계획."""

    anchor_price: int
    drop_pct: Decimal
    target_pct: Decimal
    max_stages: int
    amount_per_stage: int

    def __post_init__(self) -> None:
        if not MIN_STAGES <= self.max_stages <= MAX_STAGES:
            raise LadderConfigError(
                f"max_stages must be {MIN_STAGES}~{MAX_STAGES}: {self.max_stages}"
            )
        if self.anchor_price <= 0:
            raise LadderConfigError(f"anchor_price must be positive: {self.anchor_price}")
        if self.amount_per_stage <= 0:
            raise LadderConfigError(
                f"amount_per_stage must be positive: {self.amount_per_stage}"
            )
        if not Decimal(0) < self.drop_pct < Decimal(1):
            raise LadderConfigError(f"drop_pct must be in (0, 1): {self.drop_pct}")
        if self.target_pct <= 0:
            raise LadderConfigError(f"target_pct must be positive: {self.target_pct}")

        total_drop = self.drop_pct * (self.max_stages - 1)
        if total_drop >= Decimal(1):
            raise LadderConfigError(
                f"drop_pct {self.drop_pct} × {self.max_stages - 1}단계 = {total_drop} "
                "→ 마지막 단계 발동가가 0 이하가 된다"
            )

        # 마지막 단계의 원시 발동가(정규화 전)가 1원 이상이어야 한다.
        # 위 total_drop 가드는 "수식이 음수가 아님"만 보장하는데, trigger_price 는
        # normalize_tick 으로 내림하므로 원시값이 (0,1) 구간이면 0으로 내려가고
        # tick_unit(0) 이 ValueError 를 던진다. 그러면 검증을 통과한 Ladder 가
        # 호출 시점에 터진다. 원시값 ≥ 1 이면 그 가격대의 호가 단위가 1원이므로
        # 내림 결과도 ≥ 1 이 보장된다. 발동가는 단계가 올라갈수록 낮아지므로
        # 마지막 단계만 검사하면 충분하다.
        last_raw = Decimal(self.anchor_price) * (
            Decimal(1) - self.drop_pct * (self.max_stages - 1)
        )
        if last_raw < Decimal(1):
            raise LadderConfigError(
                f"last stage raw trigger price below 1 won: {last_raw} "
                f"(anchor {self.anchor_price} × (1 - {self.drop_pct} × "
                f"{self.max_stages - 1}))"
            )

        # 발동가는 단계가 올라갈수록 낮아지므로 1단계에서 1주를 살 수 있으면
        # 모든 단계에서 살 수 있다. 1단계만 검사하면 충분하다.
        first_price = self.trigger_price(1)
        if self.amount_per_stage // first_price == 0:
            raise LadderConfigError(
                f"1단계 발동가 {first_price:,}원 > 단계금액 "
                f"{self.amount_per_stage:,}원 — 1주도 매수 불가"
            )

    def trigger_price(self, stage: int) -> int:
        """D3: anchor × (1 - drop×(n-1)). 호가 단위 내림."""
        self._check_stage(stage)
        raw = Decimal(self.anchor_price) * (Decimal(1) - self.drop_pct * (stage - 1))
        return normalize_tick(raw, Side.BUY)

    def planned_qty(self, stage: int) -> int:
        """D5 균등 금액 배분: floor(단계금액 / 발동가)."""
        return self.amount_per_stage // self.trigger_price(stage)

    def planned_investment(self, stage: int) -> int:
        return self.planned_qty(stage) * self.trigger_price(stage)

    def total_planned_investment(self) -> int:
        """계획 기준 총투입. 실제 한도 검사는 실체결금액으로 한다(설계서 6절)."""
        return sum(self.planned_investment(s) for s in range(1, self.max_stages + 1))

    def _check_stage(self, stage: int) -> None:
        if not 1 <= stage <= self.max_stages:
            raise ValueError(f"stage out of range 1~{self.max_stages}: {stage}")


def target_price(fill_price: int, target_pct: Decimal) -> int:
    """목표 매도가.

    발동가가 아니라 **실제 체결가** 기준이다(설계서 3.1절). 갭하락으로 여러
    단계가 같은 가격에 채워지면 발동가는 서로 달라도 목표가는 같아진다.
    이 때문에 목표가 계산은 ``Ladder`` 의 메서드가 아니라 별도 함수다.

    호가 단위 올림 — 내림하면 목표수익률에 미달한 채로 팔린다.
    """
    if fill_price <= 0:
        raise ValueError(f"fill_price must be positive: {fill_price}")
    raw = Decimal(fill_price) * (Decimal(1) + target_pct)
    return normalize_tick(raw, Side.SELL)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_ladder.py -v`
Expected: PASS (설계서 예시 표 7행 + 나머지, 총 27 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/ladder.py tests/domain/test_ladder.py
git commit -m "feat: 사다리 계산 추가

설계서 3.1절과 D3. 1단계 체결가 대비 누적으로 발동가를 산출하고 호가 단위로
내림한다. 설계서에 실린 예시 표(총투입 6,978,200원, 892주)를 회귀 테스트로
고정했다.

target_price를 Ladder의 메서드가 아닌 별도 함수로 둔 이유는 목표가가 발동가가
아니라 실제 체결가에 매달려 있어 사다리와 생명주기가 다르기 때문이다."
```

---

### Task 4: 단계 상태기계

**Files:**
- Create: `src/autotrading7s/domain/stage.py`
- Test: `tests/domain/test_stage.py`

**Interfaces:**
- Consumes: `StageStatus`
- Produces:
  - `IllegalStageTransition(RuntimeError)`
  - `StageState(stage_no: int, status: StageStatus, trigger_price: int, planned_qty: int, fill_price: int | None = None, fill_qty: int | None = None, bought_at: datetime | None = None, last_sold_at: datetime | None = None, rebuy_count: int = 0)` — frozen. 프로퍼티 `held_qty -> int`
  - `to_buy_pending(state) -> StageState`
  - `to_holding(state, fill_price: int, fill_qty: int, at: datetime) -> StageState`
  - `to_sell_pending(state) -> StageState`
  - `after_sell(state, at: datetime, allow_rebuy: bool) -> StageState`
  - `cancel_buy(state) -> StageState`
  - `cancel_sell(state) -> StageState`
  - `force_sold(state, at: datetime) -> StageState` — 긴급청산 전용, 전이표 우회

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_stage.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from autotrading7s.domain.stage import (
    IllegalStageTransition,
    StageState,
    after_sell,
    cancel_buy,
    cancel_sell,
    force_sold,
    to_buy_pending,
    to_holding,
    to_sell_pending,
)
from autotrading7s.domain.types import StageStatus

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def waiting(stage_no: int = 2) -> StageState:
    return StageState(
        stage_no=stage_no,
        status=StageStatus.WAITING,
        trigger_price=9_500,
        planned_qty=105,
    )


def holding() -> StageState:
    return to_holding(to_buy_pending(waiting()), fill_price=9_480, fill_qty=105, at=T0)


def test_happy_path_buy_then_sell_with_rebuy():
    st = waiting()
    assert st.held_qty == 0

    st = to_buy_pending(st)
    assert st.status is StageStatus.BUY_PENDING
    assert st.held_qty == 0, "PENDING 중에는 보유수량으로 세지 않는다"

    st = to_holding(st, fill_price=9_480, fill_qty=105, at=T0)
    assert st.status is StageStatus.HOLDING
    assert (st.fill_price, st.fill_qty, st.bought_at) == (9_480, 105, T0)
    assert st.held_qty == 105

    st = to_sell_pending(st)
    assert st.status is StageStatus.SELL_PENDING
    assert st.held_qty == 105, "매도 체결 전까지는 여전히 보유"

    sold_at = T0 + timedelta(minutes=10)
    st = after_sell(st, at=sold_at, allow_rebuy=True)
    assert st.status is StageStatus.WAITING
    assert st.last_sold_at == sold_at
    assert st.rebuy_count == 1
    assert st.fill_price is None and st.fill_qty is None
    assert st.held_qty == 0
    assert st.trigger_price == 9_500, "발동가는 사다리에 고정되어 변하지 않는다"


def test_after_sell_without_rebuy_is_terminal():
    st = after_sell(to_sell_pending(holding()), at=T0, allow_rebuy=False)
    assert st.status is StageStatus.SOLD
    assert st.rebuy_count == 0
    with pytest.raises(IllegalStageTransition):
        to_buy_pending(st)


def test_cancel_buy_returns_to_waiting():
    st = cancel_buy(to_buy_pending(waiting()))
    assert st.status is StageStatus.WAITING
    assert st.fill_price is None


def test_cancel_sell_returns_to_holding():
    """매도 주문이 체결 없이 취소되면 보유로 되돌아간다."""
    st = cancel_sell(to_sell_pending(holding()))
    assert st.status is StageStatus.HOLDING
    assert st.held_qty == 105


def test_partial_buy_fill_confirms_with_filled_quantity_only():
    """설계서 4.1절: 매수 부분체결은 체결 수량만으로 HOLDING 확정."""
    st = to_holding(to_buy_pending(waiting()), fill_price=9_480, fill_qty=60, at=T0)
    assert st.status is StageStatus.HOLDING
    assert st.fill_qty == 60
    assert st.planned_qty == 105, "계획 수량은 기록으로 남는다"


@pytest.mark.parametrize(
    ("from_status", "action"),
    [
        (StageStatus.WAITING, "to_holding"),
        (StageStatus.WAITING, "to_sell_pending"),
        (StageStatus.WAITING, "cancel_buy"),
        (StageStatus.BUY_PENDING, "to_buy_pending"),
        (StageStatus.BUY_PENDING, "to_sell_pending"),
        (StageStatus.HOLDING, "to_buy_pending"),
        (StageStatus.HOLDING, "to_holding"),
        (StageStatus.SELL_PENDING, "to_sell_pending"),
        (StageStatus.SOLD, "to_sell_pending"),
        (StageStatus.SOLD, "cancel_buy"),
    ],
)
def test_illegal_transitions_are_rejected(from_status: StageStatus, action: str):
    st = StageState(
        stage_no=2, status=from_status, trigger_price=9_500, planned_qty=105,
        fill_price=9_480, fill_qty=105,
    )
    fn = {
        "to_buy_pending": lambda s: to_buy_pending(s),
        "to_holding": lambda s: to_holding(s, fill_price=1, fill_qty=1, at=T0),
        "to_sell_pending": lambda s: to_sell_pending(s),
        "cancel_buy": lambda s: cancel_buy(s),
    }[action]
    with pytest.raises(IllegalStageTransition):
        fn(st)


@pytest.mark.parametrize(
    "status",
    [StageStatus.WAITING, StageStatus.BUY_PENDING, StageStatus.HOLDING,
     StageStatus.SELL_PENDING],
)
def test_force_sold_bypasses_transition_table(status: StageStatus):
    """긴급청산은 Trigger Engine을 우회하는 별도 경로다 (설계서 11.1절)."""
    st = StageState(stage_no=3, status=status, trigger_price=9_000, planned_qty=111,
                    fill_price=8_950, fill_qty=111)
    forced = force_sold(st, at=T0)
    assert forced.status is StageStatus.SOLD
    assert forced.last_sold_at == T0
    assert forced.held_qty == 0


def test_force_sold_on_already_sold_is_idempotent():
    st = StageState(stage_no=3, status=StageStatus.SOLD, trigger_price=9_000,
                    planned_qty=111)
    assert force_sold(st, at=T0).status is StageStatus.SOLD


def test_state_is_frozen():
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        waiting().status = StageStatus.HOLDING  # type: ignore[misc]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_stage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.stage'`

- [ ] **Step 3: `src/autotrading7s/domain/stage.py` 구현**

```python
"""단계 상태기계 — 설계서 4.1절.

BUY_PENDING / SELL_PENDING 이라는 중간 상태가 중복 주문을 막는 유일한
방어선이다. WebSocket 시세는 초당 수십 틱이 오므로, 주문을 보내고 응답을
기다리는 동안 상태가 WAITING 으로 남아 있으면 그 틱마다 새 주문이 나간다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from autotrading7s.domain.types import StageStatus


class IllegalStageTransition(RuntimeError):
    """전이표가 허용하지 않는 상태 전이."""


# 설계서 4.1절 전이도.
_ALLOWED: dict[StageStatus, frozenset[StageStatus]] = {
    StageStatus.WAITING: frozenset({StageStatus.BUY_PENDING}),
    StageStatus.BUY_PENDING: frozenset({StageStatus.HOLDING, StageStatus.WAITING}),
    StageStatus.HOLDING: frozenset({StageStatus.SELL_PENDING}),
    # SELL_PENDING → HOLDING 은 매도 주문이 체결 없이 취소된 경우다.
    # 설계서 4.1절 전이도에 명시되지 않았으나 미체결 취소 처리에 필요하다.
    StageStatus.SELL_PENDING: frozenset(
        {StageStatus.HOLDING, StageStatus.WAITING, StageStatus.SOLD}
    ),
    StageStatus.SOLD: frozenset(),
}


@dataclass(frozen=True, slots=True)
class StageState:
    stage_no: int
    status: StageStatus
    trigger_price: int
    planned_qty: int
    fill_price: int | None = None
    fill_qty: int | None = None
    bought_at: datetime | None = None
    last_sold_at: datetime | None = None
    rebuy_count: int = 0

    @property
    def held_qty(self) -> int:
        """실제 보유 수량. PENDING 매수 중에는 아직 0이다."""
        if self.status in (StageStatus.HOLDING, StageStatus.SELL_PENDING):
            return self.fill_qty or 0
        return 0


def _guard(state: StageState, to: StageStatus) -> None:
    if to not in _ALLOWED[state.status]:
        raise IllegalStageTransition(
            f"stage {state.stage_no}: {state.status.value} → {to.value} 는 허용되지 않음"
        )


def to_buy_pending(state: StageState) -> StageState:
    _guard(state, StageStatus.BUY_PENDING)
    return replace(state, status=StageStatus.BUY_PENDING)


def to_holding(
    state: StageState, *, fill_price: int, fill_qty: int, at: datetime
) -> StageState:
    """매수 체결 반영.

    부분체결이면 ``fill_qty`` 가 ``planned_qty`` 보다 작다. 설계서 4.1절에 따라
    체결 수량만으로 확정하며 잔량을 쫓지 않는다.
    """
    _guard(state, StageStatus.HOLDING)
    if fill_price <= 0 or fill_qty <= 0:
        raise ValueError(f"invalid fill: price={fill_price} qty={fill_qty}")
    return replace(
        state,
        status=StageStatus.HOLDING,
        fill_price=fill_price,
        fill_qty=fill_qty,
        bought_at=at,
    )


def to_sell_pending(state: StageState) -> StageState:
    _guard(state, StageStatus.SELL_PENDING)
    return replace(state, status=StageStatus.SELL_PENDING)


def after_sell(state: StageState, *, at: datetime, allow_rebuy: bool) -> StageState:
    """매도 전량 체결 반영.

    ``allow_rebuy`` 면 WAITING 으로 복귀하여 같은 발동가에서 재매수 대상이 되고,
    아니면 SOLD 로 종료된다. 발동가는 사다리에 고정되어 있어 변하지 않는다.
    """
    target = StageStatus.WAITING if allow_rebuy else StageStatus.SOLD
    _guard(state, target)
    return replace(
        state,
        status=target,
        fill_price=None,
        fill_qty=None,
        bought_at=None,
        last_sold_at=at,
        rebuy_count=state.rebuy_count + (1 if allow_rebuy else 0),
    )


def cancel_buy(state: StageState) -> StageState:
    """매수 주문 미체결 취소 → 대기 복귀. 다음 틱에 재시도된다."""
    _guard(state, StageStatus.WAITING)
    return replace(state, status=StageStatus.WAITING)


def cancel_sell(state: StageState) -> StageState:
    """매도 주문이 체결 없이 취소됨 → 보유 복귀."""
    _guard(state, StageStatus.HOLDING)
    return replace(state, status=StageStatus.HOLDING)


def force_sold(state: StageState, *, at: datetime) -> StageState:
    """긴급청산 전용 — 전이표를 우회한다.

    설계서 11.1절은 긴급청산을 Trigger Engine 을 거치지 않는 별도 경로로
    규정한다. 이 함수는 그 설계를 코드에 반영한 것이며, 일반 전이 경로에서는
    절대 호출하지 않는다. 이미 SOLD 인 단계에 대해 멱등하다.
    """
    return replace(
        state,
        status=StageStatus.SOLD,
        fill_price=None,
        fill_qty=None,
        bought_at=None,
        last_sold_at=at,
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_stage.py -v`
Expected: PASS (20 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/stage.py tests/domain/test_stage.py
git commit -m "feat: 단계 상태기계 추가

설계서 4.1절. PENDING 중간 상태가 중복 주문을 막는 방어선이므로 held_qty가
PENDING 매수 중에는 0을 반환한다.

설계서 전이도에 없던 SELL_PENDING → HOLDING(매도 미체결 취소)을 추가했다.
긴급청산용 force_sold는 전이표를 우회하는 별도 함수로 두어, 설계서 11.1절의
'Trigger Engine을 거치지 않는 별도 경로'를 코드 구조에 반영했다."
```

---

### Task 5: 사이클 상태기계

**Files:**
- Create: `src/autotrading7s/domain/cycle.py`
- Test: `tests/domain/test_cycle.py`

**Interfaces:**
- Consumes: `CycleStatus`, `CloseReason`, `Ladder`
- Produces:
  - `IllegalCycleTransition(RuntimeError)`
  - `Cycle(cycle_id: int, config_id: int, seq: int, status: CycleStatus, anchor_price: int | None = None, ladder: Ladder | None = None, close_reason: CloseReason | None = None, started_at: datetime | None = None, closed_at: datetime | None = None)` — frozen. 프로퍼티 `accepts_triggers -> bool`, `is_active -> bool`
  - `start(cycle, at) -> Cycle`, `confirm_anchor(cycle, anchor_price, ladder, at) -> Cycle`, `abort_start(cycle) -> Cycle`, `pause(cycle) -> Cycle`, `resume(cycle) -> Cycle`, `begin_liquidation(cycle) -> Cycle`, `close(cycle, reason, at) -> Cycle`
  - `is_cycle_complete(states: Sequence[StageState]) -> bool`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_cycle.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import (
    Cycle,
    IllegalCycleTransition,
    abort_start,
    begin_liquidation,
    close,
    confirm_anchor,
    is_cycle_complete,
    pause,
    resume,
    start,
)
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def idle() -> Cycle:
    return Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)


def ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running() -> Cycle:
    return confirm_anchor(start(idle(), at=T0), anchor_price=10_000,
                          ladder=ladder(), at=T0)


def test_starting_does_not_accept_triggers():
    """앵커가 없으면 사다리를 계산할 수 없다 — 설계서 4.2절."""
    cyc = start(idle(), at=T0)
    assert cyc.status is CycleStatus.STARTING
    assert cyc.anchor_price is None
    assert cyc.accepts_triggers is False
    assert cyc.is_active is True


def test_confirm_anchor_fixes_ladder_and_enables_triggers():
    cyc = running()
    assert cyc.status is CycleStatus.RUNNING
    assert cyc.anchor_price == 10_000
    assert cyc.ladder is not None
    assert cyc.accepts_triggers is True


def test_abort_start_returns_to_idle():
    """1단계 주문이 미체결·취소되면 사이클이 성립하지 않는다."""
    cyc = abort_start(start(idle(), at=T0))
    assert cyc.status is CycleStatus.IDLE
    assert cyc.anchor_price is None


@pytest.mark.parametrize(
    "status",
    [CycleStatus.IDLE, CycleStatus.STARTING, CycleStatus.PAUSED,
     CycleStatus.LIQUIDATING, CycleStatus.CLOSED],
)
def test_only_running_accepts_triggers(status: CycleStatus):
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status,
                anchor_price=10_000, ladder=ladder())
    assert cyc.accepts_triggers is False


def test_pause_and_resume():
    cyc = pause(running())
    assert cyc.status is CycleStatus.PAUSED
    assert cyc.accepts_triggers is False
    assert resume(cyc).status is CycleStatus.RUNNING


def test_liquidation_from_running_and_paused():
    assert begin_liquidation(running()).status is CycleStatus.LIQUIDATING
    assert begin_liquidation(pause(running())).status is CycleStatus.LIQUIDATING


def test_close_records_reason_and_time():
    cyc = close(running(), reason=CloseReason.NORMAL, at=T0)
    assert cyc.status is CycleStatus.CLOSED
    assert cyc.close_reason is CloseReason.NORMAL
    assert cyc.closed_at == T0


def test_close_from_liquidating_records_emergency():
    cyc = close(begin_liquidation(running()), reason=CloseReason.EMERGENCY, at=T0)
    assert cyc.close_reason is CloseReason.EMERGENCY


def test_paused_can_be_closed():
    """외부에서 수동 전량 매도된 종목은 PAUSED 에서 종료할 수 있어야 한다."""
    assert close(pause(running()), reason=CloseReason.NORMAL, at=T0).status \
        is CycleStatus.CLOSED


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (CycleStatus.IDLE, "pause"),
        (CycleStatus.IDLE, "resume"),
        (CycleStatus.IDLE, "begin_liquidation"),
        (CycleStatus.RUNNING, "start"),
        (CycleStatus.RUNNING, "resume"),
        (CycleStatus.LIQUIDATING, "pause"),
        (CycleStatus.LIQUIDATING, "resume"),
        (CycleStatus.CLOSED, "start"),
        (CycleStatus.CLOSED, "pause"),
        (CycleStatus.CLOSED, "begin_liquidation"),
    ],
)
def test_illegal_cycle_transitions(status: CycleStatus, action: str):
    cyc = Cycle(cycle_id=1, config_id=1, seq=1, status=status)
    fn = {
        "start": lambda c: start(c, at=T0),
        "pause": pause,
        "resume": resume,
        "begin_liquidation": begin_liquidation,
    }[action]
    with pytest.raises(IllegalCycleTransition):
        fn(cyc)


def test_confirm_anchor_only_from_starting():
    with pytest.raises(IllegalCycleTransition):
        confirm_anchor(idle(), anchor_price=10_000, ladder=ladder(), at=T0)


def _stage(no: int, status: StageStatus, qty: int | None = None) -> StageState:
    return StageState(stage_no=no, status=status, trigger_price=10_000 - no * 500,
                      planned_qty=100, fill_price=9_000 if qty else None, fill_qty=qty)


def test_is_cycle_complete_when_no_holdings():
    """설계서 4.2절: 보유수량 0 도달이 사이클 종료 조건."""
    states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.WAITING)]
    assert is_cycle_complete(states) is True


def test_is_cycle_not_complete_while_holding():
    states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.HOLDING, qty=105)]
    assert is_cycle_complete(states) is False


def test_is_cycle_not_complete_while_pending():
    """PENDING 주문이 남아 있으면 아직 종료가 아니다."""
    states = [_stage(1, StageStatus.SOLD), _stage(2, StageStatus.BUY_PENDING)]
    assert is_cycle_complete(states) is False
    states = [_stage(1, StageStatus.SELL_PENDING, qty=100)]
    assert is_cycle_complete(states) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_cycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.cycle'`

- [ ] **Step 3: `src/autotrading7s/domain/cycle.py` 구현**

```python
"""사이클 상태기계 — 설계서 4.2절.

STARTING 은 앵커가 아직 없는 구간이다. 사다리를 계산할 수 없으므로 트리거
판정을 전혀 하지 않는다. 1단계가 체결되어 앵커가 확정되는 순간 RUNNING 으로
전이하고 사다리가 사이클에 박제된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus


class IllegalCycleTransition(RuntimeError):
    """전이표가 허용하지 않는 사이클 상태 전이."""


_ALLOWED: dict[CycleStatus, frozenset[CycleStatus]] = {
    CycleStatus.IDLE: frozenset({CycleStatus.STARTING}),
    # STARTING → LIQUIDATING: 설계서 4.2절이 긴급청산을 "어느 상태에서든"으로
    # 규정한다. STARTING 은 1단계 매수 주문이 체결 대기 중인 상태이며, 급락 중이라면
    # 사용자가 가장 절실하게 빠져나오려는 순간이다.
    CycleStatus.STARTING: frozenset(
        {CycleStatus.RUNNING, CycleStatus.IDLE, CycleStatus.LIQUIDATING}
    ),
    CycleStatus.RUNNING: frozenset(
        {CycleStatus.PAUSED, CycleStatus.LIQUIDATING, CycleStatus.CLOSED}
    ),
    # PAUSED → CLOSED 는 대사 불일치로 정지된 뒤 외부에서 수동 전량 매도된
    # 종목을 정리하는 경로다(설계서 10.2절).
    CycleStatus.PAUSED: frozenset(
        {CycleStatus.RUNNING, CycleStatus.LIQUIDATING, CycleStatus.CLOSED}
    ),
    CycleStatus.LIQUIDATING: frozenset({CycleStatus.CLOSED}),
    CycleStatus.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Cycle:
    cycle_id: int
    config_id: int
    seq: int
    status: CycleStatus
    anchor_price: int | None = None
    ladder: Ladder | None = None
    close_reason: CloseReason | None = None
    started_at: datetime | None = None
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        """앵커·사다리 불변식.

        RUNNING·PAUSED 는 둘 다 필수다. RUNNING 은 사다리를 실제로 읽는 유일한
        상태이고(accepts_triggers 가 그것으로 게이트), PAUSED 는 RUNNING 에서만
        도달하므로 항상 사다리를 갖는다.

        LIQUIDATING 은 필수가 아니다 — STARTING → LIQUIDATING 이 허용되므로 앵커
        확정 전에 청산이 시작될 수 있다. 다만 앵커가 있으면 사다리도 있어야 하고
        일치해야 한다.

        일치 검사를 여기에 두는 이유는 confirm_anchor 의 호출부 검사만으로는
        직접 생성(Plan 2 의 SQLite 행 복원)을 막지 못하기 때문이다.
        """
        if self.status in (CycleStatus.RUNNING, CycleStatus.PAUSED):
            if self.anchor_price is None:
                raise ValueError(
                    f"Cycle status {self.status.value} requires anchor_price, got None"
                )
            if self.ladder is None:
                raise ValueError(
                    f"Cycle status {self.status.value} requires ladder, got None"
                )
        if self.anchor_price is not None and self.ladder is not None:
            if self.anchor_price != self.ladder.anchor_price:
                raise ValueError(
                    f"anchor_price {self.anchor_price} != "
                    f"ladder.anchor_price {self.ladder.anchor_price}"
                )
        if self.status is CycleStatus.LIQUIDATING and self.anchor_price is not None:
            if self.ladder is None:
                raise ValueError(
                    "Cycle status LIQUIDATING with anchor_price requires ladder, got None"
                )

    @property
    def is_active(self) -> bool:
        return self.status in (CycleStatus.STARTING, CycleStatus.RUNNING)

    @property
    def accepts_triggers(self) -> bool:
        """트리거 판정을 수행해도 되는 상태인가.

        RUNNING 만 허용한다. STARTING 은 앵커가 없어 사다리를 계산할 수 없고,
        PAUSED·LIQUIDATING 은 자동 트리거가 정지된 상태다.
        """
        return self.status is CycleStatus.RUNNING


def _guard(cycle: Cycle, to: CycleStatus) -> None:
    if to not in _ALLOWED[cycle.status]:
        raise IllegalCycleTransition(
            f"cycle {cycle.cycle_id}: {cycle.status.value} → {to.value} 는 허용되지 않음"
        )


def start(cycle: Cycle, *, at: datetime) -> Cycle:
    """사용자가 [시작]을 눌렀다. 1단계 주문을 내기 전 상태."""
    _guard(cycle, CycleStatus.STARTING)
    return replace(cycle, status=CycleStatus.STARTING, started_at=at)


def confirm_anchor(
    cycle: Cycle, *, anchor_price: int, ladder: Ladder, at: datetime
) -> Cycle:
    """1단계가 체결되어 앵커가 확정됐다. 사다리를 사이클에 고정한다."""
    _guard(cycle, CycleStatus.RUNNING)
    if anchor_price != ladder.anchor_price:
        raise ValueError(
            f"anchor mismatch: {anchor_price} != ladder {ladder.anchor_price}"
        )
    return replace(
        cycle,
        status=CycleStatus.RUNNING,
        anchor_price=anchor_price,
        ladder=ladder,
        started_at=cycle.started_at or at,
    )


def abort_start(cycle: Cycle) -> Cycle:
    """1단계 주문이 미체결·거부되어 사이클이 성립하지 않았다."""
    _guard(cycle, CycleStatus.IDLE)
    return replace(cycle, status=CycleStatus.IDLE, started_at=None)


def pause(cycle: Cycle) -> Cycle:
    """자동 트리거 정지, 보유는 유지 (설계서 D11)."""
    _guard(cycle, CycleStatus.PAUSED)
    return replace(cycle, status=CycleStatus.PAUSED)


def resume(cycle: Cycle) -> Cycle:
    _guard(cycle, CycleStatus.RUNNING)
    return replace(cycle, status=CycleStatus.RUNNING)


def begin_liquidation(cycle: Cycle) -> Cycle:
    """긴급청산 시작. 자동 트리거가 즉시 정지된다 (설계서 11.1절 ①)."""
    _guard(cycle, CycleStatus.LIQUIDATING)
    return replace(cycle, status=CycleStatus.LIQUIDATING)


def close(
    cycle: Cycle, *, reason: CloseReason, at: datetime, states: Sequence[StageState]
) -> Cycle:
    """사이클 종료. 보유가 남아 있으면 거부한다.

    states 를 선택 인자로 두면 기본값이 "검사 없음"이 되어 안전장치가 아니다.
    reason=EMERGENCY 에도 같은 검사를 적용한다 — 긴급청산이 부분 체결되면
    (설계서 11절 result=PARTIAL) 보유가 남으므로 CLOSED 로 표시해서는 안 된다.
    그러면 내부 기록과 실계좌가 갈라진다(설계서 10.2절).
    """
    _guard(cycle, CycleStatus.CLOSED)
    if not is_cycle_complete(states):
        # 거부 사유를 구분한다. held_qty 는 PENDING 상태에서 0이므로, 사유가
        # 미체결 주문일 때 수량만 말하면 "0주 보유 중"이라는 모순된 메시지가 된다.
        pending = (StageStatus.BUY_PENDING, StageStatus.SELL_PENDING)
        pending_stages = [s.stage_no for s in states if s.status in pending]
        if pending_stages:
            raise ValueError(
                f"cannot close cycle — pending orders on stages: {pending_stages}"
            )
        held = sum(s.held_qty for s in states)
        raise ValueError(
            f"cannot close cycle with {held} shares still held — not all stages complete"
        )
    return replace(cycle, status=CycleStatus.CLOSED, close_reason=reason, closed_at=at)


def is_cycle_complete(states: Sequence[StageState]) -> bool:
    """사이클 종료 조건 — 보유수량 0이고 진행 중인 주문도 없다.

    설계서 4.2절은 '보유수량 0 도달'을 종료 조건으로 규정한다. PENDING 주문이
    남아 있으면 곧 보유가 생길 수 있으므로 종료로 보지 않는다.

    빈 시퀀스는 데이터 정합성 실패다. all() 이 빈 시퀀스에서 True 를 반환하므로,
    검사 없이 두면 단계 로드 실패가 "사이클 완료"로 번역되어 주식을 보유한
    사이클이 닫힌다. False 반환은 문제를 숨긴 채 사이클을 영구히 미완료로
    남기므로, 조용한 정지보다 시끄러운 오류를 택한다.
    """
    if not states:
        raise ValueError("stage states sequence is empty — data integrity failure")
    pending = (StageStatus.BUY_PENDING, StageStatus.SELL_PENDING)
    if any(s.status in pending for s in states):
        return False
    return all(s.held_qty == 0 for s in states)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_cycle.py -v`
Expected: PASS (25 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/cycle.py tests/domain/test_cycle.py
git commit -m "feat: 사이클 상태기계 추가

설계서 4.2절. accepts_triggers가 RUNNING만 허용하므로, 앵커가 없는 STARTING
구간에서 사다리 계산이 시도되는 일이 구조적으로 불가능하다.

is_cycle_complete는 보유수량 0 외에 PENDING 주문 부재도 확인한다. 진행 중인
주문이 있으면 곧 보유가 생길 수 있어 종료로 볼 수 없다."
```

---

### Task 6: 손익 집계

**Files:**
- Create: `src/autotrading7s/domain/pnl.py`
- Test: `tests/domain/test_pnl.py`

**Interfaces:**
- Consumes: `StageState`, `StageStatus`
- Produces:
  - `invested_amount(states) -> int` — 보유 중 단계의 실체결금액 합
  - `held_qty(states) -> int`
  - `avg_price(states) -> int | None` — 반올림. 보유 0이면 `None`
  - `unrealized_pnl(states, current_price) -> int`
  - `unrealized_pnl_pct(states, current_price) -> Decimal | None` — 소수 2자리
  - `holding_stage_count(states) -> int`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_pnl.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from autotrading7s.domain.pnl import (
    avg_price,
    held_qty,
    holding_stage_count,
    invested_amount,
    unrealized_pnl,
    unrealized_pnl_pct,
)
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import StageStatus

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def held(stage_no: int, fill_price: int, fill_qty: int,
         status: StageStatus = StageStatus.HOLDING) -> StageState:
    return StageState(stage_no=stage_no, status=status, trigger_price=fill_price,
                      planned_qty=fill_qty, fill_price=fill_price,
                      fill_qty=fill_qty, bought_at=T0)


def waiting(stage_no: int) -> StageState:
    return StageState(stage_no=stage_no, status=StageStatus.WAITING,
                      trigger_price=8_000, planned_qty=125)


# 설계서 14.1절 보유현황 목업 — 삼성전자
SAMSUNG = [held(1, 10_000, 100), held(2, 9_480, 105), held(3, 8_950, 111)]
# 설계서 14.1절 보유현황 목업 — 카카오 (7단계 전부 보유)
KAKAO = [
    held(1, 10_000, 100), held(2, 9_500, 105), held(3, 9_000, 111),
    held(4, 8_500, 117), held(5, 8_000, 125), held(6, 7_500, 133),
    held(7, 7_000, 142),
]


def test_samsung_mockup_numbers():
    states = SAMSUNG + [waiting(4)]
    assert invested_amount(states) == 2_988_850
    assert held_qty(states) == 316
    assert avg_price(states) == 9_458
    assert holding_stage_count(states) == 3
    assert unrealized_pnl(states, 9_340) == -37_410
    assert unrealized_pnl_pct(states, 9_340) == Decimal("-1.25")


def test_kakao_mockup_numbers():
    assert invested_amount(KAKAO) == 6_982_500
    assert held_qty(KAKAO) == 833
    assert avg_price(KAKAO) == 8_382
    assert holding_stage_count(KAKAO) == 7
    assert unrealized_pnl(KAKAO, 7_910) == -393_470
    assert unrealized_pnl_pct(KAKAO, 7_910) == Decimal("-5.64")


def test_mockup_totals_add_up():
    """종목별 손익의 합이 설계서 목업의 합계와 일치해야 한다."""
    total = unrealized_pnl(SAMSUNG, 9_340) + unrealized_pnl(KAKAO, 7_910)
    assert total == -430_880


def test_sell_pending_counts_as_held():
    states = [held(1, 10_000, 100, status=StageStatus.SELL_PENDING)]
    assert held_qty(states) == 100
    assert invested_amount(states) == 1_000_000


def test_buy_pending_does_not_count():
    """PENDING 매수는 아직 보유가 아니다."""
    states = [
        StageState(stage_no=1, status=StageStatus.BUY_PENDING, trigger_price=10_000,
                   planned_qty=100)
    ]
    assert held_qty(states) == 0
    assert invested_amount(states) == 0


def test_empty_holdings():
    states = [waiting(1), waiting(2)]
    assert invested_amount(states) == 0
    assert held_qty(states) == 0
    assert avg_price(states) is None
    assert unrealized_pnl(states, 9_340) == 0
    assert unrealized_pnl_pct(states, 9_340) is None
    assert holding_stage_count(states) == 0


def test_profit_case():
    states = [held(1, 10_000, 100)]
    assert unrealized_pnl(states, 10_500) == 50_000
    assert unrealized_pnl_pct(states, 10_500) == Decimal("5.00")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_pnl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.pnl'`

- [ ] **Step 3: `src/autotrading7s/domain/pnl.py` 구현**

```python
"""손익 집계 — 설계서 12.3절·14.1절.

보유현황 표의 평균단가·평가손익은 이 모듈의 순수 함수로 계산한다. UI 파일
안에서 직접 계산하지 않는 이유는 설계서 14.4절에 있다 — GUI 코드는 개발
환경(Linux EC2)에서 자동 테스트가 불가능한 사각지대다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from autotrading7s.domain.stage import StageState

_CENT = Decimal("0.01")


def _held(states: Iterable[StageState]) -> list[StageState]:
    return [s for s in states if s.held_qty > 0 and s.fill_price is not None]


def invested_amount(states: Sequence[StageState]) -> int:
    """보유 중 단계의 실체결금액 합.

    총한도 검사도 이 기준(실체결금액)을 쓴다. 계획금액으로 세면
    floor(금액/가격) 오차 때문에 한도가 실제보다 헐거워진다(설계서 6절).
    """
    return sum(s.fill_price * s.held_qty for s in _held(states))  # type: ignore[operator]


def held_qty(states: Sequence[StageState]) -> int:
    return sum(s.held_qty for s in states)


def holding_stage_count(states: Sequence[StageState]) -> int:
    """보유현황 표의 '단계' 열에 쓰는 진행 단계 수."""
    return len(_held(states))


def avg_price(states: Sequence[StageState]) -> int | None:
    """가중 평균단가. 보유가 없으면 None."""
    qty = held_qty(states)
    if qty == 0:
        return None
    return int(
        (Decimal(invested_amount(states)) / qty).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )


def unrealized_pnl(states: Sequence[StageState], current_price: int) -> int:
    """평가손익 금액. 평단 반올림을 거치지 않고 실체결금액에서 직접 뺀다."""
    return held_qty(states) * current_price - invested_amount(states)


def unrealized_pnl_pct(
    states: Sequence[StageState], current_price: int
) -> Decimal | None:
    """평가손익률(%). 소수 2자리. 보유가 없으면 None."""
    invested = invested_amount(states)
    if invested == 0:
        return None
    ratio = Decimal(unrealized_pnl(states, current_price)) / invested * 100
    return ratio.quantize(_CENT, rounding=ROUND_HALF_UP)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_pnl.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/pnl.py tests/domain/test_pnl.py
git commit -m "feat: 손익 집계 추가

설계서 14.1절 보유현황 목업의 수치(삼성전자 -37,410원/-1.25%,
카카오 -393,470원/-5.64%, 합계 -430,880원)를 회귀 테스트로 고정했다.

평가손익은 반올림된 평단이 아니라 실체결금액에서 직접 계산한다. 평단을 거치면
반올림 오차가 금액에 증폭되어 종목별 손익의 합이 합계와 어긋난다."
```

---
### Task 7: 트리거 판정 — 매수 (규칙 2·4·5)

**Files:**
- Create: `src/autotrading7s/domain/rules.py`
- Test: `tests/domain/test_rules_buy.py`

**Interfaces:**
- Consumes: `Tick`, `TickSource`, `StageStatus`, `StageState`, `Cycle`, `Ladder`
- Produces:
  - `TriggerParams(target_pct: Decimal, allow_rebuy: bool = True, rebuy_cooldown_sec: int = 60)` — frozen
  - `BuyStage(stage_no: int, limit_price: int, qty: int, reason: str)` — frozen
  - `SellStage(stage_no: int, limit_price: int, qty: int, reason: str)` — frozen (태스크 8에서 사용)
  - `Decision = BuyStage | SellStage`
  - `decide(*, tick: Tick, cycle: Cycle, states: Sequence[StageState], params: TriggerParams, now: datetime, market_open: bool) -> list[Decision]`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_rules_buy.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import Cycle, confirm_anchor, pause, start
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.rules import BuyStage, TriggerParams, decide
from autotrading7s.domain.stage import StageState, to_buy_pending, to_holding
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
FIVE = Decimal("0.05")
PARAMS = TriggerParams(target_pct=FIVE)


def ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running_cycle(lad: Ladder | None = None) -> Cycle:
    lad = lad or ladder()
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    return confirm_anchor(start(idle, at=T0), anchor_price=lad.anchor_price,
                          ladder=lad, at=T0)


def fresh_states(lad: Ladder) -> list[StageState]:
    """1단계는 이미 체결(앵커 확정), 나머지는 대기."""
    states = [
        StageState(stage_no=1, status=StageStatus.HOLDING,
                   trigger_price=lad.trigger_price(1),
                   planned_qty=lad.planned_qty(1),
                   fill_price=lad.anchor_price, fill_qty=lad.planned_qty(1),
                   bought_at=T0)
    ]
    for n in range(2, lad.max_stages + 1):
        states.append(
            StageState(stage_no=n, status=StageStatus.WAITING,
                       trigger_price=lad.trigger_price(n),
                       planned_qty=lad.planned_qty(n))
        )
    return states


def tick(price: int, source: TickSource = TickSource.WS) -> Tick:
    return Tick(code="005930", price=price, at=T0, source=source)


def run(price: int, states, cycle=None, market_open=True, now=T0, params=PARAMS):
    return decide(tick=tick(price), cycle=cycle or running_cycle(),
                  states=states, params=params, now=now, market_open=market_open,
                  stock_code="005930")


def test_buys_next_stage_when_trigger_reached():
    lad = ladder()
    states = fresh_states(lad)
    decisions = run(9_500, states)
    assert len(decisions) == 1
    d = decisions[0]
    assert isinstance(d, BuyStage)
    assert d.stage_no == 2
    assert d.limit_price == 9_500, "지정가는 관측된 현재가로 발주한다"
    assert d.qty == lad.planned_qty(2)


def test_no_buy_above_trigger():
    assert run(9_501, fresh_states(ladder())) == []


def test_gap_down_buys_only_one_stage_per_tick():
    """규칙 2: 발동가 3개를 한꺼번에 통과해도 한 틱에 1단계만."""
    lad = ladder()
    decisions = run(8_400, fresh_states(lad))
    assert len(decisions) == 1
    assert decisions[0].stage_no == 2, "번호가 낮은 단계부터 채운다"


def test_gap_down_fills_sequentially_over_ticks():
    """8,400 에 머무는 동안 연속 틱으로 2 → 3 → 4 단계가 채워진다."""
    lad = ladder()
    states = fresh_states(lad)
    filled: list[int] = []

    for _ in range(3):
        decisions = run(8_400, states)
        assert len(decisions) == 1
        d = decisions[0]
        filled.append(d.stage_no)
        idx = d.stage_no - 1
        states[idx] = to_holding(
            to_buy_pending(states[idx]), fill_price=8_400, fill_qty=d.qty, at=T0
        )

    assert filled == [2, 3, 4]
    # 체결가는 발동가가 아니라 실제 체결가로 기록된다
    assert [states[i].fill_price for i in (1, 2, 3)] == [8_400, 8_400, 8_400]


@pytest.mark.parametrize(
    "status",
    [StageStatus.BUY_PENDING, StageStatus.SELL_PENDING, StageStatus.HOLDING,
     StageStatus.SOLD],
)
def test_rule5_excludes_non_waiting_stages(status: StageStatus):
    """규칙 5: PENDING 상태 단계는 판정 대상에서 제외한다."""
    lad = ladder()
    states = fresh_states(lad)
    states[1] = StageState(stage_no=2, status=status, trigger_price=9_500,
                           planned_qty=105, fill_price=9_500, fill_qty=105)
    decisions = run(9_500, states)
    # 2단계가 제외되면 3단계 발동가(9,000)에는 아직 못 미쳤으므로 결정 없음
    assert [d.stage_no for d in decisions if isinstance(d, BuyStage)] == []


def test_rule4_no_decision_outside_market_hours():
    """규칙 4: 장 운영시간 밖에서는 어떤 결정도 내리지 않는다."""
    assert run(8_400, fresh_states(ladder()), market_open=False) == []


def test_no_decision_while_starting():
    """앵커가 없으면 사다리를 계산할 수 없다."""
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    starting = start(idle, at=T0)
    lad = ladder()
    assert decide(tick=tick(8_400), cycle=starting, states=fresh_states(lad),
                  params=PARAMS, now=T0, market_open=True) == []


def test_no_decision_while_paused():
    assert run(8_400, fresh_states(ladder()), cycle=pause(running_cycle())) == []


def test_reason_records_trigger_basis():
    """설계서 12.2절: 판정 근거를 사람이 읽을 수 있게 남긴다."""
    reason = run(9_500, fresh_states(ladder()))[0].reason
    assert "stage=2 BUY" in reason
    assert "tick=9500(WS)" in reason
    assert "trigger=9500" in reason
    assert "anchor=10000" in reason
    assert "drop=5%" in reason
    assert "rule2_sequential" in reason


def test_reason_records_rest_poll_source():
    lad = ladder()
    d = decide(tick=tick(9_500, TickSource.REST_POLL), cycle=running_cycle(lad),
               states=fresh_states(lad), params=PARAMS, now=T0, market_open=True)[0]
    assert "tick=9500(REST_POLL)" in d.reason
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_rules_buy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.rules'`

- [ ] **Step 3: `src/autotrading7s/domain/rules.py` 구현 (매수 경로만)**

```python
"""트리거 판정 — 설계서 5절.

부작용이 없는 순수 함수다. 같은 입력에는 항상 같은 출력을 낸다. 네트워크·DB·
시계 없이 "이 틱이 왔을 때 무슨 일이 벌어져야 하나"를 밀리초 단위로 수천 케이스
검증할 수 있게 하려는 설계다.

이 모듈에는 **하락 조건 매도 분기가 존재하지 않는다.** 자동 손절매 배제
원칙(설계서 6절)을 코드 구조로 강제한 것이며, 누군가 손절 기능을 추가하려 하면
그것이 명확한 설계 변경으로 드러난다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import StageStatus, Tick


@dataclass(frozen=True, slots=True)
class TriggerParams:
    """판정에 필요한 설정값. split_config 에서 추출한 뷰."""

    target_pct: Decimal
    allow_rebuy: bool = True
    rebuy_cooldown_sec: int = 60


@dataclass(frozen=True, slots=True)
class BuyStage:
    stage_no: int
    limit_price: int
    qty: int
    reason: str


@dataclass(frozen=True, slots=True)
class SellStage:
    stage_no: int
    limit_price: int
    qty: int
    reason: str


Decision = BuyStage | SellStage


def decide(
    *,
    tick: Tick,
    cycle: Cycle,
    states: Sequence[StageState],
    params: TriggerParams,
    now: datetime,
    market_open: bool,
) -> list[Decision]:
    """이 틱에 집행할 결정 목록. 부작용 없음."""
    # 규칙 4 — 장 운영시간 밖에서는 어떤 결정도 내리지 않는다.
    if not market_open:
        return []
    # RUNNING 이 아니면 판정하지 않는다. STARTING 은 앵커가 없어 사다리를
    # 계산할 수 없고, PAUSED·LIQUIDATING 은 자동 트리거가 정지된 상태다.
    if not cycle.accepts_triggers or cycle.ladder is None:
        return []

    buy = _eval_buy(tick, cycle.ladder, states, params, now)
    return [buy] if buy is not None else []


def _eval_buy(
    tick: Tick,
    ladder: Ladder,
    states: Sequence[StageState],
    params: TriggerParams,
    now: datetime,
) -> BuyStage | None:
    """규칙 2 — 조건을 만족하는 대기 단계 중 번호가 가장 낮은 하나만."""
    by_no = {s.stage_no: s for s in states}
    for stage_no in range(1, ladder.max_stages + 1):
        state = by_no.get(stage_no)
        # 규칙 5 — WAITING 이 아닌 단계는 판정 대상이 아니다. PENDING 을
        # 제외하는 것이 중복 주문을 막는 방어선이다.
        if state is None or state.status is not StageStatus.WAITING:
            continue
        if tick.price > state.trigger_price:
            continue
        qty = ladder.planned_qty(stage_no)
        if qty <= 0:
            continue
        return BuyStage(
            stage_no=stage_no,
            # 지정가는 관측된 현재가로 발주한다. 미체결이면 3초 후 취소하고
            # 다음 틱에 재시도한다(설계서 9절).
            limit_price=tick.price,
            qty=qty,
            reason=_buy_reason(stage_no=stage_no, tick=tick,
                               trigger=state.trigger_price, ladder=ladder),
        )
    return None


def _pct(value: Decimal) -> str:
    return f"{(value * 100).normalize()}"


def _buy_reason(*, stage_no: int, tick: Tick, trigger: int, ladder: Ladder) -> str:
    return (
        f"stage={stage_no} BUY | tick={tick.price}({tick.source.value}) "
        f"<= trigger={trigger} | anchor={ladder.anchor_price} "
        f"drop={_pct(ladder.drop_pct)}% stage_gap={stage_no - 1} | rule2_sequential"
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_rules_buy.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/rules.py tests/domain/test_rules_buy.py
git commit -m "feat: 매수 트리거 판정 추가 (규칙 2·4·5)

설계서 5절. decide()는 부작용 없는 순수 함수이며, 규칙 2에 따라 한 틱에 매수
1단계만 집행한다. 갭하락으로 발동가 3개를 동시에 통과해도 연속 틱으로 순차
체결되며, 체결가는 발동가가 아니라 실제 체결가로 기록된다.

이 모듈에는 하락 조건 매도 분기가 존재하지 않는다. 자동 손절매 배제 원칙을
코드 구조로 강제한 것이다."
```

---

### Task 8: 트리거 판정 — 매도와 우선순위 (규칙 1)

**Files:**
- Modify: `src/autotrading7s/domain/rules.py`
- Test: `tests/domain/test_rules_sell.py`

**Interfaces:**
- Consumes: `target_price` (태스크 3), `BuyStage`·`SellStage`·`decide` (태스크 7)
- Produces: `decide()` 가 `SellStage` 를 반환할 수 있게 확장. 시그니처는 Task 7 수정 라운드에서 `stock_code: str` 필수 키워드가 추가된 형태이며 이 태스크에서 더 변경하지 않는다

**설계서 모호성 해소:** 설계서 규칙 1은 "매도 먼저 집행"이라고만 적혀 있어 같은 틱에 매도와 매수를 함께 반환할지가 불명확하다. **매도가 하나라도 있으면 그 틱에서는 매도만 반환한다**로 확정한다. 근거는 두 가지다. ① 규칙 1의 목적이 "예수금 확보 후 매수"인데, 같은 틱에 둘을 함께 내면 매도 대금이 들어오기 전에 매수가 나간다. ② 규칙 2의 "한 틱에 하나씩" 철학과 일관된다. 틱 간격이 통상 1초 미만이라 매수는 다음 틱에 평가되므로 실질 지연이 없다.

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_rules_sell.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import Cycle, confirm_anchor, start
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.rules import BuyStage, SellStage, TriggerParams, decide
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
FIVE = Decimal("0.05")
PARAMS = TriggerParams(target_pct=FIVE)


def ladder(anchor: int = 10_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running_cycle(lad: Ladder) -> Cycle:
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    return confirm_anchor(start(idle, at=T0), anchor_price=lad.anchor_price,
                          ladder=lad, at=T0)


def stage(lad: Ladder, no: int, status: StageStatus,
          fill_price: int | None = None, fill_qty: int | None = None,
          last_sold_at: datetime | None = None) -> StageState:
    return StageState(stage_no=no, status=status, trigger_price=lad.trigger_price(no),
                      planned_qty=lad.planned_qty(no), fill_price=fill_price,
                      fill_qty=fill_qty, last_sold_at=last_sold_at)


def run(price: int, states, lad: Ladder, params=PARAMS, now=T0):
    return decide(tick=Tick(code="005930", price=price, at=T0, source=TickSource.WS),
                  cycle=running_cycle(lad), states=states, params=params,
                  now=now, market_open=True, stock_code="005930")


def test_sells_when_target_reached():
    lad = ladder()
    states = [stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100)]
    decisions = run(10_500, states, lad)
    assert len(decisions) == 1
    d = decisions[0]
    assert isinstance(d, SellStage)
    assert d.stage_no == 1
    assert d.limit_price == 10_500, "목표가로 지정가 발주"
    assert d.qty == 100


def test_no_sell_below_target():
    lad = ladder()
    states = [stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100)]
    assert run(10_499, states, lad) == []


def test_sell_limit_uses_ceiled_target_price():
    """목표가는 체결가 × (1+목표율) 을 호가 단위로 올린 값이다."""
    lad = ladder()
    states = [stage(lad, 2, StageStatus.HOLDING, fill_price=9_480, fill_qty=105)]
    d = run(9_960, states, lad)[0]
    assert d.limit_price == 9_960   # 9,954 → 올림


def test_rule1_sell_takes_precedence_over_buy():
    """설계서 규칙 1 예시 시나리오를 그대로 재현한다.

    2단계: 매도완료 → 대기 (발동가 9,500)
    3단계: 보유, 체결가 9,000, 목표가 9,450
    현재가 9,500 → 두 조건이 동시에 충족되지만 매도만 집행한다.
    """
    lad = ladder()
    states = [
        stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100),
        stage(lad, 2, StageStatus.WAITING),
        stage(lad, 3, StageStatus.HOLDING, fill_price=9_000, fill_qty=111),
    ]
    decisions = run(9_500, states, lad)
    assert all(isinstance(d, SellStage) for d in decisions)
    assert [d.stage_no for d in decisions] == [3]
    assert not any(isinstance(d, BuyStage) for d in decisions)


def test_lower_stages_sell_first_when_multiple_targets_hit():
    """반등 구간에서 아래쪽 단계가 차례로 정리된다 — 의도된 동작."""
    lad = ladder()
    states = [
        stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100),
        stage(lad, 2, StageStatus.HOLDING, fill_price=9_500, fill_qty=105),
        stage(lad, 3, StageStatus.HOLDING, fill_price=9_000, fill_qty=111),
    ]
    # 목표가: 10,500 / 9,980 / 9,450 — 9,980 에서는 2·3단계만 충족
    decisions = run(9_980, states, lad)
    assert [d.stage_no for d in decisions] == [2, 3]


def test_all_stages_sell_at_high_price():
    lad = ladder()
    states = [
        stage(lad, 1, StageStatus.HOLDING, fill_price=10_000, fill_qty=100),
        stage(lad, 2, StageStatus.HOLDING, fill_price=9_500, fill_qty=105),
    ]
    assert [d.stage_no for d in run(11_000, states, lad)] == [1, 2]


@pytest.mark.parametrize(
    "status", [StageStatus.SELL_PENDING, StageStatus.BUY_PENDING, StageStatus.SOLD]
)
def test_rule5_excludes_non_holding_from_sell(status: StageStatus):
    """규칙 5: SELL_PENDING 은 이미 주문이 나갔으므로 중복 발주하지 않는다."""
    lad = ladder()
    states = [stage(lad, 1, status, fill_price=10_000, fill_qty=100)]
    assert run(11_000, states, lad) == []


def test_sell_reason_records_basis():
    lad = ladder()
    states = [stage(lad, 3, StageStatus.HOLDING, fill_price=8_950, fill_qty=111)]
    reason = run(9_400, states, lad)[0].reason
    assert "stage=3 SELL" in reason
    assert "tick=9400(WS)" in reason
    assert "target=9400" in reason
    assert "fill=8950" in reason
    assert "target_pct=5%" in reason
    assert "rule1_sell_first" in reason
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_rules_sell.py -v`
Expected: FAIL — `test_sells_when_target_reached` 등에서 `assert len([]) == 1`

- [ ] **Step 3: `rules.py` 에 매도 평가 추가**

`decide()` 본문의 마지막 두 줄을 다음으로 교체한다.

```python
    # 규칙 1 — 매도를 먼저 평가한다. 매도가 하나라도 있으면 이 틱에서는
    # 매도만 집행하고, 매수는 다음 틱에 평가한다. 매도 대금이 들어온 뒤
    # 매수하도록 하려는 것이며, 틱 간격이 1초 미만이라 실질 지연은 없다.
    sells = _eval_sells(tick, states, params)
    if sells:
        return list(sells)

    buy = _eval_buy(tick, cycle.ladder, states, params, now)
    return [buy] if buy is not None else []
```

파일 하단에 다음을 추가한다.

```python
def _eval_sells(
    tick: Tick, states: Sequence[StageState], params: TriggerParams
) -> list[SellStage]:
    """목표가에 도달한 보유 단계 전부. 번호가 낮은 순.

    매수와 달리 개수를 제한하지 않는다. 매도는 포지션을 줄이는 방향이므로
    과다 집행 위험이 없고, 반등 구간에서 여러 단계가 동시에 목표에 닿는 것은
    세븐스플릿의 의도된 동작이다.
    """
    out: list[SellStage] = []
    for state in sorted(states, key=lambda s: s.stage_no):
        # 규칙 5 — SELL_PENDING 은 이미 주문이 나갔다.
        if state.status is not StageStatus.HOLDING:
            continue
        if state.fill_price is None or not state.fill_qty:
            continue
        target = target_price(state.fill_price, params.target_pct)
        if tick.price < target:
            continue
        out.append(
            SellStage(
                stage_no=state.stage_no,
                # 목표가로 지정가 발주한다. 지정가 매도는 시장의 최우선
                # 매수호가에 체결되므로, 목표가로 걸어도 현재가가 더 높으면
                # 더 좋은 가격에 체결된다. 목표 보장과 체결 확률을 동시에 얻는다.
                limit_price=target,
                qty=state.fill_qty,
                reason=_sell_reason(state=state, tick=tick, target=target,
                                    params=params),
            )
        )
    return out


def _sell_reason(
    *, state: StageState, tick: Tick, target: int, params: TriggerParams
) -> str:
    return (
        f"stage={state.stage_no} SELL | tick={tick.price}({tick.source.value}) "
        f">= target={target} | fill={state.fill_price} "
        f"target_pct={_pct(params.target_pct)}% | rule1_sell_first"
    )
```

import 문에 `target_price` 를 추가한다.

```python
from autotrading7s.domain.ladder import Ladder, target_price
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_rules_sell.py tests/domain/test_rules_buy.py -v`
Expected: PASS (매도 11 + 매수 13 = 24 tests). 태스크 7의 테스트가 하나도 깨지지 않아야 한다.

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/rules.py tests/domain/test_rules_sell.py
git commit -m "feat: 매도 트리거 판정과 우선순위 추가 (규칙 1)

설계서 5절 규칙 1. 매도를 매수보다 먼저 평가하고, 매도가 하나라도 있으면 그
틱에서는 매도만 집행한다. 설계서가 '매도 먼저 집행'으로만 적어 모호했던 부분을
확정한 것으로, 매도 대금이 들어온 뒤 매수하도록 하려는 규칙 1의 취지에 맞다.

매도 지정가는 목표가로 발주한다. 지정가 매도는 시장 최우선 매수호가에
체결되므로 목표가로 걸어도 현재가가 더 높으면 더 좋은 가격에 체결된다."
```

---

### Task 9: 트리거 판정 — 재매수 쿨다운 (규칙 3)

**Files:**
- Modify: `src/autotrading7s/domain/rules.py`
- Test: `tests/domain/test_rules_rebuy.py`

**Interfaces:**
- Consumes: `TriggerParams.allow_rebuy`, `TriggerParams.rebuy_cooldown_sec`, `StageState.last_sold_at`
- Produces: `_eval_buy` 가 쿨다운을 반영. 공개 시그니처 변경 없음

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_rules_rebuy.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.domain.cycle import Cycle, confirm_anchor, start
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.rules import BuyStage, TriggerParams, decide
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def ladder() -> Ladder:
    return Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def running_cycle(lad: Ladder) -> Cycle:
    idle = Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE)
    return confirm_anchor(start(idle, at=T0), anchor_price=lad.anchor_price,
                          ladder=lad, at=T0)


def sold_then_waiting(lad: Ladder, no: int, sold_at: datetime) -> StageState:
    """매도 후 대기로 복귀한 단계 (재매수 대상)."""
    return StageState(stage_no=no, status=StageStatus.WAITING,
                      trigger_price=lad.trigger_price(no),
                      planned_qty=lad.planned_qty(no),
                      last_sold_at=sold_at, rebuy_count=1)


def run(price: int, states, lad: Ladder, params: TriggerParams, now: datetime):
    return decide(tick=Tick(code="005930", price=price, at=now, source=TickSource.WS),
                  cycle=running_cycle(lad), states=states, params=params,
                  now=now, market_open=True, stock_code="005930")


@pytest.mark.parametrize(
    ("elapsed_sec", "expect_buy"),
    [(0, False), (30, False), (59, False), (60, True), (61, True), (600, True)],
)
def test_rule3_cooldown_boundary(elapsed_sec: int, expect_buy: bool):
    """규칙 3: 매도 체결 후 60초가 지나야 재매수한다."""
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    now = T0 + timedelta(seconds=elapsed_sec)
    decisions = run(9_500, states, lad, params, now)
    assert bool(decisions) is expect_buy
    if expect_buy:
        assert isinstance(decisions[0], BuyStage)
        assert decisions[0].stage_no == 2


def test_custom_cooldown_is_honored():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=300)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    assert run(9_500, states, lad, params, T0 + timedelta(seconds=299)) == []
    assert run(9_500, states, lad, params, T0 + timedelta(seconds=300)) != []


def test_zero_cooldown_allows_immediate_rebuy():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=0)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    assert run(9_500, states, lad, params, T0) != []


def test_allow_rebuy_false_blocks_rebuy():
    """설정이 재매수를 막으면 대기 상태여도 매수하지 않는다.

    정상 흐름에서는 재매수 불허 단계가 SOLD 로 끝나므로 WAITING 으로 오지
    않지만, 사용자가 사이클 중간에 설정을 바꿀 수 있어 방어가 필요하다.
    """
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=False, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    assert run(9_500, states, lad, params, T0 + timedelta(hours=1)) == []


def test_first_buy_is_not_affected_by_cooldown():
    """최초 매수(last_sold_at 없음)는 쿨다운과 무관하다."""
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=3600)
    states = [
        StageState(stage_no=2, status=StageStatus.WAITING,
                   trigger_price=lad.trigger_price(2), planned_qty=lad.planned_qty(2))
    ]
    assert run(9_500, states, lad, params, T0) != []


def test_cooldown_skips_to_next_eligible_stage():
    """쿨다운 중인 단계는 건너뛰고 다음 조건 충족 단계를 본다."""
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [
        sold_then_waiting(lad, 2, sold_at=T0),   # 쿨다운 중
        StageState(stage_no=3, status=StageStatus.WAITING,
                   trigger_price=lad.trigger_price(3), planned_qty=lad.planned_qty(3)),
    ]
    decisions = run(9_000, states, lad, params, T0 + timedelta(seconds=10))
    assert [d.stage_no for d in decisions] == [3]


def test_rebuy_reason_marks_rebuy():
    lad = ladder()
    params = TriggerParams(target_pct=FIVE, allow_rebuy=True, rebuy_cooldown_sec=60)
    states = [sold_then_waiting(lad, 2, sold_at=T0)]
    reason = run(9_500, states, lad, params, T0 + timedelta(seconds=90))[0].reason
    assert "rebuy=1" in reason
    assert "cooldown_ok" in reason
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_rules_rebuy.py -v`
Expected: FAIL — 쿨다운 미구현이므로 `elapsed 0/30/59` 케이스와 `allow_rebuy=False` 케이스가 실패

- [ ] **Step 3: `_eval_buy` 에 쿨다운 검사 추가**

`_eval_buy` 의 `if tick.price > state.trigger_price: continue` **앞**에 다음을 삽입한다.

```python
        # 규칙 3 — 재매수 쿨다운. last_sold_at 이 있으면 한 번 팔린 단계다.
        # 쿨다운이 없으면 같은 단계가 수수료를 태우며 분당 수십 번 회전한다.
        if state.last_sold_at is not None:
            if not params.allow_rebuy:
                continue
            elapsed = (now - state.last_sold_at).total_seconds()
            if elapsed < params.rebuy_cooldown_sec:
                continue
```

그리고 `_buy_reason` 을 재매수 정보를 담도록 교체한다.

```python
def _buy_reason(*, stage_no: int, tick: Tick, trigger: int, ladder: Ladder,
                state: StageState) -> str:
    parts = [
        f"stage={stage_no} BUY",
        f"tick={tick.price}({tick.source.value}) <= trigger={trigger}",
        f"anchor={ladder.anchor_price} drop={_pct(ladder.drop_pct)}% "
        f"stage_gap={stage_no - 1}",
        "rule2_sequential",
    ]
    if state.last_sold_at is not None:
        parts.append(f"rebuy={state.rebuy_count} cooldown_ok")
    return " | ".join(parts)
```

`_eval_buy` 의 호출부도 `state=state` 를 넘기도록 수정한다.

```python
            reason=_buy_reason(stage_no=stage_no, tick=tick,
                               trigger=state.trigger_price, ladder=ladder,
                               state=state),
```

- [ ] **Step 4: 테스트 통과 확인 (기존 테스트 회귀 확인 포함)**

Run: `python -m pytest tests/domain/ -v`
Expected: PASS — 태스크 7·8의 테스트가 하나도 깨지지 않아야 한다. 특히 `test_reason_records_trigger_basis` 는 `rebuy` 가 없는 경로를 검사하므로 계속 통과해야 한다.

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/rules.py tests/domain/test_rules_rebuy.py
git commit -m "feat: 재매수 쿨다운 추가 (규칙 3)

설계서 5절 규칙 3. 매도 체결 후 기본 60초가 지나야 같은 단계를 재매수한다.
쿨다운이 없으면 갭하락으로 가격이 한 자리에 머무는 동안 같은 단계가 목표가와
발동가 사이를 왕복하며 수수료만 태운다.

allow_rebuy=False 검사도 함께 둔다. 정상 흐름에서는 재매수 불허 단계가 SOLD로
끝나 WAITING으로 오지 않지만, 사용자가 사이클 중간에 설정을 바꿀 수 있다."
```

---

### Task 10: 안전장치

**Files:**
- Create: `src/autotrading7s/domain/guards.py`
- Test: `tests/domain/test_guards.py`

**Interfaces:**
- Consumes: `BuyStage`, `SellStage` (태스크 7·8)
- Produces:
  - `GuardContext(stock_invested: int, stock_limit: int, total_invested: int, total_limit: int, orders_last_minute: int, max_orders_per_minute: int = 10)` — frozen
  - `GuardVerdict(allowed: bool, reason: str)` — frozen
  - `check_buy(decision: BuyStage, ctx: GuardContext) -> GuardVerdict`
  - `check_sell(decision: SellStage, ctx: GuardContext) -> GuardVerdict`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/domain/test_guards.py`**

```python
from __future__ import annotations

import pytest

from autotrading7s.domain.guards import GuardContext, check_buy, check_sell
from autotrading7s.domain.rules import BuyStage, SellStage


def buy(price: int = 9_500, qty: int = 105) -> BuyStage:
    return BuyStage(stage_no=2, limit_price=price, qty=qty, reason="test")


def sell(price: int = 10_500, qty: int = 100) -> SellStage:
    return SellStage(stage_no=1, limit_price=price, qty=qty, reason="test")


def ctx(**over) -> GuardContext:
    kwargs = dict(
        stock_invested=0,
        stock_limit=7_000_000,
        total_invested=0,
        total_limit=21_000_000,
        orders_last_minute=0,
        max_orders_per_minute=10,
    )
    kwargs.update(over)
    return GuardContext(**kwargs)  # type: ignore[arg-type]


def test_allows_buy_within_limits():
    assert check_buy(buy(), ctx()).allowed is True


def test_stock_limit_exact_boundary_is_allowed():
    """한도와 정확히 같아지는 주문은 허용한다."""
    est = 9_500 * 105  # 997,500
    verdict = check_buy(buy(), ctx(stock_invested=7_000_000 - est))
    assert verdict.allowed is True


def test_stock_limit_exceeded_by_one_won_is_rejected():
    est = 9_500 * 105
    verdict = check_buy(buy(), ctx(stock_invested=7_000_000 - est + 1))
    assert verdict.allowed is False
    assert "종목 총한도" in verdict.reason


def test_total_limit_exceeded_is_rejected():
    est = 9_500 * 105
    verdict = check_buy(
        buy(), ctx(stock_limit=100_000_000, total_invested=21_000_000 - est + 1)
    )
    assert verdict.allowed is False
    assert "전체 총한도" in verdict.reason


def test_order_frequency_limit():
    assert check_buy(buy(), ctx(orders_last_minute=9)).allowed is True
    verdict = check_buy(buy(), ctx(orders_last_minute=10))
    assert verdict.allowed is False
    assert "주문 빈도" in verdict.reason


def test_sell_is_not_limited_by_investment_caps():
    """매도는 포지션을 줄이므로 총한도와 무관하다."""
    verdict = check_sell(sell(), ctx(stock_invested=999_999_999,
                                     total_invested=999_999_999))
    assert verdict.allowed is True


def test_sell_is_limited_by_order_frequency():
    verdict = check_sell(sell(), ctx(orders_last_minute=10))
    assert verdict.allowed is False
    assert "주문 빈도" in verdict.reason


def test_verdict_reason_is_always_present():
    """감사 추적성 — 허용된 경우에도 근거를 남긴다 (설계서 6절)."""
    assert check_buy(buy(), ctx()).reason != ""
    assert check_sell(sell(), ctx()).reason != ""


def test_reason_records_limit_usage():
    reason = check_buy(buy(), ctx(stock_invested=1_200_000)).reason
    assert "1200000" in reason.replace(",", "")
    assert "7000000" in reason.replace(",", "")


@pytest.mark.parametrize("bad", [-1, -1_000])
def test_rejects_negative_context(bad: int):
    with pytest.raises(ValueError):
        ctx(stock_invested=bad)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/domain/test_guards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.domain.guards'`

- [ ] **Step 3: `src/autotrading7s/domain/guards.py` 구현**

```python
"""안전장치 — 설계서 6절.

설계서 7절 2항의 "무한 물타기 리스크"에 대한 코드 레벨 대응이다. 세븐스플릿은
손절매를 하지 않으므로, 프로그램이 제한할 수 있는 것은 투입 총액뿐이다.

한도는 **실체결금액 누적** 기준이다. 계획금액으로 세면 floor(금액/가격) 오차
때문에 한도가 실제보다 헐거워진다(설계서 3.1절).
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrading7s.domain.rules import BuyStage, SellStage


@dataclass(frozen=True, slots=True)
class GuardContext:
    stock_invested: int          # 이 종목의 누적 실체결금액
    stock_limit: int             # split_config.total_limit
    total_invested: int          # 전 종목 누적 실체결금액
    total_limit: int             # 전체 한도
    orders_last_minute: int
    max_orders_per_minute: int = 10

    def __post_init__(self) -> None:
        for name in (
            "stock_invested", "stock_limit", "total_invested", "total_limit",
            "orders_last_minute", "max_orders_per_minute",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative: {getattr(self, name)}")


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    allowed: bool
    reason: str


def check_buy(decision: BuyStage, ctx: GuardContext) -> GuardVerdict:
    """매수 허용 여부.

    예상 체결금액은 지정가 × 수량으로 계산한다. 실제 체결가는 지정가 이하이므로
    이 추정은 보수적이다 — 한도를 넘길 위험이 없는 쪽으로 어긋난다.
    """
    if ctx.orders_last_minute >= ctx.max_orders_per_minute:
        return GuardVerdict(
            False,
            f"주문 빈도 제한 초과: {ctx.orders_last_minute}/"
            f"{ctx.max_orders_per_minute}건/분",
        )

    estimate = decision.limit_price * decision.qty

    if ctx.stock_invested + estimate > ctx.stock_limit:
        return GuardVerdict(
            False,
            f"종목 총한도 초과: 누적 {ctx.stock_invested:,} + 예상 {estimate:,} "
            f"> 한도 {ctx.stock_limit:,}",
        )

    if ctx.total_invested + estimate > ctx.total_limit:
        return GuardVerdict(
            False,
            f"전체 총한도 초과: 누적 {ctx.total_invested:,} + 예상 {estimate:,} "
            f"> 한도 {ctx.total_limit:,}",
        )

    return GuardVerdict(
        True,
        f"guard_ok stage={decision.stage_no} est={estimate:,} "
        f"stock={ctx.stock_invested:,}/{ctx.stock_limit:,} "
        f"total={ctx.total_invested:,}/{ctx.total_limit:,}",
    )


def check_sell(decision: SellStage, ctx: GuardContext) -> GuardVerdict:
    """매도 허용 여부. 포지션을 줄이는 방향이므로 투입 한도와 무관하다."""
    if ctx.orders_last_minute >= ctx.max_orders_per_minute:
        return GuardVerdict(
            False,
            f"주문 빈도 제한 초과: {ctx.orders_last_minute}/"
            f"{ctx.max_orders_per_minute}건/분",
        )
    return GuardVerdict(
        True, f"guard_ok stage={decision.stage_no} SELL qty={decision.qty}"
    )
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/domain/test_guards.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/domain/guards.py tests/domain/test_guards.py
git commit -m "feat: 총한도·주문빈도 안전장치 추가

설계서 6절. 한도는 계획금액이 아니라 실체결금액 누적 기준이다. floor(금액/가격)
때문에 단계별 실토입이 993,440~999,600원으로 흔들리므로, 계획 기준으로 세면
한도가 실제보다 헐거워진다.

예상 체결금액은 지정가 × 수량으로 계산한다. 실제 체결가는 지정가 이하이므로
이 추정은 한도를 넘기지 않는 쪽으로 보수적이다.

매도는 포지션을 줄이는 방향이라 투입 한도 검사에서 제외하고 빈도 제한만 적용한다."
```

---

### Task 11: 시계 포트와 G1 게이트 통과

**Files:**
- Create: `src/autotrading7s/ports/clock.py`
- Create: `src/autotrading7s/adapters/fake/clock.py`
- Create: `README.md`
- Test: `tests/adapters/test_fake_clock.py`
- Test: `tests/test_g1_gate.py`

**Interfaces:**
- Consumes: 전 태스크
- Produces:
  - `ClockPort` Protocol — `now() -> datetime`, `is_market_open(at: datetime | None = None) -> bool`
  - `FakeClock(current: datetime, market_open: bool = True)` — `advance(seconds: float)`, `set_market_open(bool)`

- [ ] **Step 1: 실패하는 테스트 작성 — `tests/adapters/test_fake_clock.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.ports.clock import ClockPort

T0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)


def test_fake_clock_satisfies_port():
    clock: ClockPort = FakeClock(current=T0)
    assert clock.now() == T0
    assert clock.is_market_open() is True


def test_advance_moves_time_forward():
    clock = FakeClock(current=T0)
    clock.advance(90)
    assert clock.now() == T0 + timedelta(seconds=90)


def test_market_open_can_be_toggled():
    clock = FakeClock(current=T0)
    clock.set_market_open(False)
    assert clock.is_market_open() is False
    clock.set_market_open(True)
    assert clock.is_market_open() is True


def test_advance_accepts_fractional_seconds():
    clock = FakeClock(current=T0)
    clock.advance(0.5)
    assert clock.now() == T0 + timedelta(milliseconds=500)
```

`mkdir -p tests/adapters && touch tests/adapters/__init__.py` 를 먼저 실행한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/adapters/test_fake_clock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.ports.clock'`

- [ ] **Step 3: 포트와 Fake 구현**

`src/autotrading7s/ports/clock.py`:

```python
"""시계 포트 — 설계서 5절 규칙 4, 7.2절.

시간을 주입 가능하게 만드는 이유는 "15:29에 갭하락이 오면?" 같은 시나리오를
테스트에서 재현하기 위해서다. 실제 장 운영시간·휴장일 판단 방법은 설계서
18.2절에 따라 구현 2단계에서 확정하며, 그때 KiwoomClock 이 이 포트를 구현한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...

    def is_market_open(self, at: datetime | None = None) -> bool:
        """``at`` (기본값: 현재) 이 정규장 운영시간 안인가."""
        ...
```

`src/autotrading7s/adapters/fake/clock.py`:

```python
"""테스트용 시계 — 시간과 장 운영 여부를 명시적으로 조작한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class FakeClock:
    current: datetime
    market_open: bool = field(default=True)

    def now(self) -> datetime:
        return self.current

    def is_market_open(self, at: datetime | None = None) -> bool:
        return self.market_open

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)

    def set_market_open(self, value: bool) -> None:
        self.market_open = value
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/adapters/test_fake_clock.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: G1 종합 시나리오 테스트 작성 — `tests/test_g1_gate.py`**

한 사이클을 판정 함수만으로 끝까지 돌려, 개별 단위 테스트가 놓치는 조합 문제를 잡는다.

```python
"""G1 게이트 — 도메인 코어만으로 한 사이클을 끝까지 돌린다.

브로커도 DB도 없이 decide() 와 상태 전이 함수만으로 진행한다. 개별 단위
테스트가 통과해도 조합에서 어긋나는 문제를 잡기 위한 시나리오 테스트다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.domain.cycle import (
    Cycle,
    close,
    confirm_anchor,
    is_cycle_complete,
    start,
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
    StageStatus,
    Tick,
    TickSource,
)

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FIVE = Decimal("0.05")


def _ladder() -> Ladder:
    return Ladder(anchor_price=10_000, drop_pct=FIVE, target_pct=FIVE,
                  max_stages=7, amount_per_stage=1_000_000)


def _initial_states(lad: Ladder) -> list[StageState]:
    return [
        StageState(stage_no=n, status=StageStatus.WAITING,
                   trigger_price=lad.trigger_price(n), planned_qty=lad.planned_qty(n))
        for n in range(1, lad.max_stages + 1)
    ]


def test_full_cycle_down_then_up_closes_at_zero_holdings():
    lad = _ladder()
    clock = FakeClock(current=T0)
    params = TriggerParams(target_pct=FIVE, allow_rebuy=False, rebuy_cooldown_sec=60)
    states = _initial_states(lad)

    # 1단계는 사이클 시작 시 체결되어 앵커를 확정한다.
    states[0] = to_holding(to_buy_pending(states[0]), fill_price=10_000,
                           fill_qty=lad.planned_qty(1), at=clock.now())
    cycle = confirm_anchor(
        start(Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE),
              at=clock.now()),
        anchor_price=10_000, ladder=lad, at=clock.now(),
    )
    assert cycle.status is CycleStatus.RUNNING

    orders = 0

    def step(price: int) -> list[BuyStage | SellStage]:
        nonlocal orders, states
        decisions = decide(
            tick=Tick(code="005930", price=price, at=clock.now(),
                      source=TickSource.WS),
            cycle=cycle, states=states, params=params,
            now=clock.now(), market_open=clock.is_market_open(),
            stock_code="005930",
        )
        for d in decisions:
            ctx = GuardContext(
                stock_invested=invested_amount(states), stock_limit=7_000_000,
                total_invested=invested_amount(states), total_limit=21_000_000,
                orders_last_minute=0,
            )
            idx = d.stage_no - 1
            if isinstance(d, BuyStage):
                assert check_buy(d, ctx).allowed
                states[idx] = to_holding(
                    to_buy_pending(states[idx]), fill_price=d.limit_price,
                    fill_qty=d.qty, at=clock.now(),
                )
            else:
                assert check_sell(d, ctx).allowed
                states[idx] = after_sell(
                    to_sell_pending(states[idx]), at=clock.now(),
                    allow_rebuy=params.allow_rebuy,
                )
            orders += 1
            clock.advance(1)
        return decisions

    # 하락 — 2~4단계가 순차로 채워진다.
    for price in (9_500, 9_000, 8_500):
        assert [d.stage_no for d in step(price)] != []

    assert [s.status for s in states[:4]] == [StageStatus.HOLDING] * 4
    assert held_qty(states) == sum(lad.planned_qty(n) for n in range(1, 5))

    # 반등 — 낮은 단계가 먼저 정리된다.
    sold_order: list[int] = []
    # 목표가: 4단계 8,930 / 3단계 9,450 / 2단계 9,980 / 1단계 10,500
    # (8,500 × 1.05 = 8,925 는 10원 배수가 아니므로 올림하여 8,930)
    for price in (8_930, 9_450, 9_980, 10_500):
        for d in step(price):
            assert isinstance(d, SellStage)
            sold_order.append(d.stage_no)

    assert sold_order == [4, 3, 2, 1], "체결가가 낮은 단계가 먼저 목표에 닿는다"
    assert held_qty(states) == 0
    assert is_cycle_complete(states) is True

    closed = close(cycle, reason=CloseReason.NORMAL, at=clock.now(), states=states)
    assert closed.status is CycleStatus.CLOSED
    assert closed.close_reason is CloseReason.NORMAL
    assert orders == 7, "매수 3건 + 매도 4건"


def test_no_activity_outside_market_hours():
    lad = _ladder()
    clock = FakeClock(current=T0)
    clock.set_market_open(False)
    states = _initial_states(lad)
    states[0] = to_holding(to_buy_pending(states[0]), fill_price=10_000,
                           fill_qty=100, at=T0)
    cycle = confirm_anchor(
        start(Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE), at=T0),
        anchor_price=10_000, ladder=lad, at=T0,
    )
    for price in (9_500, 8_000, 12_000):
        assert decide(
            tick=Tick(code="005930", price=price, at=clock.now(),
                      source=TickSource.WS),
            cycle=cycle, states=states, params=TriggerParams(target_pct=FIVE),
            now=clock.now(), market_open=clock.is_market_open(),
            stock_code="005930",
        ) == []


def test_total_limit_stops_further_buys():
    """한도에 걸리면 판정은 나오지만 guard 가 막는다."""
    lad = _ladder()
    states = _initial_states(lad)
    states[0] = to_holding(to_buy_pending(states[0]), fill_price=10_000,
                           fill_qty=100, at=T0)
    cycle = confirm_anchor(
        start(Cycle(cycle_id=1, config_id=1, seq=1, status=CycleStatus.IDLE), at=T0),
        anchor_price=10_000, ladder=lad, at=T0,
    )
    decisions = decide(
        tick=Tick(code="005930", price=9_500, at=T0, source=TickSource.WS),
        cycle=cycle, states=states, params=TriggerParams(target_pct=FIVE),
        now=T0, market_open=True, stock_code="005930",
    )
    assert len(decisions) == 1
    ctx = GuardContext(stock_invested=6_900_000, stock_limit=7_000_000,
                       total_invested=6_900_000, total_limit=21_000_000,
                       orders_last_minute=0)
    verdict = check_buy(decisions[0], ctx)  # type: ignore[arg-type]
    assert verdict.allowed is False
    assert "종목 총한도" in verdict.reason


def test_domain_imports_nothing_external():
    """설계서 7.2절 의존 규칙 — domain 은 표준 라이브러리만 쓴다."""
    import ast
    import pathlib
    import sys

    stdlib = set(sys.stdlib_module_names)
    domain_dir = pathlib.Path(__file__).parent.parent / "src" / "autotrading7s" / "domain"
    offenders: list[str] = []

    for path in domain_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                if root and root not in stdlib and root != "autotrading7s":
                    offenders.append(f"{path.name}: {name}")

    assert offenders == [], f"domain 이 외부 모듈을 import 한다: {offenders}"
```

**시나리오 검증 근거** — 반등 구간의 매도 순서:

| 단계 | 체결가 | 계산값 (×1.05) | 목표가 (호가 올림) | 매도 발동 틱 |
|---:|---:|---:|---:|---:|
| 4 | 8,500 | 8,925 | **8,930** | 8,930 |
| 3 | 9,000 | 9,450 | 9,450 | 9,450 |
| 2 | 9,500 | 9,975 | **9,980** | 9,980 |
| 1 | 10,000 | 10,500 | 10,500 | 10,500 |

계산값이 10원 배수가 아닌 4·2단계는 올림 때문에 목표가가 5원 올라간다. 틱 시퀀스를 각 단계의 목표가에 정확히 맞춰 두었으므로 매 틱에 한 단계씩만 매도되며, `sold_order` 는 `[4, 3, 2, 1]` 이 된다.

이 표는 호가 단위 정규화가 왜 목표가 계산에 반드시 들어가야 하는지 보여준다. 정규화가 없으면 8,925원에 매도 주문이 나가고 거부된다.

> **Task 5 실행 중 확정된 계약 변경** (계획서 작성 시점에는 없었음):
> - `close(cycle, *, reason, at, states)` — `states` 가 **필수** 키워드 인자다.
>   보유가 남아 있거나 미체결 주문이 있으면 `ValueError` 로 거부한다. 안전장치를
>   선택 인자로 두면 기본값이 "검사 없음"이 되므로 필수로 했다.
> - `is_cycle_complete(states)` 는 빈 시퀀스에 `ValueError` 를 던진다. 사이클은
>   항상 `max_stages` 개의 단계를 가지므로 빈 목록은 데이터 정합성 실패다.
> - `Cycle.__post_init__` 이 `RUNNING`·`PAUSED` 에서 `anchor_price` 와 `ladder` 를
>   요구한다. `LIQUIDATING` 은 맨몸 생성을 허용하되 앵커가 있으면 사다리 일치를
>   검사한다(긴급청산이 `STARTING` 에서도 시작될 수 있으므로).
> - `StageState.__post_init__` 이 `HOLDING`·`SELL_PENDING` 에서 `fill_price` 와
>   `fill_qty` 를 요구한다.
> - `_ALLOWED[STARTING]` 에 `LIQUIDATING` 이 포함된다(설계서 4.2절 "어느
>   상태에서든"). `LIQUIDATING` 에서 나가는 경로는 `CLOSED` 하나뿐이다 — 청산
>   의도가 보존되어야 하므로 일방향 래칫이다.

- [ ] **Step 6: G1 게이트 실행 — 전체 테스트와 커버리지**

Run:
```bash
python -m pytest tests/ -v
python -m pytest tests/ --cov=autotrading7s.domain --cov-report=term-missing
```

Expected:
- 전체 PASS
- `autotrading7s.domain` 커버리지 **95% 이상**. 미달이면 빠진 분기에 테스트를 추가한다.
- `test_domain_imports_nothing_external` PASS — 의존 규칙이 자동 검증된다.

- [ ] **Step 7: `README.md` 작성**

```markdown
# AutoTrading 7s

키움증권 REST API 기반 세븐스플릿(7-Split) 자동투자 프로그램.

- 설계서: `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`
- 구현 계획: `docs/superpowers/plans/`

## 현재 상태

**Plan 1 (도메인 코어, G1) 완료.** 사다리 계산·호가 단위·상태기계·트리거 판정·
안전장치가 구현되어 있으며, 네트워크·DB·GUI 없이 전부 테스트로 검증된다.

미구현: 영속성(Plan 2), 키움 어댑터(Plan 3), GUI(Plan 4).

## 개발

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest tests/ -v
python -m pytest tests/ --cov=autotrading7s.domain --cov-report=term-missing
```

## 설계 원칙

- `domain/` 은 표준 라이브러리 외 어떤 것도 import 하지 않는다. 테스트로 강제한다.
- 금액·가격은 원 단위 `int`, 비율만 `Decimal`. `float` 는 금지한다.
- 자동 트리거 경로는 시장가를 표현할 수 없다. 시장가는 긴급청산 전용이다.
- 주문 요청 타입에 신용·미수 필드가 존재하지 않는다.
- `decide()` 에 하락 조건 매도 분기가 없다. 자동 손절매는 전략 원칙상 배제한다.
```

- [ ] **Step 8: 커밋**

```bash
git add src/autotrading7s/ports/clock.py src/autotrading7s/adapters/fake/clock.py \
        tests/adapters/ tests/test_g1_gate.py README.md
git commit -m "feat: 시계 포트와 G1 게이트 시나리오 테스트 추가

ClockPort로 시간을 주입 가능하게 만들어 장 운영시간 시나리오를 테스트에서
재현한다. 실제 장 시간·휴장일 판단은 설계서 18.2절에 따라 구현 2단계에서
확정한다.

G1 게이트 테스트는 브로커도 DB도 없이 판정 함수와 상태 전이만으로 한 사이클을
끝까지 돌려, 개별 단위 테스트가 놓치는 조합 문제를 잡는다.

domain이 외부 모듈을 import하지 않는지 AST로 검사하는 테스트를 추가했다.
설계서 7.2절 의존 규칙이 문서 약속에서 자동 검증으로 바뀐다."
```

---

## G1 게이트 통과 기준 (설계서 15.2절)

Plan 1 완료 시 다음이 모두 통과해야 한다.

- [ ] 사다리 계산 — 앵커·발동가·수량·누적투입 (설계서 3.1절 예시 표 고정)
- [ ] 호가 단위 정규화 — 구간 경계값, 매수 내림·매도 올림
- [ ] 규칙 1 — 매도 우선 평가
- [ ] 규칙 2 — 갭하락 순차 매수
- [ ] 규칙 3 — 재매수 쿨다운 경계값 (59/60/61초)
- [ ] 규칙 4 — 장외 무동작
- [ ] 규칙 5 — PENDING 제외
- [ ] 단계 상태 전이 전수 + 불법 전이 거부
- [ ] 사이클 상태 전이 전수 + 불법 전이 거부
- [ ] 사이클 안전장치 — 앵커·사다리 불변식, 빈 단계 목록 거부, 보유 중 종료 거부, 긴급청산이 STARTING 에서도 가능, LIQUIDATING 일방향 래칫
- [ ] 사다리 설정 검증 — 마지막 단계 원시 발동가 1원 하한(양방향 경계), total_drop == 1 경계
- [ ] guards 전항목 — 한도 경계값, 1주 미달, 빈도 제한
- [ ] 평가·실현손익 계산 (설계서 14.1절 목업 수치 고정)
- [ ] `domain/` 의존 규칙 자동 검증
- [ ] `autotrading7s.domain` 커버리지 95% 이상

---

## Plan 1 이후

G1 통과 후 **Plan 2 (영속성 + 시뮬레이션 엔진, G2)** 로 진행한다. 범위는 다음과 같다.

- `ports/broker.py`, `ports/repository.py`
- `adapters/sqlite/` — 설계서 12절 스키마, 마이그레이션, `holdings` 뷰
- `adapters/fake/broker.py` — 체결 모드(INSTANT/DELAYED/PARTIAL/NEVER), 실패 모드(TIMEOUT/REJECT/DISCONNECT)
- `engine/executor.py` — 설계서 9절 주문 실행 파이프라인, UNKNOWN 분기
- `engine/orchestrator.py` — asyncio 태스크, `priority_q` 우선 소비
- `engine/reconciler.py`, `engine/recovery.py`, `engine/emergency.py`
- `app/commands.py`, `app/events.py`, `app/engine_thread.py`
- `cli.py` — headless 기동
- 설계서 15.2절 G2 시나리오 12건 전부
