# AutoTrading 7s — GUI (Plan 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 설계서 14절의 화면을 만든다 — 보유현황, 단계별 상세, 설정 등록(사다리 미리보기), 긴급청산·강제 종료 다이얼로그, 로그 뷰.

**Architecture:** `ui/` 를 **순수 뷰모델**과 **얇은 Tkinter 셸**로 가른다. 뷰모델은 `tkinter` 를 import 하지 않고 EC2 에서 전수 테스트되며, 셸은 뷰모델이 만든 값을 위젯에 옮기는 일만 한다. GUI 는 `app/engine_thread.EngineThread` 하나만 접점으로 쓰고 DB 를 건드리지 않는다.

**Tech Stack:** Python 3.12, `tkinter`(표준 라이브러리, **EC2 에 없음**), `dataclasses`. 테스트는 `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`

**선행 기록:**
- `docs/superpowers/records/2026-09-02-plan2b-handover-to-3-and-4.md` — Plan 4 에 넘긴 10건
- `docs/superpowers/records/2026-09-02-plan2b-execution-ledger.md` — 룰링 17건

---

## 이 계획을 지배하는 사실: EC2 에 `tkinter` 가 없다

```
$ .venv/bin/python -c "import tkinter"
ModuleNotFoundError: No module named 'tkinter'
```

디스플레이가 없는 것을 넘어 **모듈 자체가 없다.** 그러므로 `tkinter` 를 import 하는 파일은 EC2 에서 **import 조차 되지 않고**, 그 안의 코드는 자동 검증이 영원히 닿지 않는 사각지대다 (설계서 18.1 리스크 7).

이것이 설계서 14.4절의 규칙에 구체적인 형태를 준다:

```
ui/view_model.py   순수 계산·서식. tkinter 를 import 하지 않는다.  → EC2 에서 전수 테스트
ui/presenter.py    이벤트 소비 상태기계. tkinter 를 import 하지 않는다. → EC2 에서 전수 테스트
ui/text_render.py  설계서 14.1·14.2 의 ASCII 표. tkinter 없음.     → EC2 에서 전수 테스트
ui/widgets/*.py    Tkinter 셸. domain·engine·ports·adapters 를 import 하지 않는다. → Windows 에서만 확인
```

**두 경계를 AST 로 못 박는다.** 뷰모델 쪽에 `tkinter` 가 들어오면 그 로직이 테스트 밖으로 나가고, 위젯 쪽에 `domain` 이 들어오면 계산이 사각지대로 들어간다. 둘 다 게이트 테스트가 거부한다.

**위젯 층은 이 계획의 산출물이지만 이 계획이 검증할 수 없다.** Task 12 가 Windows 수동 검증 체크리스트를 만들고, 그것이 G3(모의투자 검증)의 선행 절차가 된다. 이 계획의 완료는 "EC2 에서 검증 가능한 전부가 검증됨 + 위젯 코드가 존재함" 이며, "화면이 제대로 그려짐" 은 사용자가 Windows 에서 확인해야 한다. **그 사실을 완료 보고에 명시한다.**

---

## Global Constraints

Plan 1·2A·2B 의 제약을 전부 승계한다. 이 계획에서 실제로 위반될 수 있는 것과 새로 추가되는 것:

- **Python 3.12** 이상. `from __future__ import annotations` 를 모든 모듈의 docstring 직후 첫 import 로 둔다.
- 파이썬 실행은 반드시 **`.venv/bin/python`**. 테스트는 `.venv/bin/python -m pytest`.
- **`ui/view_model.py`·`ui/presenter.py`·`ui/text_render.py` 는 `tkinter` 를 import 하지 않는다.**
- **`ui/widgets/` 는 `domain`·`engine`·`ports`·`adapters` 를 import 하지 않는다.** `app` 과 `ui.view_model`·`ui.presenter` 만 쓴다.
- **`ui/` 는 DB 를 건드리지 않는다** (설계서 14.4절). `sqlite3` import 금지. 접점은 `app/engine_thread.EngineThread` 뿐이다.
- **표시용 계산조차 `domain/pnl.py`·`domain/ladder.py` 의 순수 함수를 호출한다** (설계서 14.4절). 뷰모델 안에서 평가손익률을 직접 계산하지 않는다.
- **금액·가격은 원 단위 `int`, 비율만 `Decimal`.** 서식 함수는 `int` 를 받아 `str` 을 낸다.
- **도메인의 모든 `datetime` 은 tz-aware.**
- 커밋 메시지는 한국어 본문 + Conventional Commits 접두어. `git add` 는 브리프가 지정한 경로만. **`git add -A` 금지.**
- 브랜치는 `feat/gui`.

### 승계하는 화면 규칙 (설계서 14절)

| # | 규칙 |
|---|---|
| 1 | 상태 표기는 `감시` / `소진` / `IDLE` / `일시정지` / `청산중` / `⚠불일치` 여섯 가지 |
| 2 | 상단 배너는 모의투자에서 `▣ 모의투자`, 실전에서 붉은 `▣ 실전투자` |
| 3 | "목표까지 / 매수까지" 한 열에 방향 기호로 두 의미를 담는다 (보유는 목표까지, 대기는 매수 발동까지) |
| 4 | 미리보기의 발동가는 내림, 목표가는 올림 (호가 단위 정규화) |
| 5 | 강제 종료 확인은 `강제종료`, 전체 청산 확인은 `전체청산` 텍스트 입력 |
| 6 | 보유현황 하단에 증권사 평균단가와의 차이를 명시 (설계서 2.1절) |

---

## 이 계획이 해소하는 핸드오버

| # | 출처 | 내용 | 해소 태스크 |
|---|---|---|---|
| 2B-1 | Plan 2B | 접점은 `EngineThread` 하나 | 8, 11 |
| 2B-2 | Plan 2B | `raise_if_failed()` 를 주기적으로 확인해야 한다 | 8, 11 |
| 2B-3 | Plan 2B | 이벤트가 화면에 필요한 것을 싣고 있다 / **`holdings()` 를 GUI 가 직접 부르지 않는다 — 그 API 를 Plan 4 가 정한다** | 1, 2 |
| 2B-4 | Plan 2B | `OrderUnknown` 과 `OrderRejected` 를 같은 색으로 그리면 안 된다 | 8, 11 |
| 2B-5 | Plan 2B | `CycleLoadFailed` 에 사용자 탈출구가 필요하다 | 8, 11 |
| 2B-6 | Plan 2B | 확인 문자열은 `강제종료`·`전체청산` 정확히 그 값 | 7, 11 |
| 2B-7 | Plan 2B | `GuardBlocked.reason` 은 도메인이 만든 문자열 — 그대로 표시 | 8 |
| 2B-8 | Plan 2B | `ResetReconcileBaseline` 의 UI 입구가 필요하다 | 11 |
| 2B-9 | Plan 2B | 사다리 미리보기는 엔진 없이 만들 수 있다 + 입력 검증은 Plan 4 의 몫 | 6, 3 |
| 2B-10 | Plan 2B | 사이클 상태와 설정 상태를 혼동하지 말 것 | 4 |

Plan 3(키움 어댑터)의 핸드오버는 이 계획의 범위 밖이다. 종목명 조회([조회] 버튼)는 브로커가 필요하므로 Plan 3 의 몫이며, 이 계획은 사용자가 직접 입력하게 한다 (`stock_name` 은 `None` 을 허용한다).

---

## File Structure

```
src/autotrading7s/
├── app/
│   ├── snapshot.py        Snapshot·ConfigSnapshot — 엔진이 GUI 에 밀어주는 상태
│   ├── commands.py        (수정) SaveConfig·UpdateConfig 추가
│   └── events.py          (수정) ConfigSaved·ConfigRejected 추가
├── engine/
│   └── orchestrator.py    (수정) 스냅샷 생성·발행, 설정 명령 처리
├── ports/repository.py    (수정) update_config
├── adapters/sqlite/
│   └── repository.py      (수정) update_config
└── ui/
    ├── __init__.py
    ├── view_model.py      순수 — 보유현황·단계상세·사다리미리보기·다이얼로그·상태바
    ├── presenter.py       순수 — 이벤트 소비 상태기계
    ├── text_render.py     순수 — 설계서 14.1·14.2 의 ASCII 표
    └── widgets/
        ├── __init__.py
        ├── main_window.py
        ├── holdings_table.py
        ├── stage_detail.py
        ├── config_dialog.py
        ├── emergency_dialog.py
        └── log_view.py
```

`view_model.py` 를 한 파일로 두는 기준: 전부 **입력이 스냅샷이고 출력이 표 한 개**인 순수 함수다. 호출자가 하나(프레젠터)이고 서로 자료구조를 공유하므로 쪼개면 import 만 늘어난다. 위젯은 반대로 화면 영역마다 파일을 나눈다 — Windows 에서 하나씩 확인하게 되므로 그 단위가 리뷰 단위다.

테스트:
```
tests/app/test_snapshot.py
tests/engine/test_snapshot_emission.py  test_config_commands.py
tests/adapters/test_repository_update_config.py
tests/ui/test_view_model_holdings.py  test_view_model_stages.py
        test_view_model_ladder_preview.py  test_view_model_dialogs.py
        test_presenter.py  test_text_render.py
tests/test_g4_prep_gate.py    (의존 방향 + 위젯 층 규칙)
```

---

## Task 1: 스냅샷 계약 (`app/snapshot.py`)

**배경 (2B 핸드오버 3).** GUI 가 상태를 얻는 경로가 없다. `holdings()` 뷰는 리포지토리에 있지만 GUI 는 DB 를 건드릴 수 없고, 그 규칙이 리포지토리의 단일 작성자 전제를 성립시킨다.

**Ruling: 엔진이 스냅샷을 `event_q` 로 **밀어준다** (요청-응답이 아니다).** 큐 계약이 한 방향으로 유지되어야 설계서 7.1절이 말한 "향후 프로세스 분리 시 큐를 소켓으로 교체" 가 성립한다. 요청-응답 채널은 상관 ID 와 블로킹이 필요한 **두 번째 프로토콜**이며, 그것을 도입하는 순간 그 교체가 단순한 작업이 아니게 된다. 틀렸을 경우 비용: GUI 가 원하는 시점에 조회할 수 없다 — 실제로는 200ms 마다 큐를 비우므로 차이가 없다.

**Ruling: `holdings()` 뷰로는 설계서 14.1절의 표를 그릴 수 없다.** 그 뷰는 `stage_state WHERE status IN ('HOLDING','SELL_PENDING')` 로 조인하므로 **보유 0 인 설정은 행을 만들지 못하고**(목업의 `NAVER 0/5 IDLE` 이 그런 행이다), `config_id` 도 없어서 명령을 보낼 대상을 알 수 없다. 스냅샷은 `list_configs()` + `load_active_cycles()` + `load_stages()` 로 만든다. 틀렸을 경우 비용: 없다 — 뷰는 그대로 남아 있고 다른 소비자가 쓸 수 있다.

**Ruling: `ConfigSnapshot.stages` 는 도메인 `StageState` 를 그대로 담는다.** 설계서 14.4절이 "표시용 계산조차 `domain/pnl.py` 의 순수 함수를 호출" 하라고 규정하고, 그 함수들이 `Sequence[StageState]` 를 받는다. 별도 DTO 로 옮기면 뷰모델이 `pnl` 을 쓸 수 없어 계산을 다시 구현하게 되고, **그것이 14.4절이 금지한 바로 그것이다.** `StageState`·`Ladder` 는 frozen 이므로 큐를 건너도 안전하다. 틀렸을 경우 비용: `app/` 이 `domain.stage`·`domain.ladder` 에 의존한다 — `app/events.py` 가 이미 `domain.types` 에 의존하므로 새 방향이 아니다.

**Ruling: 이벤트로만 알 수 있는 것은 스냅샷에 넣지 않는다.** 대사 판정(`⚠불일치`), 로드 실패, 시세 폴백 여부는 DB 에서 읽을 수 없고 이벤트로만 온다 — 프레젠터가 이벤트에서 누적한다. 스냅샷에도 넣으면 같은 사실의 출처가 둘이 되고, 어느 쪽이 최신인지 알 수 없다. 틀렸을 경우 비용: GUI 를 재시작하면 그 경고가 사라진다 — 이력은 `reconcile_log`·`emergency_liquidation_log` 에 남아 있고, 다음 대사(5분)가 같은 불일치를 다시 보고한다.

**Files:**
- Create: `src/autotrading7s/app/snapshot.py`
- Test: `tests/app/test_snapshot.py`

**Interfaces:**
- Produces: `ConfigSnapshot`, `Snapshot(Event)`, `Snapshot.core` (리비전 비교용)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/app/test_snapshot.py`:

```python
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autotrading7s.app.events import Event
from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus

AT = datetime(2026, 9, 2, 9, 42, tzinfo=UTC)
PCT = Decimal("0.05")


def _ladder(anchor=10_000, stages=7) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=PCT, target_pct=PCT,
                  max_stages=stages, amount_per_stage=1_000_000)


def _stages(ladder: Ladder, holding=()) -> tuple[StageState, ...]:
    out = []
    for n in range(1, ladder.max_stages + 1):
        st = StageState(stage_no=n, status=StageStatus.WAITING,
                        trigger_price=ladder.trigger_price(n),
                        planned_qty=ladder.planned_qty(n))
        if n in holding:
            st = dataclasses.replace(
                st, status=StageStatus.HOLDING,
                fill_price=ladder.trigger_price(n),
                fill_qty=ladder.planned_qty(n), bought_at=AT)
        out.append(st)
    return tuple(out)


def _config(**over) -> ConfigSnapshot:
    ladder = over.pop("ladder", _ladder())
    kw = dict(
        config_id=1, stock_code="005930", stock_name="삼성전자", label="기본",
        config_status="ACTIVE", max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=True, rebuy_cooldown_sec=60,
        stock_limit=7_000_000, cycle_id=2, cycle_seq=2,
        cycle_status=CycleStatus.RUNNING, anchor_price=10_000, ladder=ladder,
        cycle_started_at=AT, stages=_stages(ladder, holding=(1, 2, 3)),
        pending_orders=0,
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def test_snapshot_is_an_event_so_it_flows_on_the_event_queue():
    """스냅샷은 이벤트다 — 큐 계약이 한 방향으로 유지된다.

    요청-응답 채널을 만들면 상관 ID 와 블로킹이 필요한 두 번째 프로토콜이
    생기고, 설계서 7.1절이 말한 "큐를 소켓으로 교체" 가 단순한 작업이 아니게
    된다.
    """
    snap = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    assert isinstance(snap, Event)


def test_snapshot_and_config_snapshot_are_frozen():
    for cls in (Snapshot, ConfigSnapshot):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen, cls


def test_snapshot_rejects_a_naive_timestamp():
    with pytest.raises(ValueError, match="tz-aware"):
        Snapshot(configs=(), total_limit=1, at=datetime(2026, 9, 2, 9, 42))


def test_config_snapshot_carries_config_id_for_commands():
    """`holdings()` 뷰에는 config_id 가 없다 — 그래서 명령 대상을 알 수 없다.

    GUI 의 [시작]·[일시정지]·[긴급청산]이 모두 config_id 를 보내므로 표의 각
    행이 그것을 알아야 한다.
    """
    assert _config().config_id == 1


def test_config_snapshot_can_describe_an_idle_config_with_no_cycle():
    """설계서 14.1절 목업의 `NAVER 0/5 IDLE` 행.

    `holdings()` 뷰는 보유 단계가 없으면 행 자체를 만들지 않는다 — 그 행을
    그릴 수 있는 것이 스냅샷을 따로 두는 이유다.
    """
    idle = _config(config_status="IDLE", cycle_id=None, cycle_seq=None,
                   cycle_status=None, anchor_price=None, ladder=None,
                   cycle_started_at=None, stages=())
    assert idle.cycle_status is None
    assert idle.stages == ()


def test_stages_are_domain_objects_so_pnl_can_be_called_directly():
    """설계서 14.4절 — 표시용 계산조차 domain/pnl.py 를 호출한다.

    별도 DTO 로 옮기면 뷰모델이 pnl 을 쓸 수 없어 계산을 다시 구현하게 되고,
    그것이 14.4절이 금지한 바로 그것이다.
    """
    from autotrading7s.domain import pnl

    snap = _config()
    assert all(isinstance(s, StageState) for s in snap.stages)
    assert pnl.holding_stage_count(snap.stages) == 3
    assert pnl.held_qty(snap.stages) > 0


def test_core_ignores_the_timestamp_so_idle_ticks_emit_nothing():
    """리비전 비교는 `at` 을 무시해야 한다.

    포함하면 매 틱마다 스냅샷이 달라져서 유휴 구간에도 큐가 자란다. 시간
    기준으로 주기를 두는 대안은 FakeClock 이 멈춘 테스트에서 첫 스냅샷만
    나가게 만든다.
    """
    a = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    b = Snapshot(configs=(_config(),), total_limit=21_000_000,
                 at=AT.replace(hour=10))
    assert a != b                      # `at` 이 다르므로 객체는 다르다
    assert a.core == b.core             # 그러나 리비전은 같다


def test_core_changes_when_a_stage_fills():
    a = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    ladder = _ladder()
    b = Snapshot(configs=(_config(stages=_stages(ladder, holding=(1, 2, 3, 4))),),
                 total_limit=21_000_000, at=AT)
    assert a.core != b.core


def test_core_changes_when_the_total_limit_changes():
    a = Snapshot(configs=(_config(),), total_limit=21_000_000, at=AT)
    b = Snapshot(configs=(_config(),), total_limit=20_000_000, at=AT)
    assert a.core != b.core


def test_core_changes_when_pending_orders_change():
    """미체결 건수는 긴급청산 다이얼로그의 '함께 취소됩니다' 안내에 쓰인다.

    리비전에서 빠지면 그 숫자가 화면에서 갱신되지 않는다.
    """
    a = Snapshot(configs=(_config(pending_orders=0),), total_limit=1, at=AT)
    b = Snapshot(configs=(_config(pending_orders=2),), total_limit=1, at=AT)
    assert a.core != b.core
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/app/test_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.app.snapshot'`

- [ ] **Step 3: 구현한다**

`src/autotrading7s/app/snapshot.py`:

```python
"""엔진 → GUI 상태 스냅샷 — 설계서 14.1절의 표를 그리기 위한 것.

**`holdings()` 뷰로는 그 표를 그릴 수 없다.** 그 뷰는 `stage_state WHERE
status IN ('HOLDING','SELL_PENDING')` 로 조인하므로 보유 0 인 설정은 행을 만들지
못하고(목업의 `NAVER 0/5 IDLE` 이 그런 행이다), `config_id` 도 없어서 명령을
보낼 대상을 알 수 없다. 스냅샷은 `list_configs()`·`load_active_cycles()`·
`load_stages()` 로 만든다.

스냅샷은 **이벤트**다. 큐 계약이 한 방향(명령 in / 이벤트 out)으로 유지되어야
설계서 7.1절이 말한 "향후 프로세스 분리 시 큐를 소켓으로 교체" 가 성립한다.
요청-응답 채널은 상관 ID 와 블로킹이 필요한 두 번째 프로토콜이다.

**단계는 도메인 객체를 그대로 담는다.** 설계서 14.4절이 표시용 계산조차
`domain/pnl.py` 의 순수 함수를 호출하라고 규정하고, 그 함수들이
`Sequence[StageState]` 를 받는다. 별도 DTO 로 옮기면 뷰모델이 계산을 다시
구현하게 되고 그것이 14.4절이 금지한 것이다.

**이벤트로만 알 수 있는 것은 여기 없다.** 대사 판정, 로드 실패, 시세 폴백
여부는 DB 에서 읽을 수 없다 — 프레젠터가 이벤트에서 누적한다. 스냅샷에도
넣으면 같은 사실의 출처가 둘이 되고 어느 쪽이 최신인지 알 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from autotrading7s.app.events import Event, _require_aware
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """한 설정과 그 활성 사이클의 상태. 사이클이 없으면 cycle_* 이 None 이다."""

    config_id: int
    stock_code: str
    stock_name: str | None
    label: str | None
    config_status: str                 # IDLE | ACTIVE (설계서 12.1절)
    max_stages: int
    drop_pct: Decimal
    target_pct: Decimal
    amount_per_stage: int
    allow_rebuy: bool
    rebuy_cooldown_sec: int
    stock_limit: int                   # split_config.total_limit — 종목 한도
    cycle_id: int | None
    cycle_seq: int | None
    cycle_status: CycleStatus | None
    anchor_price: int | None
    ladder: Ladder | None
    cycle_started_at: datetime | None
    stages: tuple[StageState, ...]
    pending_orders: int


@dataclass(frozen=True, slots=True)
class Snapshot(Event):
    configs: tuple[ConfigSnapshot, ...]
    total_limit: int                   # 전체 총한도 (EngineSettings)
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)

    @property
    def core(self) -> tuple[object, ...]:
        """`at` 을 제외한 비교용 값 — 상태가 변했는지만 본다.

        `at` 을 포함하면 매 틱마다 스냅샷이 달라져 유휴 구간에도 큐가 자란다.
        시간 주기로 거르는 대안은 `FakeClock` 이 멈춘 테스트에서 첫 스냅샷만
        나가게 만든다.
        """
        return (self.configs, self.total_limit)
```

`app/events.py` 의 `_require_aware` 를 재사용한다 — 같은 규칙을 두 번 쓰면 어긋난다. 이름이 밑줄로 시작하지만 같은 패키지 안이므로 의도된 공유다. **`events.py` 에서 그 함수 위에 그 사실을 주석으로 남긴다.**

- [ ] **Step 4: 테스트가 통과하는 것을 확인한다**

Run: `.venv/bin/python -m pytest tests/app -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/autotrading7s/app/snapshot.py src/autotrading7s/app/events.py tests/app/test_snapshot.py
git commit -m "$(printf 'feat: 엔진 → GUI 상태 스냅샷 계약\n\n2B 핸드오버 3. GUI 가 상태를 얻는 경로가 없었다 — holdings() 뷰는 리포지토리에\n있지만 GUI 는 DB 를 건드릴 수 없고, 그 규칙이 단일 작성자 전제를 성립시킨다.\n\nholdings() 뷰로는 설계서 14.1절의 표를 그릴 수 없다. 그 뷰는 보유 단계로\n조인하므로 보유 0 인 설정이 행을 만들지 못하고(목업의 NAVER 0/5 IDLE), config_id\n도 없어서 명령 대상을 알 수 없다.\n\n스냅샷을 이벤트로 두어 큐 계약을 한 방향으로 유지했다. 요청-응답 채널은 상관 ID\n와 블로킹이 필요한 두 번째 프로토콜이고, 그것을 도입하면 설계서 7.1절이 말한\n"큐를 소켓으로 교체" 가 단순한 작업이 아니게 된다.\n\n단계는 도메인 StageState 를 그대로 담는다. 설계서 14.4절이 표시용 계산조차\ndomain/pnl.py 를 호출하라고 규정하고 그 함수들이 Sequence[StageState] 를 받으므로,\n별도 DTO 로 옮기면 뷰모델이 계산을 다시 구현하게 된다.\n\ncore 가 at 을 제외한다 — 포함하면 유휴 틱마다 큐가 자란다.')"
```

---

## Task 2: 스냅샷 생성과 발행 (`engine/orchestrator.py`)

**Ruling: 상태가 변할 때만 발행한다.** `Snapshot.core` 를 마지막 발행분과 비교해 같으면 내지 않는다. 시간 주기로 거르는 대안은 `FakeClock` 이 멈춘 테스트에서 첫 스냅샷만 나가게 만들어 그 경로를 검증할 수 없게 한다. 틀렸을 경우 비용: 매 틱마다 스냅샷을 **만들기는** 한다 — 활성 사이클마다 `load_stages` 한 번이며, `on_tick` 이 이미 같은 읽기를 하므로 새로운 비용이 아니다. 틱 사이 유휴 시간이 길어지면 문제가 되지 않고, 반대로 초당 수백 틱이 오는 종목에서는 이 읽기가 병목이 될 수 있다 — 그때는 리비전을 도메인 이벤트로 추적하는 것이 다음 단계다.

**Ruling: 손상된 사이클도 스냅샷에 담는다 (`stages=()`).** 그 설정이 표에서 사라지면 사용자는 그것이 존재하는지조차 모른다 — 2A 핸드오버 7 이 요구한 "사용자에게 나갈 길" 의 최소 조건은 **그 설정이 화면에 보이는 것**이다. 로드 실패의 내용은 `CycleLoadFailed` 이벤트가 이미 전달하고, 프레젠터가 그것을 그 행에 붙인다.

**Files:**
- Modify: `src/autotrading7s/engine/orchestrator.py`
- Test: `tests/engine/test_snapshot_emission.py`

**Interfaces:**
- Consumes: Task 1 의 `Snapshot`·`ConfigSnapshot`
- Produces: `Orchestrator.build_snapshot() -> Snapshot`, `Orchestrator.emit_snapshot_if_changed() -> bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/engine/test_snapshot_emission.py`:

```python
from __future__ import annotations

import queue
from datetime import UTC, datetime, timedelta

import pytest

from autotrading7s.adapters.fake.broker import FakeBroker, FillMode
from autotrading7s.adapters.fake.clock import FakeClock
from autotrading7s.app.commands import StartCycle
from autotrading7s.app.settings import EngineSettings
from autotrading7s.app.snapshot import Snapshot
from autotrading7s.domain.types import CycleStatus, StageStatus, Tick, TickSource
from autotrading7s.engine.orchestrator import Orchestrator

AT = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def _build(repo, broker, *, total_limit=100_000_000):
    clock = FakeClock(current=AT)
    qs = (queue.Queue(), queue.Queue(), queue.Queue())

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    orch = Orchestrator(
        repo=repo, broker=broker, clock=clock,
        settings=EngineSettings(total_limit=total_limit,
                                max_orders_per_minute=60),
        command_q=qs[0], priority_q=qs[1], event_q=qs[2], sleep=sleep,
        max_fallback_rounds=1,
    )
    return orch, clock, qs


def _snapshots(event_q):
    out = []
    while not event_q.empty():
        e = event_q.get_nowait()
        if isinstance(e, Snapshot):
            out.append(e)
    return out


def _tick(price, at=AT):
    return Tick(code="005930", price=price, at=at, source=TickSource.WS)


def test_snapshot_lists_every_config_including_idle_ones(repo_two_stocks):
    """설계서 14.1절 목업의 `NAVER 0/5 IDLE` 행을 그릴 수 있어야 한다."""
    repo_two_stocks.save_config(
        repo_two_stocks.load_config(1).__class__(
            config_id=None, stock_code="035420", stock_name="NAVER",
            label="기본", max_stages=5,
            drop_pct=repo_two_stocks.load_config(1).drop_pct,
            target_pct=repo_two_stocks.load_config(1).target_pct,
            amount_per_stage=1_000_000, allow_rebuy=False,
            rebuy_cooldown_sec=60, total_limit=5_000_000, status="IDLE",
            created_at=AT, updated_at=AT))
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker)

    snap = orch.build_snapshot()

    codes = [c.stock_code for c in snap.configs]
    assert codes == ["005930", "000660", "035420"]
    naver = snap.configs[-1]
    assert naver.config_status == "IDLE"
    assert naver.cycle_id is None
    assert naver.stages == ()
    assert naver.max_stages == 5


def test_snapshot_carries_the_total_limit_from_settings(repo_two_stocks):
    """상태바의 `총한도 9,971,350 / 21,000,000` 오른쪽 숫자다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker, total_limit=21_000_000)
    assert orch.build_snapshot().total_limit == 21_000_000


def test_snapshot_carries_stages_and_pending_order_counts(repo_two_stocks):
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker)

    snap = orch.build_snapshot()
    samsung = snap.configs[0]

    assert len(samsung.stages) == 7
    assert samsung.stages[0].status is StageStatus.HOLDING
    assert samsung.cycle_status is CycleStatus.RUNNING
    assert samsung.anchor_price == 10_000
    assert samsung.ladder is not None
    assert samsung.pending_orders == 0


@pytest.mark.asyncio
async def test_pending_order_count_is_per_config(repo_two_stocks):
    """긴급청산 다이얼로그의 '미체결 매수주문 2건이 함께 취소됩니다' 안내."""
    from autotrading7s.domain.rules import BuyStage
    from autotrading7s.engine.executor import Executor

    cyc = repo_two_stocks.load_active_cycles()[0]
    config = repo_two_stocks.load_config(cyc.config_id)
    broker = FakeBroker([9_500], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    ex = Executor(repo=repo_two_stocks, broker=broker,
                  clock=FakeClock(current=AT), emit=lambda e: None)
    waiting = next(s for s in repo_two_stocks.load_stages(cyc.cycle_id)
                   if s.stage_no == 2)
    await ex.send(cycle=cyc, config=config, stage=waiting,
                  decision=BuyStage(stage_no=2, limit_price=9_500, qty=52,
                                    reason="r"),
                  tick=_tick(9_500))

    orch, _, _ = _build(repo_two_stocks, broker)
    snap = orch.build_snapshot()

    assert snap.configs[0].pending_orders == 1
    assert snap.configs[1].pending_orders == 0


def test_a_corrupt_cycle_still_appears_with_no_stages(repo_two_stocks):
    """2A 핸드오버 7 — 사용자에게 나갈 길의 최소 조건은 그 설정이 보이는 것이다.

    표에서 사라지면 사용자는 그것이 존재하는지조차 모른다.
    """
    cyc = repo_two_stocks.load_active_cycles()[0]
    repo_two_stocks._conn.execute(
        "UPDATE stage_state SET trigger_price = trigger_price + 7 "
        "WHERE cycle_id = ? AND stage_no = 1", (cyc.cycle_id,))
    repo_two_stocks._conn.commit()
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, _ = _build(repo_two_stocks, broker)

    snap = orch.build_snapshot()

    samsung = snap.configs[0]
    assert samsung.stages == ()
    assert samsung.cycle_id == cyc.cycle_id          # 사이클은 여전히 보인다
    assert samsung.cycle_status is CycleStatus.RUNNING
    assert len(snap.configs) == 2                    # 다른 종목도 그대로


def test_emit_is_skipped_when_nothing_changed(repo_two_stocks):
    """유휴 틱마다 스냅샷이 나가면 큐가 자란다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, (_, _, event_q) = _build(repo_two_stocks, broker)

    assert orch.emit_snapshot_if_changed() is True
    assert orch.emit_snapshot_if_changed() is False
    assert orch.emit_snapshot_if_changed() is False
    assert len(_snapshots(event_q)) == 1


@pytest.mark.asyncio
async def test_emit_fires_when_a_stage_changes(repo_fresh):
    broker = FakeBroker([10_000], validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _build(repo_fresh, broker)
    orch.emit_snapshot_if_changed()
    _snapshots(event_q)

    await orch.on_tick(_tick(10_000))
    await orch.poll_pending()

    assert orch.emit_snapshot_if_changed() is True
    snaps = _snapshots(event_q)
    assert snaps and snaps[-1].configs[0].stages[0].status is StageStatus.HOLDING


@pytest.mark.asyncio
async def test_run_emits_a_snapshot_before_the_first_tick(repo_two_stocks):
    """GUI 는 기동 직후 화면을 그려야 한다 — 첫 틱을 기다릴 수 없다.

    장이 열리기 전이나 IDLE 설정만 있는 상태에서도 표가 보여야 한다.
    """
    broker = FakeBroker([10_000], fill_mode=FillMode.NEVER,
                        validate_account=True, cash=100_000_000)
    orch, clock, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    from autotrading7s.app.commands import Shutdown
    command_q.put(Shutdown())

    await orch.run()

    snaps = _snapshots(event_q)
    assert snaps, "run() 이 시작 직후 스냅샷을 내지 않았다"
    assert len(snaps[0].configs) == 2


@pytest.mark.asyncio
async def test_run_emits_a_snapshot_even_with_no_active_cycles(repo_fresh):
    """활성 사이클이 없으면 run() 이 바로 반환한다 — 그래도 스냅샷은 나가야 한다."""
    repo_fresh._conn.execute(
        "UPDATE cycle SET status = 'CLOSED', close_reason = 'NORMAL', "
        "closed_at = ?", (AT.isoformat(),))
    repo_fresh._conn.commit()
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, (_, _, event_q) = _build(repo_fresh, broker)

    await orch.run()

    snaps = _snapshots(event_q)
    assert snaps and snaps[0].configs[0].cycle_id is None


@pytest.mark.asyncio
async def test_command_handling_emits_a_snapshot(repo_fresh):
    """[시작]을 눌렀는데 화면이 그대로면 사용자는 눌렸는지 알 수 없다."""
    repo_fresh._conn.execute(
        "UPDATE cycle SET status = 'CLOSED', close_reason = 'NORMAL', "
        "closed_at = ?", (AT.isoformat(),))
    repo_fresh._conn.commit()
    broker = FakeBroker([10_000], validate_account=True)
    orch, _, (command_q, _, event_q) = _build(repo_fresh, broker)
    orch.emit_snapshot_if_changed()
    _snapshots(event_q)

    command_q.put(StartCycle(config_id=1))
    await orch.drain_commands()

    snaps = _snapshots(event_q)
    assert snaps, "명령 처리 후 스냅샷이 나가지 않았다"
    assert snaps[-1].configs[0].cycle_status is CycleStatus.STARTING
    assert snaps[-1].configs[0].config_status == "ACTIVE"
```

- [ ] **Step 2: 실패 확인 → 구현**

`Orchestrator` 에 추가한다.

```python
    # ── 스냅샷 ──────────────────────────────────────────────────────────
    def build_snapshot(self) -> Snapshot:
        """설계서 14.1절의 표를 그릴 수 있는 상태를 모은다.

        `holdings()` 뷰를 쓰지 않는 이유: 그 뷰는 보유 단계로 조인하므로
        보유 0 인 설정이 행을 만들지 못하고 `config_id` 도 없다.
        """
        by_config = {c.config_id: c for c in self._repo.load_active_cycles()}
        pending: dict[int, int] = {}
        for row in self._repo.load_pending_orders():
            pending[row.cycle_id] = pending.get(row.cycle_id, 0) + 1

        configs: list[ConfigSnapshot] = []
        for config in self._repo.list_configs():
            cyc = by_config.get(config.config_id)
            stages: tuple[StageState, ...] = ()
            if cyc is not None:
                try:
                    stages = tuple(self._repo.load_stages(cyc.cycle_id))
                except CorruptRowError:
                    # 손상된 사이클도 담는다 — 표에서 사라지면 사용자는 그것이
                    # 존재하는지조차 모른다. 실패의 내용은 CycleLoadFailed
                    # 이벤트가 전달하고 프레젠터가 그 행에 붙인다.
                    stages = ()
            configs.append(ConfigSnapshot(
                config_id=config.config_id,
                stock_code=config.stock_code,
                stock_name=config.stock_name,
                label=config.label,
                config_status=config.status,
                max_stages=config.max_stages,
                drop_pct=config.drop_pct,
                target_pct=config.target_pct,
                amount_per_stage=config.amount_per_stage,
                allow_rebuy=config.allow_rebuy,
                rebuy_cooldown_sec=config.rebuy_cooldown_sec,
                stock_limit=config.total_limit,
                cycle_id=None if cyc is None else cyc.cycle_id,
                cycle_seq=None if cyc is None else cyc.seq,
                cycle_status=None if cyc is None else cyc.status,
                anchor_price=None if cyc is None else cyc.anchor_price,
                ladder=None if cyc is None else cyc.ladder,
                cycle_started_at=None if cyc is None else cyc.started_at,
                stages=stages,
                pending_orders=0 if cyc is None
                               else pending.get(cyc.cycle_id, 0),
            ))
        return Snapshot(configs=tuple(configs),
                        total_limit=self._settings.total_limit,
                        at=self._clock.now())

    def emit_snapshot_if_changed(self) -> bool:
        """상태가 변했을 때만 발행한다. 발행했으면 True.

        `Snapshot.core` 가 `at` 을 제외하므로 유휴 틱에서는 아무것도 나가지
        않는다.
        """
        snap = self.build_snapshot()
        if snap.core == self._last_snapshot_core:
            return False
        self._last_snapshot_core = snap.core
        self._emit(snap)
        return True
```

생성자에 `self._last_snapshot_core: tuple[object, ...] | None = None` 을 추가한다.

`drain_commands` 의 끝에서 발행한다 — 명령을 하나라도 처리했을 때만.

```python
    async def drain_commands(self) -> None:
        """`priority_q` 를 먼저 완전히 비우고, 그 다음 `command_q` 를 본다."""
        handled = False
        for q in (self._priority_q, self._command_q):
            while True:
                try:
                    command = q.get_nowait()
                except queue.Empty:
                    break
                await self._handle(command)
                handled = True
        if handled:
            # [시작]을 눌렀는데 화면이 그대로면 사용자는 눌렸는지 알 수 없다.
            self.emit_snapshot_if_changed()
```

`_cycle_once` 의 끝과 `run()` 의 첫 `drain_commands` 뒤에 발행한다.

```python
    async def run(self) -> None:
        await self.drain_commands()
        # GUI 는 기동 직후 화면을 그려야 한다 — 첫 틱을 기다릴 수 없고, 활성
        # 사이클이 없으면 아래 루프가 바로 반환하므로 여기서 내야 한다.
        self.emit_snapshot_if_changed()
        rounds = 0
        ...

    async def _cycle_once(self, tick: Tick) -> None:
        await self.drain_commands()
        await self.on_tick(tick)
        await self.poll_pending()
        if self._due_for_reconcile(tick):
            await self.reconcile()
        self.emit_snapshot_if_changed()
```

새 import: `from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot`, `from autotrading7s.domain.stage import StageState`(이미 있다).

- [ ] **Step 3~5: 통과 확인 → 전체 회귀 → 커밋**

```bash
git add src/autotrading7s/engine/orchestrator.py tests/engine/test_snapshot_emission.py
git commit -m "$(printf 'feat: 스냅샷 생성과 발행 — 상태가 변할 때만\n\n설계서 14.1절의 표를 그릴 수 있는 상태를 list_configs·load_active_cycles·\nload_stages 로 모은다. holdings() 뷰를 쓰지 않는 이유는 그 뷰가 보유 단계로\n조인하므로 보유 0 인 설정이 행을 만들지 못하고 config_id 도 없기 때문이다.\n\nSnapshot.core 가 at 을 제외하므로 유휴 틱에서는 아무것도 나가지 않는다. 시간\n주기로 거르는 대안은 FakeClock 이 멈춘 테스트에서 첫 스냅샷만 나가게 만들어\n그 경로를 검증할 수 없게 한다.\n\n손상된 사이클도 stages=() 로 담는다. 표에서 사라지면 사용자는 그것이 존재하는지\n조차 모르고, 2A 핸드오버 7 이 요구한 "사용자에게 나갈 길" 의 최소 조건은 그\n설정이 화면에 보이는 것이다.\n\nrun() 이 첫 틱 전에 발행한다 — 활성 사이클이 없으면 루프가 바로 반환하므로\n그러지 않으면 IDLE 설정만 있는 화면이 영원히 비어 있다.')"
```

---

## Task 3: 설정 등록·수정 명령과 `update_config`

**Ruling: 수정은 `IDLE` 설정만 가능하다.** `ACTIVE` 설정의 값을 바꾸면 진행 중인 사이클의 사다리(`cycle.ladder_json` 에 고정)와 어긋나고, `load_stages` 의 H4(`trigger_price` 대조)가 **그 사이클을 로드 불가로 만든다** — 2A 가 만든 안전장치가 정확히 그 상황을 잡는다. 즉 허용하면 사용자가 설정을 저장하는 것만으로 사이클을 복구 불가 상태로 만들 수 있다. 설계서 17절 단계 3 이 "설정 등록/수정" 을 요구하므로 수정 자체는 범위 안이지만, 대상은 `IDLE` 뿐이다. 틀렸을 경우 비용: 사용자가 진행 중인 설정의 이름조차 바꿀 수 없다 — 사이클을 종료한 뒤 바꾸면 되고, 그쪽이 안전한 방향이다.

**Ruling: 문자열 → `Decimal` 파싱은 뷰모델이 한다.** 명령은 이미 타입이 맞는 값만 담는다. 사용자가 비율에 `NaN` 이나 `abc` 를 넣으면 `decimal` 내부 예외가 그대로 올라와 오류 메시지가 불친절하다는 것이 Plan 1 의 기록이며(2B 핸드오버 9), 그 친절함은 입력 지점의 책임이다. 엔진은 도메인 불변식 위반만 `ConfigRejected` 로 되돌린다. 틀렸을 경우 비용: 같은 검증이 두 곳에 있는 것처럼 보인다 — 실제로는 다른 것이다(형식 대 불변식).

**Ruling: 삭제는 두지 않는다.** 설계서 14절에 삭제 UI 가 없고, `cycle` 이 `split_config` 를 FK 로 참조하므로 이력이 있는 설정은 지울 수 없다. 설계서의 모델은 `IDLE` 로 두는 것이다. 틀렸을 경우 비용: 오타로 만든 설정이 목록에 남는다 — 이름을 고칠 수 있으므로(IDLE 이면) 실용적 문제가 아니다.

**Files:**
- Modify: `src/autotrading7s/app/commands.py`, `src/autotrading7s/app/events.py`, `src/autotrading7s/engine/orchestrator.py`, `src/autotrading7s/ports/repository.py`, `src/autotrading7s/adapters/sqlite/repository.py`
- Test: `tests/adapters/test_repository_update_config.py`, `tests/engine/test_config_commands.py`

**Interfaces:**
- Produces:
  - `commands.SaveConfig(...)` — `config_id: None` 이면 신규, 정수면 수정
  - `events.ConfigSaved(config_id, at)`, `events.ConfigRejected(config_id, detail, at)`
  - `RepositoryPort.update_config(config: SplitConfig, *, at: datetime) -> None` — `config_id` 필수, `IDLE` 만

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 리포지토리**

`tests/adapters/test_repository_update_config.py`:

```python
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from autotrading7s.ports.repository import RepositoryPort, RowNotFound

AT = datetime(2026, 9, 2, 11, 0, tzinfo=UTC)


def test_update_config_changes_an_idle_config(repo_two_stocks):
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    changed = dataclasses.replace(repo_two_stocks.load_config(1),
                                  label="공격형", amount_per_stage=2_000_000)

    repo_two_stocks.update_config(changed, at=AT)

    reloaded = repo_two_stocks.load_config(1)
    assert reloaded.label == "공격형"
    assert reloaded.amount_per_stage == 2_000_000
    assert reloaded.status == "IDLE"


def test_update_config_refuses_an_active_config(repo_two_stocks):
    """ACTIVE 설정의 값을 바꾸면 진행 중인 사이클의 사다리와 어긋난다.

    `cycle.ladder_json` 은 고정되어 있고 `load_stages` 의 H4 가 `trigger_price`
    를 그 사다리와 대조하므로, 설정을 바꾸는 것만으로 **그 사이클이 로드 불가**
    가 된다 — 2A 가 만든 안전장치가 정확히 그 상황을 잡는다. 저장 한 번으로
    복구 불가 상태를 만들 수 있으면 안 된다.
    """
    import dataclasses

    assert repo_two_stocks.load_config(1).status == "ACTIVE"
    changed = dataclasses.replace(repo_two_stocks.load_config(1),
                                  amount_per_stage=2_000_000)
    with pytest.raises(ValueError, match="IDLE"):
        repo_two_stocks.update_config(changed, at=AT)
    assert repo_two_stocks.load_config(1).amount_per_stage == 500_000


def test_update_config_requires_a_config_id(repo_two_stocks):
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    orphan = dataclasses.replace(repo_two_stocks.load_config(1),
                                 config_id=None)
    with pytest.raises(ValueError, match="config_id"):
        repo_two_stocks.update_config(orphan, at=AT)


def test_update_config_rejects_a_missing_row(repo_two_stocks):
    ghost = dataclasses.replace(repo_two_stocks.load_config(1),
                                config_id=9999, status="IDLE")
    with pytest.raises(RowNotFound):
        repo_two_stocks.update_config(ghost, at=AT)


def test_update_config_does_not_change_status(repo_two_stocks):
    """상태는 `set_config_status` 의 몫이다 — 두 경로가 같은 컬럼을 쓰면
    어느 쪽이 최신인지 알 수 없다."""
    repo_two_stocks.set_config_status(1, "IDLE", at=AT)
    changed = dataclasses.replace(repo_two_stocks.load_config(1),
                                  status="ACTIVE", label="바뀜")
    repo_two_stocks.update_config(changed, at=AT)
    assert repo_two_stocks.load_config(1).status == "IDLE"
    assert repo_two_stocks.load_config(1).label == "바뀜"


def test_port_declares_update_config():
    assert "update_config" in RepositoryPort.__protocol_attrs__
```

- [ ] **Step 2: 실패하는 테스트를 쓴다 — 명령 처리**

`tests/engine/test_config_commands.py`:

```python
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
async def test_save_config_rejects_a_domain_invariant_violation(repo_two_stocks):
    """단계 수 2~7 (설계서 3.1절) — 도메인이 거부하고 엔진이 되돌린다."""
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(max_stages=9))

    await orch.drain_commands()

    rejected = [e for e in _drain(event_q) if isinstance(e, ConfigRejected)]
    assert len(rejected) == 1
    assert "max_stages" in rejected[0].detail or "단계" in rejected[0].detail
    assert len(repo_two_stocks.list_configs()) == 2


@pytest.mark.asyncio
async def test_save_config_rejects_a_stage_that_cannot_buy_one_share(
    repo_two_stocks,
):
    """1단계에서 1주도 못 사는 설정은 도메인이 거부한다 (Ladder 불변식).

    이 거부가 사용자에게 보이지 않으면 [저장]을 눌렀는데 아무 일도 일어나지
    않는 화면이 된다.
    """
    broker = FakeBroker([10_000], validate_account=True)
    orch, (command_q, _, event_q) = _build(repo_two_stocks, broker)
    command_q.put(_new(amount_per_stage=1))

    await orch.drain_commands()

    rejected = [e for e in _drain(event_q) if isinstance(e, ConfigRejected)]
    assert len(rejected) == 1


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
    with pytest.raises(TypeError):
        SaveConfig(config_id=None, stock_code="005930", stock_name=None,
                   label=None, max_stages=7, drop_pct="0.05",  # type: ignore[arg-type]
                   target_pct=PCT, amount_per_stage=1_000_000,
                   allow_rebuy=True, rebuy_cooldown_sec=60,
                   total_limit=7_000_000)
```

- [ ] **Step 3: 실패 확인 → 구현**

`commands.py` 에 추가한다.

```python
@dataclass(frozen=True, slots=True)
class SaveConfig(Command):
    """분할 설정 등록·수정 — 설계서 14.2절.

    `config_id` 가 `None` 이면 신규, 정수면 수정이다. **수정은 `IDLE` 설정만
    가능하다** — `ACTIVE` 설정의 값을 바꾸면 진행 중인 사이클의 사다리와
    어긋나고 `load_stages` 의 H4 가 그 사이클을 로드 불가로 만든다.

    값은 이미 타입이 맞아야 한다. 문자열 → `Decimal` 파싱은 뷰모델의 몫이며
    (`ui/view_model.parse_config_form`), 그래야 파싱 실패가 입력 위젯 옆에
    보인다 — 엔진 스레드에서 일어나면 그 메시지는 로그에만 남는다.
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

    def __post_init__(self) -> None:
        for name in ("drop_pct", "target_pct"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(
                    f"{name} must be Decimal, not {type(value).__name__} — "
                    f"문자열 파싱은 뷰모델의 몫이다"
                )
        for name in ("max_stages", "amount_per_stage", "rebuy_cooldown_sec",
                     "total_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(
                    f"{name} must be int, not {type(value).__name__}"
                )
```

`from decimal import Decimal` 을 추가한다.

`events.py` 에 추가한다.

```python
@dataclass(frozen=True, slots=True)
class ConfigSaved(Event):
    config_id: int
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)


@dataclass(frozen=True, slots=True)
class ConfigRejected(Event):
    """저장이 거부됐다. `detail` 은 도메인·리포지토리가 만든 문장을 그대로 담는다.

    이 이벤트가 없으면 [저장]을 눌렀는데 아무 일도 일어나지 않는 화면이 된다.
    """

    config_id: int | None
    detail: str
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)
```

`RepositoryPort`·`SqliteRepository` 에 `update_config` 를 추가한다.

```python
    def update_config(self, config: SplitConfig, *, at: datetime) -> None:
        """`IDLE` 설정의 값을 갱신한다. `status` 는 갱신하지 않는다.

        `ACTIVE` 설정을 거부하는 이유: 진행 중인 사이클의 사다리는
        `cycle.ladder_json` 에 고정되어 있고 `load_stages` 의 H4 가
        `trigger_price` 를 그 사다리와 대조한다. 설정을 바꾸면 그 대조가
        실패해 **사이클이 로드 불가**가 된다 — 저장 한 번으로 복구 불가
        상태를 만들 수 있으면 안 된다.

        `status` 를 건드리지 않는 이유: 상태 전이는 `set_config_status` 의
        몫이다. 두 경로가 같은 컬럼을 쓰면 어느 쪽이 최신인지 알 수 없다.
        """
        if config.config_id is None:
            raise ValueError("update_config requires config_id; use save_config "
                             "for a new row")
        current = self._conn.execute(
            "SELECT status FROM split_config WHERE id = ?",
            (config.config_id,),
        ).fetchone()
        if current is None:
            raise RowNotFound(f"no split_config row with id={config.config_id}")
        if dict(current)["status"] != "IDLE":
            raise ValueError(
                f"config {config.config_id} is "
                f"{dict(current)['status']} — IDLE 설정만 수정할 수 있다 "
                f"(진행 중인 사이클의 사다리와 어긋난다)"
            )
        # `config_to_row` 는 status·created_at·updated_at 을 모두 담는다.
        # status 는 set_config_status 의 몫이고, created_at 은 최초 등록 시각
        # 이므로 갱신하지 않으며, updated_at 은 인자 `at` 으로 덮는다.
        row = config_to_row(config)
        for key in ("status", "created_at", "updated_at"):
            row.pop(key, None)
        assignments = ", ".join(f"{k} = :{k}" for k in row)
        with self._conn:
            self._conn.execute(
                f"UPDATE split_config SET {assignments}, updated_at = :updated "
                "WHERE id = :id",
                row | {"id": config.config_id, "updated": dt_to_text(at)},
            )
```

`Orchestrator._handle` 에 분기를 추가한다.

```python
        elif isinstance(command, cmd.SaveConfig):
            self._save_config(command)
```

```python
    def _save_config(self, command: cmd.SaveConfig) -> None:
        """설계서 14.2절 [저장]. 거부를 반드시 이벤트로 되돌린다 —
        그러지 않으면 눌렀는데 아무 일도 일어나지 않는 화면이 된다."""
        at = self._clock.now()
        try:
            config = SplitConfig(
                config_id=command.config_id, stock_code=command.stock_code,
                stock_name=command.stock_name, label=command.label,
                max_stages=command.max_stages, drop_pct=command.drop_pct,
                target_pct=command.target_pct,
                amount_per_stage=command.amount_per_stage,
                allow_rebuy=command.allow_rebuy,
                rebuy_cooldown_sec=command.rebuy_cooldown_sec,
                total_limit=command.total_limit,
                # 신규는 IDLE 로 시작하고, 수정은 update_config 가 status 를
                # 아예 건드리지 않는다 — 상태 전이는 set_config_status 의 몫이다.
                status="IDLE",
                created_at=at, updated_at=at,
            )
            # 사다리가 성립하는지 여기서 확인한다 — 1단계에서 1주도 못 사는
            # 설정은 Ladder 의 불변식이 거부하고, 그 거부가 사용자에게
            # 보여야 한다.
            config.to_ladder(anchor_price=command.amount_per_stage)
            if command.config_id is None:
                config_id = self._repo.save_config(config)
            else:
                self._repo.update_config(config, at=at)
                config_id = command.config_id
        except (ValueError, TypeError) as exc:
            self._emit(ConfigRejected(config_id=command.config_id,
                                      detail=str(exc), at=at))
            return
        self._emit(ConfigSaved(config_id=config_id, at=at))
```

**`to_ladder(anchor_price=command.amount_per_stage)` 는 임시 앵커다** — 실제 앵커는 1단계 체결가로 확정되므로 여기서는 "사다리가 성립하는 앵커가 하나라도 있는가" 만 본다. 단계금액을 앵커로 쓰면 1단계에서 최소 1주를 살 수 있으므로, 이 검사를 통과하지 못하는 설정은 어떤 앵커에서도 성립하지 않는다.

- [ ] **Step 4~6: 통과 확인 → 전체 회귀 → 커밋**

```bash
git add src/autotrading7s/app src/autotrading7s/engine/orchestrator.py src/autotrading7s/ports/repository.py src/autotrading7s/adapters/sqlite/repository.py tests/adapters/test_repository_update_config.py tests/engine/test_config_commands.py tests/ports/test_repository.py
git commit -m "$(printf 'feat: 설정 등록·수정 명령 (설계서 14.2절)\n\n수정은 IDLE 설정만 가능하다. ACTIVE 설정의 값을 바꾸면 진행 중인 사이클의\n사다리(ladder_json 에 고정)와 어긋나고 load_stages 의 H4 가 그 사이클을 로드\n불가로 만든다 — 저장 한 번으로 복구 불가 상태를 만들 수 있으면 안 된다.\n2A 가 만든 안전장치가 정확히 그 상황을 잡는다.\n\nupdate_config 는 status 를 건드리지 않는다. 상태 전이는 set_config_status 의\n몫이고, 두 경로가 같은 컬럼을 쓰면 어느 쪽이 최신인지 알 수 없다.\n\nSaveConfig 는 타입이 맞는 값만 받는다 — 문자열 파싱은 뷰모델의 몫이며 그래야\n파싱 실패가 입력 위젯 옆에 보인다(2B 핸드오버 9). 엔진 스레드에서 일어나면 그\n메시지는 로그에만 남는다.\n\n거부를 반드시 ConfigRejected 로 되돌린다. 없으면 [저장]을 눌렀는데 아무 일도\n일어나지 않는 화면이 된다.\n\n삭제는 두지 않았다 — 설계서 14절에 삭제 UI 가 없고 cycle 이 split_config 를 FK\n로 참조하므로 이력이 있는 설정은 지울 수 없다.')"
```

---

## Task 4: 보유현황 뷰모델 (`ui/view_model.py`)

**Ruling: 뷰모델은 숫자를 담고 서식은 렌더러가 한다.** `HoldingRowView.pnl_pct` 는 `Decimal` 이고 `-1.25%` 라는 문자열이 아니다. 이유: 숫자를 단정하는 테스트는 계산을 검증하고, 문자열을 단정하는 테스트는 서식을 검증한다 — 섞으면 소수점 자리를 바꿀 때 계산 테스트가 함께 깨지고, 어느 쪽이 틀렸는지 알 수 없다. Tkinter 위젯도 정렬·색상을 위해 숫자가 필요하다. 틀렸을 경우 비용: 렌더러가 하나 더 필요하다 — Task 9 가 그것이며, 그 렌더러가 설계서 14.1절의 목업을 그대로 재현해 **레이아웃까지 EC2 에서 테스트된다.**

**Ruling: 가격이 없는 종목은 합계에서 제외하고 그 사실을 함께 반환한다.** 투입금액으로 대체하면 손익 0 으로 보여 사용자가 그 종목이 반영됐다고 믿는다. 첫 틱이 오기 전(기동 직후, 장 시작 전)에 정확히 그 상태가 된다. 틀렸을 경우 비용: 합계가 일부 종목만 반영한다 — 그 사실이 `TotalsView.missing_prices` 에 있으므로 화면이 알릴 수 있다.

**Ruling: `⚠불일치` 가 다른 상태 표기를 덮는다.** 대사 불일치는 사용자가 가장 먼저 알아야 하는 것이고, 그 상태에서 사이클은 이미 `PAUSED` 이므로 "일시정지" 는 같은 사실의 덜 중요한 절반이다.

**Files:**
- Create: `src/autotrading7s/ui/__init__.py`, `src/autotrading7s/ui/view_model.py`
- Test: `tests/ui/__init__.py`, `tests/ui/conftest.py`, `tests/ui/test_view_model_holdings.py`

**Interfaces:**
- Produces:
  - `HoldingRowView` — `config_id`, `stock_code`, `stock_name`, `label`, `held_qty`, `avg_price`, `current_price`, `pnl`, `pnl_pct`, `holding_stages`, `max_stages`, `status_label`
  - `TotalsView` — `invested`, `valuation`, `pnl`, `pnl_pct`, `missing_prices`
  - `HoldingsView` — `rows`, `totals`, `broker_avg_notice`
  - `status_label(config: ConfigSnapshot, *, mismatched: bool) -> str`
  - `build_holdings(snapshot, *, prices, mismatched_codes) -> HoldingsView`

- [ ] **Step 1: 공유 픽스처를 만든다**

`tests/ui/conftest.py`:

```python
"""뷰모델 테스트의 스냅샷 픽스처.

설계서 14.1절 목업의 세 행(삼성전자 3/7 감시, 카카오 7/7 소진, NAVER 0/5 IDLE)
을 그대로 만든다 — 목업이 이 계획의 사양이므로 그것을 재현할 수 있어야 한다.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus, StageStatus

AT = datetime(2026, 9, 2, 9, 42, tzinfo=UTC)
PCT = Decimal("0.05")


def ladder(anchor: int, *, stages: int = 7, amount: int = 1_000_000) -> Ladder:
    return Ladder(anchor_price=anchor, drop_pct=PCT, target_pct=PCT,
                  max_stages=stages, amount_per_stage=amount)


def stages_of(lad: Ladder, *, holding: dict[int, tuple[int, int]] | None = None,
              sold: tuple[int, ...] = ()) -> tuple[StageState, ...]:
    """`holding` 은 {단계: (체결가, 수량)}."""
    holding = holding or {}
    out = []
    for n in range(1, lad.max_stages + 1):
        st = StageState(stage_no=n, status=StageStatus.WAITING,
                        trigger_price=lad.trigger_price(n),
                        planned_qty=lad.planned_qty(n))
        if n in holding:
            price, qty = holding[n]
            st = dataclasses.replace(st, status=StageStatus.HOLDING,
                                     fill_price=price, fill_qty=qty,
                                     bought_at=AT)
        elif n in sold:
            st = dataclasses.replace(st, status=StageStatus.SOLD,
                                     last_sold_at=AT, rebuy_count=1)
        out.append(st)
    return tuple(out)


def config(**over) -> ConfigSnapshot:
    lad = over.pop("ladder", ladder(10_000))
    kw = dict(
        config_id=1, stock_code="005930", stock_name="삼성전자", label="기본",
        config_status="ACTIVE", max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=True, rebuy_cooldown_sec=60,
        stock_limit=7_000_000, cycle_id=2, cycle_seq=2,
        cycle_status=CycleStatus.RUNNING, anchor_price=lad.anchor_price,
        ladder=lad, cycle_started_at=AT, pending_orders=0,
        stages=stages_of(lad, holding={1: (10_000, 100), 2: (9_480, 105),
                                       3: (8_950, 111)}),
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def idle_config(**over) -> ConfigSnapshot:
    """설계서 14.1절의 `NAVER 0/5 IDLE` 행."""
    kw = dict(
        config_id=3, stock_code="035420", stock_name="NAVER", label="기본",
        config_status="IDLE", max_stages=5, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=False, rebuy_cooldown_sec=60,
        stock_limit=5_000_000, cycle_id=None, cycle_seq=None,
        cycle_status=None, anchor_price=None, ladder=None,
        cycle_started_at=None, stages=(), pending_orders=0,
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def exhausted_config(**over) -> ConfigSnapshot:
    """설계서 14.1절의 `카카오 7/7 소진` 행 — 전 단계가 보유 중이다."""
    lad = ladder(9_000, amount=1_000_000)
    holding = {n: (lad.trigger_price(n), lad.planned_qty(n))
               for n in range(1, 8)}
    kw = dict(
        config_id=2, stock_code="035720", stock_name="카카오", label="공격형",
        config_status="ACTIVE", max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, allow_rebuy=True, rebuy_cooldown_sec=60,
        stock_limit=7_000_000, cycle_id=5, cycle_seq=1,
        cycle_status=CycleStatus.RUNNING, anchor_price=9_000, ladder=lad,
        cycle_started_at=AT, stages=stages_of(lad, holding=holding),
        pending_orders=0,
    )
    kw.update(over)
    return ConfigSnapshot(**kw)


def snapshot(*configs: ConfigSnapshot, total_limit: int = 21_000_000) -> Snapshot:
    return Snapshot(configs=configs or (config(),), total_limit=total_limit,
                    at=AT)


@pytest.fixture
def three_row_snapshot() -> Snapshot:
    return snapshot(config(), exhausted_config(), idle_config())
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/ui/test_view_model_holdings.py`:

```python
from __future__ import annotations

import dataclasses
from decimal import Decimal

from autotrading7s.domain import pnl
from autotrading7s.domain.types import CycleStatus, StageStatus
from autotrading7s.ui.view_model import build_holdings, status_label

from .conftest import config, exhausted_config, idle_config, snapshot


def test_row_order_follows_the_snapshot(three_row_snapshot):
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert [r.stock_code for r in view.rows] == ["005930", "035720", "035420"]


def test_row_carries_config_id_so_buttons_can_send_commands(three_row_snapshot):
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert [r.config_id for r in view.rows] == [1, 2, 3]


def test_quantities_and_average_price_come_from_domain_pnl(three_row_snapshot):
    """설계서 14.4절 — 표시용 계산조차 domain/pnl.py 를 호출한다."""
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    samsung = view.rows[0]
    stages = three_row_snapshot.configs[0].stages
    assert samsung.held_qty == pnl.held_qty(stages) == 316
    assert samsung.avg_price == pnl.avg_price(stages)
    assert samsung.holding_stages == pnl.holding_stage_count(stages) == 3
    assert samsung.max_stages == 7


def test_pnl_is_none_until_a_price_arrives(three_row_snapshot):
    """첫 틱 전에는 평가손익을 알 수 없다 — 0 으로 보여주면 안 된다."""
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert view.rows[0].current_price is None
    assert view.rows[0].pnl is None
    assert view.rows[0].pnl_pct is None


def test_pnl_uses_domain_pnl_with_the_latest_price(three_row_snapshot):
    stages = three_row_snapshot.configs[0].stages
    view = build_holdings(three_row_snapshot, prices={"005930": 9_340},
                          mismatched_codes=())
    row = view.rows[0]
    assert row.current_price == 9_340
    assert row.pnl == pnl.unrealized_pnl(stages, 9_340)
    assert row.pnl_pct == pnl.unrealized_pnl_pct(stages, 9_340)


def test_an_idle_config_shows_zero_held_and_no_average(three_row_snapshot):
    """보유가 없으면 평균단가는 None 이다 — 0 원으로 보여주면 안 된다."""
    view = build_holdings(three_row_snapshot, prices={"035420": 161_200},
                          mismatched_codes=())
    naver = view.rows[2]
    assert naver.held_qty == 0
    assert naver.avg_price is None
    assert naver.pnl is None            # 보유가 없으면 평가손익도 없다
    assert naver.current_price == 161_200
    assert (naver.holding_stages, naver.max_stages) == (0, 5)


# ── 상태 표기 (설계서 14.1절) ───────────────────────────────────────────
def test_status_labels_cover_the_six_documented_values():
    assert status_label(idle_config(), mismatched=False) == "IDLE"
    assert status_label(config(), mismatched=False) == "감시"
    assert status_label(exhausted_config(), mismatched=False) == "소진"
    paused = dataclasses.replace(config(), cycle_status=CycleStatus.PAUSED)
    assert status_label(paused, mismatched=False) == "일시정지"
    liquidating = dataclasses.replace(config(),
                                      cycle_status=CycleStatus.LIQUIDATING)
    assert status_label(liquidating, mismatched=False) == "청산중"
    assert status_label(config(), mismatched=True) == "⚠불일치"


def test_mismatch_overrides_every_other_label():
    """대사 불일치는 사용자가 가장 먼저 알아야 하는 것이다.

    그 상태에서 사이클은 이미 PAUSED 이므로 "일시정지" 는 같은 사실의 덜
    중요한 절반이다.
    """
    paused = dataclasses.replace(config(), cycle_status=CycleStatus.PAUSED)
    assert status_label(paused, mismatched=True) == "⚠불일치"
    assert status_label(idle_config(), mismatched=True) == "⚠불일치"


def test_starting_reads_as_watching():
    """설계서는 여섯 표기만 규정한다. STARTING 은 한 틱만 지속되며 사용자가
    보기엔 "시작을 눌렀고 감시 중" 이다."""
    starting = dataclasses.replace(config(), cycle_status=CycleStatus.STARTING,
                                   stages=(), ladder=None, anchor_price=None)
    assert status_label(starting, mismatched=False) == "감시"


def test_exhausted_needs_every_stage_holding():
    """`소진` 은 전 단계 보유다 — 6/7 은 아직 감시 중이다."""
    full = exhausted_config()
    last_waiting = dataclasses.replace(
        full.stages[6], status=StageStatus.WAITING,
        fill_price=None, fill_qty=None, bought_at=None)
    six = dataclasses.replace(full, stages=full.stages[:6] + (last_waiting,))
    assert status_label(six, mismatched=False) == "감시"


# ── 합계 ────────────────────────────────────────────────────────────────
def test_totals_sum_invested_and_valuation(three_row_snapshot):
    prices = {"005930": 9_340, "035720": 7_910}
    view = build_holdings(three_row_snapshot, prices=prices,
                          mismatched_codes=())
    invested = sum(pnl.invested_amount(c.stages)
                   for c in three_row_snapshot.configs)
    assert view.totals.invested == invested
    valuation = sum(pnl.held_qty(c.stages) * prices[c.stock_code]
                    for c in three_row_snapshot.configs
                    if c.stock_code in prices)
    assert view.totals.valuation == valuation
    assert view.totals.pnl == valuation - invested


def test_totals_exclude_stocks_without_a_price_and_say_so(three_row_snapshot):
    """투입금액으로 대체하면 손익 0 으로 보여 사용자가 반영됐다고 믿는다.

    기동 직후와 장 시작 전에 정확히 그 상태가 된다.
    """
    view = build_holdings(three_row_snapshot, prices={"005930": 9_340},
                          mismatched_codes=())
    assert view.totals.missing_prices == ("035720",)
    stages = three_row_snapshot.configs[0].stages
    assert view.totals.valuation == pnl.held_qty(stages) * 9_340
    assert view.totals.invested == pnl.invested_amount(stages)


def test_a_stock_with_no_holdings_is_not_a_missing_price(three_row_snapshot):
    """NAVER 는 보유가 0 이므로 가격이 없어도 합계에 영향이 없다."""
    view = build_holdings(three_row_snapshot,
                          prices={"005930": 9_340, "035720": 7_910},
                          mismatched_codes=())
    assert view.totals.missing_prices == ()


def test_totals_pct_is_none_when_nothing_is_invested():
    view = build_holdings(snapshot(idle_config()), prices={},
                          mismatched_codes=())
    assert view.totals.invested == 0
    assert view.totals.pnl_pct is None


def test_broker_average_notice_is_present(three_row_snapshot):
    """설계서 2.1절 — 증권사 앱의 평균단가와 다르다는 안내가 화면에 있어야 한다.

    이 문구가 없으면 사용자가 두 숫자를 비교하고 프로그램이 틀렸다고 판단한다.
    """
    view = build_holdings(three_row_snapshot, prices={}, mismatched_codes=())
    assert "증권사" in view.broker_avg_notice
    assert "단계별 체결가" in view.broker_avg_notice
```

- [ ] **Step 3: 실패 확인 → 구현**

Run: `.venv/bin/python -m pytest tests/ui -q` → FAIL (`No module named 'autotrading7s.ui'`)

`src/autotrading7s/ui/__init__.py` 는 빈 파일이다. `tests/ui/__init__.py` 도 빈 파일이다.

`src/autotrading7s/ui/view_model.py`:

```python
"""화면 뷰모델 — 설계서 14절.

**이 모듈은 `tkinter` 를 import 하지 않는다.** EC2 에 `tkinter` 가 아예 없으므로
(모듈 자체가 없다), 여기 들어온 로직은 자동 검증이 닿는 곳에 남고 위젯으로
넘어간 로직은 영원히 사각지대가 된다 (설계서 18.1 리스크 7).

**숫자를 담고 서식은 하지 않는다.** `pnl_pct` 는 `Decimal` 이고 `"-1.25%"` 가
아니다. 숫자를 단정하는 테스트는 계산을 검증하고 문자열을 단정하는 테스트는
서식을 검증한다 — 섞으면 소수점 자리를 바꿀 때 계산 테스트가 함께 깨지고 어느
쪽이 틀렸는지 알 수 없다. 서식은 `ui/text_render.py` 와 위젯의 몫이다.

**계산은 `domain/` 의 순수 함수를 부른다** (설계서 14.4절). 평가손익률조차 여기서
직접 계산하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot
from autotrading7s.domain import pnl
from autotrading7s.domain.types import CycleStatus

BROKER_AVG_NOTICE = (
    "증권사 앱의 평균단가는 종목 전체 1개 값이고, 본 프로그램의 단계별 "
    "체결가는 내부 가상 넘버링 기준입니다."
)


@dataclass(frozen=True, slots=True)
class HoldingRowView:
    config_id: int
    stock_code: str
    stock_name: str | None
    label: str | None
    held_qty: int
    avg_price: int | None
    current_price: int | None
    pnl: int | None
    pnl_pct: Decimal | None
    holding_stages: int
    max_stages: int
    status_label: str


@dataclass(frozen=True, slots=True)
class TotalsView:
    invested: int
    valuation: int
    pnl: int
    pnl_pct: Decimal | None
    missing_prices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HoldingsView:
    rows: tuple[HoldingRowView, ...]
    totals: TotalsView
    broker_avg_notice: str = BROKER_AVG_NOTICE


def status_label(config: ConfigSnapshot, *, mismatched: bool) -> str:
    """설계서 14.1절의 여섯 표기.

    `⚠불일치` 가 나머지를 덮는다 — 사용자가 가장 먼저 알아야 하는 것이고, 그
    상태에서 사이클은 이미 `PAUSED` 이므로 "일시정지" 는 같은 사실의 덜 중요한
    절반이다.
    """
    if mismatched:
        return "⚠불일치"
    if config.cycle_status is None or config.config_status == "IDLE":
        return "IDLE"
    if config.cycle_status is CycleStatus.LIQUIDATING:
        return "청산중"
    if config.cycle_status is CycleStatus.PAUSED:
        return "일시정지"
    # `소진` 은 전 단계 보유다 — 6/7 은 아직 감시 중이다.
    if (config.stages
            and pnl.holding_stage_count(config.stages) == config.max_stages):
        return "소진"
    # STARTING 은 한 틱만 지속되며 사용자가 보기엔 "시작을 눌렀고 감시 중" 이다.
    return "감시"


def build_holdings(
    snapshot: Snapshot, *, prices: Mapping[str, int],
    mismatched_codes: Sequence[str],
) -> HoldingsView:
    """설계서 14.1절 보유현황 표.

    가격이 없는 종목은 **합계에서 제외하고 그 사실을 함께 반환한다.**
    투입금액으로 대체하면 손익 0 으로 보여 사용자가 그 종목이 반영됐다고
    믿는다 — 기동 직후와 장 시작 전에 정확히 그 상태가 된다.
    """
    mismatched = set(mismatched_codes)
    rows: list[HoldingRowView] = []
    invested = valuation = 0
    missing: list[str] = []

    for config in snapshot.configs:
        stages = config.stages
        held = pnl.held_qty(stages)
        price = prices.get(config.stock_code)
        row_invested = pnl.invested_amount(stages)
        rows.append(HoldingRowView(
            config_id=config.config_id,
            stock_code=config.stock_code,
            stock_name=config.stock_name,
            label=config.label,
            held_qty=held,
            avg_price=pnl.avg_price(stages),
            current_price=price,
            pnl=(None if price is None or held == 0
                 else pnl.unrealized_pnl(stages, price)),
            pnl_pct=(None if price is None or held == 0
                     else pnl.unrealized_pnl_pct(stages, price)),
            holding_stages=pnl.holding_stage_count(stages),
            max_stages=config.max_stages,
            status_label=status_label(
                config, mismatched=config.stock_code in mismatched),
        ))
        if held == 0:
            continue                      # 보유가 없으면 합계에 영향이 없다
        if price is None:
            missing.append(config.stock_code)
            continue
        invested += row_invested
        valuation += held * price

    total_pnl = valuation - invested
    total_pct = (None if invested == 0
                 else (Decimal(total_pnl) / invested * 100).quantize(
                     Decimal("0.01")))
    return HoldingsView(
        rows=tuple(rows),
        totals=TotalsView(invested=invested, valuation=valuation,
                          pnl=total_pnl, pnl_pct=total_pct,
                          missing_prices=tuple(missing)),
    )
```

**합계의 백분율은 `pnl` 을 쓰지 않는다** — `domain/pnl.py` 의 함수는 `Sequence[StageState]` 를 받고 여기서는 여러 종목을 합친 값이므로 대응하는 함수가 없다. 같은 반올림 규칙(`0.01`, `ROUND_HALF_UP` 기본)을 쓴다는 사실을 주석으로 남긴다.

- [ ] **Step 4~5: 통과 확인 → 커밋**

```bash
git add src/autotrading7s/ui tests/ui
git commit -m "$(printf 'feat: 보유현황 뷰모델 — 설계서 14.1절\n\nui/view_model.py 는 tkinter 를 import 하지 않는다. EC2 에 tkinter 가 아예 없으므로\n여기 들어온 로직은 자동 검증이 닿는 곳에 남고 위젯으로 넘어간 로직은 영원히\n사각지대가 된다.\n\n숫자를 담고 서식은 하지 않는다. 숫자를 단정하는 테스트는 계산을, 문자열을\n단정하는 테스트는 서식을 검증한다 — 섞으면 소수점 자리를 바꿀 때 계산 테스트가\n함께 깨지고 어느 쪽이 틀렸는지 알 수 없다.\n\n가격이 없는 종목은 합계에서 제외하고 missing_prices 로 알린다. 투입금액으로\n대체하면 손익 0 으로 보여 사용자가 그 종목이 반영됐다고 믿는다 — 기동 직후와\n장 시작 전에 정확히 그 상태가 된다.\n\n⚠불일치 가 다른 표기를 덮는다. 그 상태에서 사이클은 이미 PAUSED 이므로\n"일시정지" 는 같은 사실의 덜 중요한 절반이다.\n\n설계서 2.1절의 증권사 평균단가 안내 문구를 뷰가 담는다 — 없으면 사용자가 두\n숫자를 비교하고 프로그램이 틀렸다고 판단한다.')"
```

---

## Task 5: 단계별 상세 뷰모델 — "목표까지 / 매수까지"

**이 열이 설계서 1.1절 5항의 요구다.** 보유 단계는 목표까지 남은 폭, 대기 단계는 매수 발동까지 남은 하락폭을 **같은 열에** 방향 기호로 구분해 보여준다. 사용자가 한 열만 훑어도 다음에 무슨 일이 일어날지 알 수 있어야 한다.

**Ruling: 두 의미를 하나의 계산으로 만든다.** `(기준가 − 현재가) / 현재가` 이며 기준가는 보유 단계면 목표가, 대기 단계면 발동가다. 목업의 숫자가 그것을 확인한다 — 1단계(체결 10,000 → 목표 10,500)가 현재가 9,340 에서 `+12.4% (1,160원)` 이고, 4단계(발동가 8,500)가 같은 현재가에서 `-9.0%` 다. 분모가 현재가인 것이 핵심이다: "지금 가격에서 몇 % 움직이면" 이 사용자의 질문이다. 틀렸을 경우 비용: 분모를 기준가로 하면 목업의 숫자와 어긋나고, 그 어긋남이 사용자에게는 "몇 % 남았는지" 의 오답이 된다.

**Ruling: 매도완료(`SOLD`) 단계는 폭을 계산하지 않는다.** 재매수가 허용되면 그 단계는 다시 대기가 되지만, `SOLD` 인 순간에는 쿨다운이 끝나기 전이므로 "하락 시 매수" 가 사실이 아니다. 틀렸을 경우 비용: 그 행의 폭 열이 비어 보인다 — `rebuy_count` 로 재매수 이력을 보여주므로 정보가 사라지지 않는다.

**Files:**
- Modify: `src/autotrading7s/ui/view_model.py`
- Test: `tests/ui/test_view_model_stages.py`

**Interfaces:**
- Produces:
  - `StageRowView` — `stage_no`, `trigger_price`, `status_label`, `fill_price`, `fill_qty`, `target_price`, `gap_pct`, `gap_won`, `gap_kind`, `rebuy_count`
  - `StageDetailView` — `config_id`, `stock_name`, `label`, `cycle_seq`, `anchor_price`, `started_at`, `rows`
  - `build_stage_detail(config, *, current_price) -> StageDetailView`
  - `STAGE_STATUS_LABELS: dict[StageStatus, str]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/ui/test_view_model_stages.py`:

```python
from __future__ import annotations

import dataclasses
from decimal import Decimal

from autotrading7s.domain.ladder import target_price
from autotrading7s.domain.types import StageStatus
from autotrading7s.ui.view_model import build_stage_detail

from .conftest import PCT, config, idle_config, ladder, stages_of


def test_header_identifies_the_cycle():
    """설계서 14.1절 — `단계별 상세 — 삼성전자 / 기본 (사이클 #2, 앵커 10,000원…)`."""
    view = build_stage_detail(config(), current_price=9_340)
    assert (view.stock_name, view.label) == ("삼성전자", "기본")
    assert view.cycle_seq == 2
    assert view.anchor_price == 10_000
    assert view.started_at is not None
    assert view.config_id == 1


def test_one_row_per_stage_in_ascending_order():
    view = build_stage_detail(config(), current_price=9_340)
    assert [r.stage_no for r in view.rows] == [1, 2, 3, 4, 5, 6, 7]


def test_holding_rows_show_fill_and_target():
    view = build_stage_detail(config(), current_price=9_340)
    first = view.rows[0]
    assert first.status_label == "보유"
    assert (first.fill_price, first.fill_qty) == (10_000, 100)
    assert first.target_price == target_price(10_000, PCT)


def test_waiting_rows_have_no_fill_or_target():
    view = build_stage_detail(config(), current_price=9_340)
    fourth = view.rows[3]
    assert fourth.status_label == "대기"
    assert (fourth.fill_price, fourth.fill_qty, fourth.target_price) == (
        None, None, None)


def test_gap_for_a_holding_stage_measures_the_way_up():
    """목업: 1단계 체결 10,000 → 목표 10,500, 현재가 9,340 → `▲ +12.4% (1,160원)`.

    분모가 현재가인 것이 핵심이다 — "지금 가격에서 몇 % 움직이면" 이 사용자의
    질문이다.
    """
    view = build_stage_detail(config(), current_price=9_340)
    first = view.rows[0]
    assert first.gap_kind == "TARGET"
    assert first.gap_won == target_price(10_000, PCT) - 9_340
    expected = (Decimal(first.gap_won) / 9_340 * 100).quantize(Decimal("0.1"))
    assert first.gap_pct == expected
    assert first.gap_pct > 0


def test_gap_for_a_waiting_stage_measures_the_way_down():
    """목업: 4단계 발동가 8,500, 현재가 9,340 → `▼ -9.0% 하락 시 매수`."""
    view = build_stage_detail(config(), current_price=9_340)
    fourth = view.rows[3]
    assert fourth.gap_kind == "TRIGGER"
    assert fourth.gap_won == fourth.trigger_price - 9_340
    assert fourth.gap_pct < 0
    assert fourth.gap_pct == Decimal("-9.0")


def test_the_gap_column_carries_both_meanings_in_one_field():
    """설계서 1.1절 5항 — 같은 열에 방향 기호로 두 의미를 담는다.

    한 열만 훑어도 다음에 무슨 일이 일어날지 알 수 있어야 한다.
    """
    view = build_stage_detail(config(), current_price=9_340)
    kinds = {r.gap_kind for r in view.rows}
    assert kinds == {"TARGET", "TRIGGER"}
    assert all(r.gap_pct is not None for r in view.rows)


def test_gap_is_none_without_a_price():
    view = build_stage_detail(config(), current_price=None)
    assert all(r.gap_pct is None and r.gap_won is None for r in view.rows)
    assert all(r.gap_kind is None for r in view.rows)


def test_sold_stage_has_no_gap_but_keeps_its_rebuy_count():
    """`SOLD` 인 순간에는 쿨다운이 끝나기 전이므로 "하락 시 매수" 가 사실이 아니다."""
    lad = ladder(10_000)
    sold = dataclasses.replace(config(), stages=stages_of(lad, sold=(1,)))
    view = build_stage_detail(sold, current_price=9_340)
    row = view.rows[0]
    assert row.status_label == "매도완료"
    assert row.gap_kind is None and row.gap_pct is None
    assert row.rebuy_count == 1


def test_pending_stages_read_as_pending():
    lad = ladder(10_000)
    stages = list(stages_of(lad, holding={1: (10_000, 100)}))
    stages[1] = dataclasses.replace(stages[1],
                                    status=StageStatus.BUY_PENDING)
    stages[0] = dataclasses.replace(stages[0],
                                    status=StageStatus.SELL_PENDING)
    view = build_stage_detail(dataclasses.replace(config(),
                                                  stages=tuple(stages)),
                              current_price=9_340)
    assert view.rows[0].status_label == "매도대기"
    assert view.rows[1].status_label == "매수대기"


def test_sell_pending_still_shows_its_target():
    """매도대기는 목표가로 주문이 나간 상태다 — 목표가가 사라지면 사용자가
    무슨 가격에 팔리는지 알 수 없다."""
    lad = ladder(10_000)
    stages = list(stages_of(lad, holding={1: (10_000, 100)}))
    stages[0] = dataclasses.replace(stages[0],
                                    status=StageStatus.SELL_PENDING)
    view = build_stage_detail(dataclasses.replace(config(),
                                                  stages=tuple(stages)),
                              current_price=9_340)
    assert view.rows[0].target_price == target_price(10_000, PCT)
    assert view.rows[0].gap_kind == "TARGET"


def test_an_idle_config_has_no_stage_rows():
    view = build_stage_detail(idle_config(), current_price=161_200)
    assert view.rows == ()
    assert view.anchor_price is None
    assert view.cycle_seq is None
```

- [ ] **Step 2: 실패 확인 → 구현**

`view_model.py` 에 추가한다.

```python
STAGE_STATUS_LABELS: dict[StageStatus, str] = {
    StageStatus.WAITING: "대기",
    StageStatus.BUY_PENDING: "매수대기",
    StageStatus.HOLDING: "보유",
    StageStatus.SELL_PENDING: "매도대기",
    StageStatus.SOLD: "매도완료",
}

_HELD = (StageStatus.HOLDING, StageStatus.SELL_PENDING)


@dataclass(frozen=True, slots=True)
class StageRowView:
    stage_no: int
    trigger_price: int
    status_label: str
    fill_price: int | None
    fill_qty: int | None
    target_price: int | None
    gap_pct: Decimal | None
    gap_won: int | None
    gap_kind: str | None            # "TARGET" | "TRIGGER" | None
    rebuy_count: int


@dataclass(frozen=True, slots=True)
class StageDetailView:
    config_id: int
    stock_name: str | None
    label: str | None
    cycle_seq: int | None
    anchor_price: int | None
    started_at: datetime | None
    rows: tuple[StageRowView, ...]


def build_stage_detail(
    config: ConfigSnapshot, *, current_price: int | None,
) -> StageDetailView:
    """설계서 14.1절 단계별 상세.

    "목표까지 / 매수까지" 열이 설계서 1.1절 5항의 요구다 — 보유 단계는
    목표까지, 대기 단계는 매수 발동까지를 **같은 열에** 담아 사용자가 한 열만
    훑어도 다음에 무슨 일이 일어날지 알 수 있게 한다.

    두 의미가 하나의 계산이다: `(기준가 − 현재가) / 현재가`. 분모가 현재가인
    것이 핵심이다 — "지금 가격에서 몇 % 움직이면" 이 사용자의 질문이고,
    설계서 목업의 숫자가 그것을 확인한다.
    """
    rows: list[StageRowView] = []
    for stage in config.stages:
        held = stage.status in _HELD
        target = (target_price(stage.fill_price, config.target_pct)
                  if held and stage.fill_price is not None else None)
        reference: int | None = None
        kind: str | None = None
        if current_price is not None:
            if target is not None:
                reference, kind = target, "TARGET"
            elif stage.status is StageStatus.WAITING:
                reference, kind = stage.trigger_price, "TRIGGER"
            # SOLD·BUY_PENDING 은 기준가가 없다. SOLD 는 쿨다운이 끝나기 전이라
            # "하락 시 매수" 가 사실이 아니고, BUY_PENDING 은 이미 주문이 나갔다.
        gap_won = None if reference is None else reference - current_price
        gap_pct = (None if gap_won is None or current_price is None
                   else (Decimal(gap_won) / current_price * 100).quantize(
                       Decimal("0.1")))
        rows.append(StageRowView(
            stage_no=stage.stage_no,
            trigger_price=stage.trigger_price,
            status_label=STAGE_STATUS_LABELS[stage.status],
            fill_price=stage.fill_price,
            fill_qty=stage.fill_qty,
            target_price=target,
            gap_pct=gap_pct,
            gap_won=gap_won,
            gap_kind=kind,
            rebuy_count=stage.rebuy_count,
        ))
    return StageDetailView(
        config_id=config.config_id, stock_name=config.stock_name,
        label=config.label, cycle_seq=config.cycle_seq,
        anchor_price=config.anchor_price, started_at=config.cycle_started_at,
        rows=tuple(rows),
    )
```

새 import: `from datetime import datetime`, `from autotrading7s.domain.ladder import target_price`, `from autotrading7s.domain.types import CycleStatus, StageStatus`.

**`BUY_PENDING` 이 폭을 갖지 않는 것은 의도다** — 이미 주문이 나갔으므로 "몇 % 남았는가" 가 답이 아니다. 목업에 그 행이 없어 설계서가 정하지 않았고, 이것이 그 공백에 대한 결정이다.

- [ ] **Step 3~4: 통과 확인 → 커밋**

```bash
git add src/autotrading7s/ui/view_model.py tests/ui/test_view_model_stages.py
git commit -m "$(printf 'feat: 단계별 상세 뷰모델 — 목표까지 / 매수까지 한 열\n\n설계서 1.1절 5항의 요구다. 보유 단계는 목표까지, 대기 단계는 매수 발동까지를\n같은 열에 방향 기호로 담아 사용자가 한 열만 훑어도 다음에 무슨 일이 일어날지\n알 수 있게 한다.\n\n두 의미가 하나의 계산이다: (기준가 − 현재가) / 현재가. 분모가 현재가인 것이\n핵심이며 설계서 목업의 숫자가 그것을 확인한다 — 1단계(체결 10,000 → 목표\n10,500)가 현재가 9,340 에서 +12.4%%, 4단계(발동가 8,500)가 같은 현재가에서\n-9.0%% 다.\n\nSOLD 는 폭을 계산하지 않는다 — 그 순간에는 쿨다운이 끝나기 전이므로 "하락 시\n매수" 가 사실이 아니다. BUY_PENDING 도 마찬가지로 이미 주문이 나갔다.\n\n매도대기는 목표가를 유지한다 — 사라지면 사용자가 무슨 가격에 팔리는지 알 수\n없다.')"
```

---

## Task 6: 사다리 미리보기와 입력 파싱 (설계서 14.2절)

**미리보기가 이 화면의 핵심이다.** 사용자가 [저장]을 누르기 전에 그 설정이 어떤 사다리를 만드는지, 총투입이 한도에 들어가는지, 전 단계 보유 시 평단이 얼마인지 보여준다. 손절매가 없는 전략에서 그 세 숫자가 사용자가 위험을 가늠하는 유일한 수단이다.

**Ruling: 미리보기는 발동가를 체결가로 가정한다.** 설계서 목업의 ⓘ 문구가 그 사실을 명시한다("실제 앵커는 1단계 체결가로 확정되며, 각 단계 목표가는 발동가가 아니라 실제 체결가 기준으로 계산됩니다"). 뷰모델은 그 가정을 `assumed_fill_is_trigger=True` 같은 플래그로 숨기지 않고, **그 문구를 뷰에 담아** 화면이 반드시 보여주게 한다. 틀렸을 경우 비용: 없다 — 설계서가 이미 정한 것이다.

**Ruling: 문자열 파싱은 `parse_config_form` 이 하고 친절한 오류를 낸다.** 사용자가 비율에 `abc`·`NaN` 을 넣으면 `decimal.InvalidOperation` 이 그대로 올라와 오류 메시지가 불친절하다는 것이 Plan 1 의 기록이다(2B 핸드오버 9). 그 예외는 `ArithmeticError` 이지 `ValueError` 가 아니므로 넓은 `except ValueError` 로도 잡히지 않는다 — 명시적으로 잡아야 한다.

**Files:**
- Modify: `src/autotrading7s/ui/view_model.py`
- Test: `tests/ui/test_view_model_ladder_preview.py`

**Interfaces:**
- Produces:
  - `LadderPreviewRow` — `stage_no`, `trigger_price`, `qty`, `investment`, `target_price`, `cumulative`
  - `LadderPreview` — `rows`, `total_investment`, `stock_limit`, `headroom`, `over_limit`, `last_drop_pct`, `full_avg_price`, `full_avg_drop_pct`, `notice`
  - `build_ladder_preview(*, anchor_price, max_stages, drop_pct, target_pct, amount_per_stage, stock_limit) -> LadderPreview`
  - `FormError(Exception)` / `parse_config_form(fields: Mapping[str, str]) -> dict[str, object]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/ui/test_view_model_ladder_preview.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.domain.ladder import Ladder, target_price
from autotrading7s.ui.view_model import (
    FormError,
    build_ladder_preview,
    parse_config_form,
)

PCT = Decimal("0.05")


def _preview(**over):
    kw = dict(anchor_price=9_340, max_stages=7, drop_pct=PCT, target_pct=PCT,
              amount_per_stage=1_000_000, stock_limit=7_000_000)
    kw.update(over)
    return build_ladder_preview(**kw)


def test_rows_match_the_domain_ladder():
    """미리보기는 계산을 다시 구현하지 않는다 (설계서 14.4절)."""
    lad = Ladder(anchor_price=9_340, drop_pct=PCT, target_pct=PCT,
                 max_stages=7, amount_per_stage=1_000_000)
    view = _preview()
    assert [r.stage_no for r in view.rows] == list(range(1, 8))
    assert [r.trigger_price for r in view.rows] == [
        lad.trigger_price(n) for n in range(1, 8)]
    assert [r.qty for r in view.rows] == [lad.planned_qty(n)
                                          for n in range(1, 8)]
    assert [r.investment for r in view.rows] == [lad.planned_investment(n)
                                                 for n in range(1, 8)]


def test_target_prices_assume_the_trigger_is_the_fill():
    """설계서 목업의 ⓘ 문구가 그 가정을 명시한다."""
    lad = Ladder(anchor_price=9_340, drop_pct=PCT, target_pct=PCT,
                 max_stages=7, amount_per_stage=1_000_000)
    view = _preview()
    assert [r.target_price for r in view.rows] == [
        target_price(lad.trigger_price(n), PCT) for n in range(1, 8)]


def test_cumulative_column_accumulates():
    view = _preview()
    running = 0
    for row in view.rows:
        running += row.investment
        assert row.cumulative == running
    assert view.total_investment == running


def test_total_matches_the_domain_total():
    lad = Ladder(anchor_price=9_340, drop_pct=PCT, target_pct=PCT,
                 max_stages=7, amount_per_stage=1_000_000)
    assert _preview().total_investment == lad.total_planned_investment()


def test_headroom_is_positive_when_the_plan_fits():
    """목업: `예상 총투입 6,978,200원 / 한도 7,000,000원 ✓ 여유 21,800`."""
    view = _preview()
    assert view.over_limit is False
    assert view.headroom == view.stock_limit - view.total_investment
    assert view.headroom > 0


def test_over_limit_is_flagged_not_hidden():
    """한도를 넘는 설정을 저장할 수는 있다 — 한도는 매수를 막는 장치이지\n    설정을 막는 장치가 아니다. 그러나 화면이 그 사실을 말해야 한다."""
    view = _preview(stock_limit=1_000_000)
    assert view.over_limit is True
    assert view.headroom < 0


def test_last_stage_drop_is_measured_against_the_anchor():
    """목업: `7단계 발동가는 앵커 대비 -30.1% (호가단위 내림 반영)`."""
    lad = Ladder(anchor_price=9_340, drop_pct=PCT, target_pct=PCT,
                 max_stages=7, amount_per_stage=1_000_000)
    view = _preview()
    expected = ((Decimal(lad.trigger_price(7) - 9_340) / 9_340) * 100
                ).quantize(Decimal("0.1"))
    assert view.last_drop_pct == expected
    assert view.last_drop_pct < 0


def test_full_average_price_and_its_drop():
    """목업: `전단계 보유 시 평단 7,823원 (앵커 대비 -16.2%)`.

    손절매가 없는 전략에서 이 숫자가 사용자가 최악의 경우를 가늠하는 수단이다.
    """
    lad = Ladder(anchor_price=9_340, drop_pct=PCT, target_pct=PCT,
                 max_stages=7, amount_per_stage=1_000_000)
    total_qty = sum(lad.planned_qty(n) for n in range(1, 8))
    view = _preview()
    assert view.full_avg_price == round(
        lad.total_planned_investment() / total_qty)
    expected = ((Decimal(view.full_avg_price - 9_340) / 9_340) * 100
                ).quantize(Decimal("0.1"))
    assert view.full_avg_drop_pct == expected


def test_notice_states_that_the_anchor_is_the_first_fill():
    """이 문구가 없으면 사용자가 미리보기의 목표가를 확정된 값으로 읽는다."""
    view = _preview()
    assert "1단계 체결가" in view.notice
    assert "체결가 기준" in view.notice


def test_a_config_that_cannot_buy_one_share_raises():
    """Ladder 의 불변식을 그대로 통과시킨다 — 미리보기가 도메인보다 관대하면
    화면에서 괜찮아 보이는 설정이 저장에서 거부된다."""
    from autotrading7s.domain.ladder import LadderConfigError

    with pytest.raises(LadderConfigError):
        _preview(amount_per_stage=1, anchor_price=100_000)


# ── 입력 파싱 (2B 핸드오버 9) ───────────────────────────────────────────
def _form(**over):
    fields = dict(stock_code="005930", stock_name="삼성전자", label="기본",
                  max_stages="7", drop_pct="5.0", target_pct="5.0",
                  amount_per_stage="1,000,000", rebuy_cooldown_sec="60",
                  total_limit="7,000,000", allow_rebuy="1")
    fields.update(over)
    return fields


def test_parse_turns_percent_text_into_a_ratio():
    """화면은 `5.0` %% 를 보여주고 도메인은 `Decimal("0.05")` 를 받는다."""
    parsed = parse_config_form(_form())
    assert parsed["drop_pct"] == Decimal("0.05")
    assert parsed["target_pct"] == Decimal("0.05")


def test_parse_accepts_thousands_separators():
    """사용자는 목업처럼 `1,000,000` 을 입력한다."""
    parsed = parse_config_form(_form())
    assert parsed["amount_per_stage"] == 1_000_000
    assert parsed["total_limit"] == 7_000_000


def test_parse_reports_the_field_name_on_bad_input():
    """오류가 어느 입력란의 것인지 말해야 위젯이 그 옆에 표시할 수 있다."""
    with pytest.raises(FormError) as exc:
        parse_config_form(_form(drop_pct="abc"))
    assert "drop_pct" in str(exc.value)


def test_parse_rejects_nan_explicitly():
    """`Decimal("NaN")` 은 만들어지고, 그 뒤 도메인이 `InvalidOperation` 을
    던진다 — 그것은 `ArithmeticError` 이지 `ValueError` 가 아니므로 넓은
    `except ValueError` 로도 잡히지 않는다 (Plan 1 의 기록).
    """
    for text in ("NaN", "nan", "Infinity", "-Infinity"):
        with pytest.raises(FormError, match="drop_pct"):
            parse_config_form(_form(drop_pct=text))


def test_parse_rejects_an_empty_required_field():
    with pytest.raises(FormError, match="stock_code"):
        parse_config_form(_form(stock_code="   "))


def test_parse_keeps_an_empty_optional_field_as_none():
    parsed = parse_config_form(_form(stock_name="", label=""))
    assert parsed["stock_name"] is None
    assert parsed["label"] is None


def test_parse_reads_the_rebuy_checkbox():
    assert parse_config_form(_form(allow_rebuy="1"))["allow_rebuy"] is True
    assert parse_config_form(_form(allow_rebuy="0"))["allow_rebuy"] is False


def test_parsed_fields_are_exactly_what_save_config_needs():
    """파싱 결과를 그대로 `SaveConfig(**parsed)` 에 넘길 수 있어야 한다.

    이름이 하나라도 어긋나면 위젯이 그 차이를 손으로 메우게 되고, 그 코드는
    EC2 에서 검증되지 않는 곳에 들어간다.
    """
    from autotrading7s.app.commands import SaveConfig

    parsed = parse_config_form(_form())
    command = SaveConfig(config_id=None, **parsed)
    assert command.stock_code == "005930"
    assert command.drop_pct == Decimal("0.05")
```

- [ ] **Step 2: 실패 확인 → 구현**

`view_model.py` 에 추가한다.

```python
LADDER_PREVIEW_NOTICE = (
    "실제 앵커는 1단계 체결가로 확정되며, 각 단계 목표가는 발동가가 아니라 "
    "실제 체결가 기준으로 계산됩니다."
)

_REQUIRED_TEXT = ("stock_code",)
_OPTIONAL_TEXT = ("stock_name", "label")
_INT_FIELDS = ("max_stages", "amount_per_stage", "rebuy_cooldown_sec",
               "total_limit")
_PCT_FIELDS = ("drop_pct", "target_pct")


class FormError(Exception):
    """입력란 하나의 형식 오류. 메시지에 필드 이름이 들어간다.

    위젯이 그 이름으로 어느 입력란 옆에 표시할지 결정한다.
    """


@dataclass(frozen=True, slots=True)
class LadderPreviewRow:
    stage_no: int
    trigger_price: int
    qty: int
    investment: int
    target_price: int
    cumulative: int


@dataclass(frozen=True, slots=True)
class LadderPreview:
    rows: tuple[LadderPreviewRow, ...]
    total_investment: int
    stock_limit: int
    headroom: int
    over_limit: bool
    last_drop_pct: Decimal
    full_avg_price: int
    full_avg_drop_pct: Decimal
    notice: str = LADDER_PREVIEW_NOTICE


def _pct_vs(value: int, anchor: int) -> Decimal:
    return (Decimal(value - anchor) / anchor * 100).quantize(Decimal("0.1"))


def build_ladder_preview(
    *, anchor_price: int, max_stages: int, drop_pct: Decimal,
    target_pct: Decimal, amount_per_stage: int, stock_limit: int,
) -> LadderPreview:
    """설계서 14.2절 사다리 미리보기.

    `Ladder` 를 그대로 쓴다 — 미리보기가 계산을 다시 구현하면 화면의 숫자와
    실제 사다리가 어긋나고, 그 어긋남은 사용자가 저장한 뒤에야 드러난다.
    `Ladder` 의 불변식(1단계에서 1주 이상)도 그대로 통과시킨다: 미리보기가
    도메인보다 관대하면 화면에서 괜찮아 보이는 설정이 저장에서 거부된다.
    """
    ladder = Ladder(anchor_price=anchor_price, drop_pct=drop_pct,
                    target_pct=target_pct, max_stages=max_stages,
                    amount_per_stage=amount_per_stage)
    rows: list[LadderPreviewRow] = []
    cumulative = 0
    total_qty = 0
    for n in range(1, max_stages + 1):
        investment = ladder.planned_investment(n)
        cumulative += investment
        total_qty += ladder.planned_qty(n)
        rows.append(LadderPreviewRow(
            stage_no=n, trigger_price=ladder.trigger_price(n),
            qty=ladder.planned_qty(n), investment=investment,
            target_price=target_price(ladder.trigger_price(n), target_pct),
            cumulative=cumulative,
        ))
    full_avg = int((Decimal(cumulative) / total_qty).to_integral_value(
        rounding=ROUND_HALF_UP))
    return LadderPreview(
        rows=tuple(rows), total_investment=cumulative, stock_limit=stock_limit,
        headroom=stock_limit - cumulative, over_limit=cumulative > stock_limit,
        last_drop_pct=_pct_vs(ladder.trigger_price(max_stages), anchor_price),
        full_avg_price=full_avg,
        full_avg_drop_pct=_pct_vs(full_avg, anchor_price),
    )


def parse_config_form(fields: Mapping[str, str]) -> dict[str, object]:
    """설정 등록 폼의 문자열을 `SaveConfig` 가 받는 타입으로 바꾼다.

    반환한 dict 를 그대로 `SaveConfig(config_id=..., **parsed)` 에 넘길 수
    있어야 한다 — 이름이 하나라도 어긋나면 위젯이 그 차이를 손으로 메우게
    되고, 그 코드는 EC2 에서 검증되지 않는 곳에 들어간다.

    `NaN`·`Infinity` 를 명시적으로 거부하는 이유: `Decimal("NaN")` 은
    만들어지고 그 뒤 도메인이 `decimal.InvalidOperation` 을 던지는데, 그것은
    `ArithmeticError` 이지 `ValueError` 가 아니므로 호출자의 넓은
    `except ValueError` 로도 잡히지 않는다 (Plan 1 의 기록).
    """
    out: dict[str, object] = {}
    for name in _REQUIRED_TEXT:
        text = (fields.get(name) or "").strip()
        if not text:
            raise FormError(f"{name}: 값을 입력하세요")
        out[name] = text
    for name in _OPTIONAL_TEXT:
        text = (fields.get(name) or "").strip()
        out[name] = text or None
    for name in _INT_FIELDS:
        text = (fields.get(name) or "").strip().replace(",", "")
        try:
            out[name] = int(text)
        except ValueError:
            raise FormError(f"{name}: 정수를 입력하세요 ({text!r})") from None
    for name in _PCT_FIELDS:
        text = (fields.get(name) or "").strip().replace("%", "")
        try:
            percent = Decimal(text)
        except InvalidOperation:
            raise FormError(f"{name}: 숫자를 입력하세요 ({text!r})") from None
        if not percent.is_finite():
            raise FormError(f"{name}: 유한한 숫자를 입력하세요 ({text!r})")
        out[name] = percent / 100
    out["allow_rebuy"] = (fields.get("allow_rebuy") or "").strip() in (
        "1", "true", "True", "yes", "on")
    return out
```

새 import: `from decimal import ROUND_HALF_UP, Decimal, InvalidOperation`, `from autotrading7s.domain.ladder import Ladder, target_price`.

- [ ] **Step 3~4: 통과 확인 → 커밋**

```bash
git add src/autotrading7s/ui/view_model.py tests/ui/test_view_model_ladder_preview.py
git commit -m "$(printf 'feat: 사다리 미리보기와 입력 파싱 — 설계서 14.2절\n\n미리보기가 Ladder 를 그대로 쓴다. 계산을 다시 구현하면 화면의 숫자와 실제\n사다리가 어긋나고, 그 어긋남은 사용자가 저장한 뒤에야 드러난다. Ladder 의\n불변식도 그대로 통과시킨다 — 미리보기가 도메인보다 관대하면 화면에서 괜찮아\n보이는 설정이 저장에서 거부된다.\n\n전 단계 보유 시 평단과 앵커 대비 하락률을 담는다. 손절매가 없는 전략에서 그\n숫자가 사용자가 최악의 경우를 가늠하는 수단이다.\n\n한도 초과는 숨기지 않고 표시한다 — 한도는 매수를 막는 장치이지 설정을 막는\n장치가 아니지만, 화면이 그 사실을 말해야 한다.\n\nparse_config_form 이 NaN·Infinity 를 명시적으로 거부한다. Decimal("NaN") 은\n만들어지고 그 뒤 도메인이 InvalidOperation 을 던지는데 그것은 ArithmeticError\n이지 ValueError 가 아니라 넓은 except ValueError 로도 잡히지 않는다(Plan 1 의\n기록, 2B 핸드오버 9).\n\n파싱 결과를 그대로 SaveConfig(**parsed) 에 넘길 수 있는지 테스트가 확인한다 —\n이름이 어긋나면 위젯이 그 차이를 손으로 메우고 그 코드는 검증 사각지대에 들어간다.')"
```

---

## Task 7: 다이얼로그·상태바·배너 뷰모델

**Ruling: 긴급청산 시도 이력은 프레젠터가 이벤트에서 누적한다.** 설계서 11.4절의 강제 종료 다이얼로그는 "청산 시도 3회, 마지막 15:28" 을 보여줘야 하는데, 그 이력은 `emergency_liquidation_log` 에 있고 **GUI 는 DB 를 읽을 수 없다.** 스냅샷에 넣는 대안은 이력 테이블 전체를 매 틱 실어 보내는 것이므로 과하다. 프레젠터가 `EmergencyResult` 이벤트에서 세션 안의 시도를 누적한다 — 강제 종료는 청산 실패 **직후**에 하는 일이므로 같은 세션이 정상 경로다. 틀렸을 경우 비용: GUI 를 재시작하면 그 횟수가 0 으로 보인다 — 다이얼로그가 "이 세션에서" 라고 명시하고, 영구 이력은 DB 에 남아 있다.

**Ruling: 배너의 시각은 위젯이 표시한다.** 뷰모델이 담으면 초마다 스냅샷이 필요해진다. 시계 읽기는 계산이 아니므로 설계서 14.4절이 금지한 것에 해당하지 않는다.

**Files:**
- Modify: `src/autotrading7s/ui/view_model.py`
- Test: `tests/ui/test_view_model_dialogs.py`

**Interfaces:**
- Produces:
  - `EmergencyDialogView` — `config_id`, `stock_code`, `stock_name`, `held_qty`, `holding_stages`, `current_price`, `estimated_amount`, `avg_price`, `estimated_pnl`, `estimated_pnl_pct`, `pending_orders`, `required_text`
  - `ForceCloseDialogView` — `config_id`, `stock_code`, `stock_name`, `remaining_qty`, `holding_stages`, `attempts`, `last_attempt_at`, `last_failure_detail`, `required_text`
  - `StatusBarView` — `quote_source_label`, `last_reconcile_label`, `total_used`, `total_limit`, `used_pct`
  - `BannerView` — `env_label`, `is_real`, `connection_label`, `engine_error`
  - `build_emergency_view(config, *, current_price, scope) -> EmergencyDialogView`
  - `build_force_close_view(config, *, attempts, last_attempt_at, last_failure_detail) -> ForceCloseDialogView`
  - `build_status_bar(...)`, `build_banner(...)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/ui/test_view_model_dialogs.py`:

```python
from __future__ import annotations

import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrading7s.app.commands import (
    FORCE_CLOSE_CONFIRMATION,
    LIQUIDATE_ALL_CONFIRMATION,
)
from autotrading7s.domain import pnl
from autotrading7s.ui.view_model import (
    build_banner,
    build_emergency_view,
    build_force_close_view,
    build_status_bar,
)

from .conftest import AT, config, idle_config


# ── 14.3 긴급청산 다이얼로그 ────────────────────────────────────────────
def test_emergency_view_shows_what_will_be_sold():
    """설계서 14.3절 — 보유수량·보유 단계 수·현재가·예상금액·평균단가·예상손익."""
    view = build_emergency_view(config(), current_price=9_340, scope="SINGLE")
    stages = config().stages
    assert view.stock_code == "005930"
    assert view.held_qty == pnl.held_qty(stages) == 316
    assert view.holding_stages == 3
    assert view.current_price == 9_340
    assert view.estimated_amount == 316 * 9_340
    assert view.avg_price == pnl.avg_price(stages)
    assert view.estimated_pnl == pnl.unrealized_pnl(stages, 9_340)
    assert view.estimated_pnl_pct == pnl.unrealized_pnl_pct(stages, 9_340)


def test_emergency_view_announces_orders_that_will_be_canceled():
    """설계서 14.3절 — `미체결 매수주문 2건이 함께 취소됩니다`.

    ②를 빠뜨리면 긴급청산이 무력화된다는 것이 설계서 11.1절의 경고이고,
    사용자가 그것이 함께 일어난다는 것을 알아야 한다.
    """
    view = build_emergency_view(dataclasses.replace(config(),
                                                     pending_orders=2),
                                current_price=9_340, scope="SINGLE")
    assert view.pending_orders == 2


def test_single_scope_needs_no_text_confirmation():
    view = build_emergency_view(config(), current_price=9_340, scope="SINGLE")
    assert view.required_text is None


def test_all_scope_requires_the_exact_confirmation_text():
    """설계서 11.2절 — 전체 청산은 `전체청산` 을 직접 입력해야 한다.

    상수를 명령 모듈에서 가져오므로 어긋날 수 없다.
    """
    view = build_emergency_view(config(), current_price=9_340, scope="ALL")
    assert view.required_text == LIQUIDATE_ALL_CONFIRMATION == "전체청산"


def test_emergency_view_without_a_price_has_no_estimate():
    """현재가를 모르면 예상금액을 추측하지 않는다 — 사용자가 그 숫자를 근거로
    실행 여부를 판단한다."""
    view = build_emergency_view(config(), current_price=None, scope="SINGLE")
    assert view.current_price is None
    assert view.estimated_amount is None
    assert view.estimated_pnl is None


def test_emergency_view_rejects_a_config_with_nothing_to_sell():
    """팔 것이 없는 종목에 긴급청산 다이얼로그를 띄우면 사용자를 오도한다."""
    with pytest.raises(ValueError, match="보유"):
        build_emergency_view(idle_config(), current_price=161_200,
                             scope="SINGLE")


# ── 11.4 강제 종료 다이얼로그 ───────────────────────────────────────────
def test_force_close_view_shows_the_remainder_and_the_attempts():
    """설계서 11.4절 — `남은 보유 40주 (보유 단계 1개)`, `청산 시도 3회, 마지막 15:28`."""
    view = build_force_close_view(
        config(), attempts=3, last_attempt_at=AT + timedelta(hours=6),
        last_failure_detail="거래정지 (API 응답 코드 40510)")
    assert view.remaining_qty == 316
    assert view.holding_stages == 3
    assert view.attempts == 3
    assert view.last_attempt_at == AT + timedelta(hours=6)
    assert "거래정지" in view.last_failure_detail


def test_force_close_view_requires_the_exact_confirmation_text():
    view = build_force_close_view(config(), attempts=1, last_attempt_at=AT,
                                  last_failure_detail=None)
    assert view.required_text == FORCE_CLOSE_CONFIRMATION == "강제종료"


def test_force_close_view_rejects_a_config_with_no_remainder():
    """잔량 0 의 강제 종료는 의미가 없다 (설계서 11.4절 절차 ③).

    엔진도 그것을 정상 종료로 처리하므로, 다이얼로그가 애초에 뜨면 안 된다.
    """
    with pytest.raises(ValueError, match="잔량"):
        build_force_close_view(idle_config(), attempts=1, last_attempt_at=AT,
                               last_failure_detail=None)


# ── 상태바 (설계서 14.1절 하단) ─────────────────────────────────────────
def test_status_bar_shows_the_quote_source():
    ws = build_status_bar(fallback_active=False, last_reconcile=None,
                          total_used=9_971_350, total_limit=21_000_000)
    assert "WebSocket" in ws.quote_source_label
    rest = build_status_bar(fallback_active=True, last_reconcile=None,
                            total_used=0, total_limit=1)
    assert "폴백" in rest.quote_source_label


def test_status_bar_shows_limit_usage():
    """목업: `총한도 9,971,350 / 21,000,000 (47%)`."""
    bar = build_status_bar(fallback_active=False, last_reconcile=None,
                           total_used=9_971_350, total_limit=21_000_000)
    assert bar.total_used == 9_971_350
    assert bar.total_limit == 21_000_000
    assert bar.used_pct == Decimal("47.5")


def test_status_bar_handles_a_zero_limit_without_dividing():
    bar = build_status_bar(fallback_active=False, last_reconcile=None,
                           total_used=0, total_limit=0)
    assert bar.used_pct is None


def test_status_bar_reports_the_last_reconcile():
    """목업: `대사 09:40 일치`. 불일치면 그 사실이 보여야 한다."""
    from autotrading7s.app.events import ReconcileMismatch

    quiet = build_status_bar(fallback_active=False, last_reconcile=None,
                             total_used=0, total_limit=1)
    assert "일치" in quiet.last_reconcile_label
    mismatch = ReconcileMismatch(stock_code="005930", internal_qty=316,
                                 broker_qty=300, verdict="INTERNAL_MORE",
                                 action_taken="PAUSED", at=AT)
    noisy = build_status_bar(fallback_active=False, last_reconcile=mismatch,
                             total_used=0, total_limit=1)
    assert "005930" in noisy.last_reconcile_label
    assert "INTERNAL_MORE" in noisy.last_reconcile_label


# ── 배너 (설계서 14.1절 상단) ───────────────────────────────────────────
def test_banner_distinguishes_mock_from_real():
    """실전 프로파일은 붉은 `▣ 실전투자` 다 — 색은 위젯이 `is_real` 로 정한다.

    이 구분이 흐려지면 사용자가 실전 계좌에서 시험한다.
    """
    mock = build_banner(env="mock", fallback_active=False, engine_error=None)
    assert mock.env_label == "▣ 모의투자"
    assert mock.is_real is False
    real = build_banner(env="real", fallback_active=False, engine_error=None)
    assert real.env_label == "▣ 실전투자"
    assert real.is_real is True


def test_banner_rejects_an_unknown_environment():
    """조용히 모의투자로 떨어지면 사용자가 실전이라고 믿는 채로 돌린다."""
    with pytest.raises(ValueError, match="env"):
        build_banner(env="prod", fallback_active=False, engine_error=None)


def test_banner_shows_the_connection_state():
    ws = build_banner(env="mock", fallback_active=False, engine_error=None)
    assert "WS" in ws.connection_label
    rest = build_banner(env="mock", fallback_active=True, engine_error=None)
    assert "폴백" in rest.connection_label


def test_banner_surfaces_a_dead_engine():
    """조용히 죽은 엔진은 "프로그램이 켜져 있는데 트리거를 놓치는" 최악의
    상태다 (설계서 18.1 리스크 6)."""
    dead = build_banner(env="mock", fallback_active=False,
                        engine_error="RuntimeError: 복구 실패")
    assert dead.engine_error is not None
    assert "복구 실패" in dead.engine_error
```

- [ ] **Step 2: 실패 확인 → 구현**

`view_model.py` 에 추가한다. 핵심만 적는다 — 나머지는 위 테스트가 요구하는 필드를 그대로 채운다.

```python
_ENV_LABELS = {"mock": "▣ 모의투자", "real": "▣ 실전투자"}


@dataclass(frozen=True, slots=True)
class EmergencyDialogView:
    config_id: int
    stock_code: str
    stock_name: str | None
    held_qty: int
    holding_stages: int
    current_price: int | None
    estimated_amount: int | None
    avg_price: int | None
    estimated_pnl: int | None
    estimated_pnl_pct: Decimal | None
    pending_orders: int
    required_text: str | None


def build_emergency_view(
    config: ConfigSnapshot, *, current_price: int | None, scope: str,
) -> EmergencyDialogView:
    """설계서 14.3절 재확인 다이얼로그.

    팔 것이 없는 종목에 이 다이얼로그를 띄우면 사용자를 오도하므로 거부한다.
    현재가를 모르면 예상금액을 추측하지 않는다 — 사용자가 그 숫자를 근거로
    실행 여부를 판단한다.
    """
    held = pnl.held_qty(config.stages)
    if held == 0:
        raise ValueError(
            f"{config.stock_code}: 보유 수량이 0 이므로 긴급청산할 것이 없다"
        )
    return EmergencyDialogView(
        config_id=config.config_id, stock_code=config.stock_code,
        stock_name=config.stock_name, held_qty=held,
        holding_stages=pnl.holding_stage_count(config.stages),
        current_price=current_price,
        estimated_amount=None if current_price is None else held * current_price,
        avg_price=pnl.avg_price(config.stages),
        estimated_pnl=(None if current_price is None
                       else pnl.unrealized_pnl(config.stages, current_price)),
        estimated_pnl_pct=(None if current_price is None else
                           pnl.unrealized_pnl_pct(config.stages, current_price)),
        pending_orders=config.pending_orders,
        required_text=(LIQUIDATE_ALL_CONFIRMATION if scope == "ALL" else None),
    )
```

`ForceCloseDialogView`·`build_force_close_view` 는 같은 모양이며, 잔량 0 을 `ValueError("잔량 …")` 로 거부하고 `required_text=FORCE_CLOSE_CONFIRMATION` 을 고정한다.

`build_status_bar` 는 `total_limit == 0` 에서 `used_pct=None` 을 낸다(0 으로 나누지 않는다). `build_banner` 는 알 수 없는 `env` 를 `ValueError` 로 거부한다 — 조용히 모의투자로 떨어지면 사용자가 실전이라고 믿는 채로 돌린다.

`from autotrading7s.app.commands import FORCE_CLOSE_CONFIRMATION, LIQUIDATE_ALL_CONFIRMATION` 을 import 한다 — 상수를 다시 쓰면 어긋난다.

- [ ] **Step 3~4: 통과 확인 → 커밋**

```bash
git add src/autotrading7s/ui/view_model.py tests/ui/test_view_model_dialogs.py
git commit -m "$(printf 'feat: 다이얼로그·상태바·배너 뷰모델\n\n확인 문자열 상수를 app/commands 에서 가져온다 — 다시 쓰면 어긋나고, 어긋나면\n사용자가 정확히 입력했는데 버튼이 활성화되지 않는다.\n\n긴급청산 다이얼로그가 함께 취소될 미체결 건수를 보여준다(설계서 14.3절).\n②를 빠뜨리면 긴급청산이 무력화된다는 것이 11.1절의 경고이고, 사용자가 그것이\n함께 일어난다는 것을 알아야 한다.\n\n현재가를 모르면 예상금액을 추측하지 않는다 — 사용자가 그 숫자를 근거로 실행\n여부를 판단한다. 팔 것이 없는 종목과 잔량 0 인 강제 종료는 다이얼로그 자체를\n거부한다(설계서 11.4절 절차 ③).\n\n배너가 알 수 없는 env 를 거부한다. 조용히 모의투자로 떨어지면 사용자가 실전\n이라고 믿는 채로 돌린다.\n\n엔진 오류를 배너에 노출한다 — 조용히 죽은 엔진은 프로그램이 켜져 있는데\n트리거를 놓치는 최악의 상태다(설계서 18.1 리스크 6).')"
```

---

## Task 8: 프레젠터 — 이벤트 소비 상태기계 (`ui/presenter.py`)

**이 모듈이 GUI 로직의 전부다.** 위젯은 프레젠터가 만든 뷰를 그리고 사용자 입력을 명령으로 되돌리는 일만 한다.

**Ruling: 대사 불일치 경고는 사용자나 사이클 종료가 지울 때까지 남는다.** 대사는 일치할 때 **이벤트를 내지 않는다**(설계서 10.2절: "일치 — 로그 없음") — 그래서 해소를 알 방법이 없다. `CycleClosed` 는 자동으로 지우고, 사용자가 기준선 초기화나 재개를 누를 때 위젯이 `clear_mismatch(code)` 를 함께 부른다. 틀렸을 경우 비용: 사용자가 실계좌를 맞춘 뒤에도 경고가 남는다 — 지우는 방법이 화면에 있으므로 막힌 상태가 아니다.

**Ruling: 로그 줄은 이벤트 종류를 그대로 담는다.** `OrderUnknown` 과 `OrderRejected` 를 같은 색으로 그리면 안 된다는 것이 2B 핸드오버 4 이고, 위젯이 그 구분을 하려면 종류 이름이 필요하다. 문구만 담으면 위젯이 문자열을 검사하게 되고 그것은 사각지대의 로직이다.

**Files:**
- Create: `src/autotrading7s/ui/presenter.py`
- Test: `tests/ui/test_presenter.py`

**Interfaces:**
- Produces:
  - `Severity` — `"INFO" | "WARN" | "ERROR"`
  - `LogLine` — `kind: str`, `severity: str`, `text: str`, `at: datetime`
  - `Presenter(env: str, *, log_capacity: int = 500)` — `.consume(event)`, `.consume_all(events)`, `.holdings()`, `.stage_detail(config_id)`, `.status_bar()`, `.banner()`, `.emergency(config_id, scope)`, `.force_close(config_id)`, `.log_lines()`, `.take_config_feedback()`, `.clear_mismatch(code)`, `.note_engine_error(text)`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/ui/test_presenter.py`:

```python
from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from autotrading7s.app.events import (
    ConfigRejected,
    ConfigSaved,
    CycleClosed,
    CycleLoadFailed,
    EmergencyResult,
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
from autotrading7s.ui.presenter import Presenter

from .conftest import AT, config, exhausted_config, idle_config, snapshot


def _presenter(env="mock") -> Presenter:
    return Presenter(env)


def _tick(code="005930", price=9_340):
    return TickUpdate(stock_code=code, price=price, source=TickSource.WS,
                      at=AT)


def test_holdings_is_empty_before_the_first_snapshot():
    """기동 직후 스냅샷이 오기 전에도 화면을 그릴 수 있어야 한다."""
    p = _presenter()
    view = p.holdings()
    assert view.rows == ()
    assert view.totals.invested == 0


def test_snapshot_populates_the_holdings_view(three_row_snapshot):
    p = _presenter()
    p.consume(three_row_snapshot)
    assert [r.stock_code for r in p.holdings().rows] == [
        "005930", "035720", "035420"]


def test_ticks_feed_the_price_column(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick()])
    assert p.holdings().rows[0].current_price == 9_340
    assert p.holdings().rows[0].pnl is not None


def test_a_later_tick_replaces_an_earlier_one(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(price=9_340),
                   _tick(price=9_500)])
    assert p.holdings().rows[0].current_price == 9_500


def test_mismatch_marks_the_row_and_survives_new_snapshots(three_row_snapshot):
    """대사는 일치할 때 이벤트를 내지 않으므로 해소를 알 방법이 없다.

    새 스냅샷이 온다고 지우면, 5분마다 오는 다음 대사까지 경고가 사라져
    사용자가 그 사이에 아무 문제도 없다고 믿는다.
    """
    p = _presenter()
    p.consume_all([three_row_snapshot, ReconcileMismatch(
        stock_code="005930", internal_qty=316, broker_qty=300,
        verdict="INTERNAL_MORE", action_taken="PAUSED", at=AT)])
    assert p.holdings().rows[0].status_label == "⚠불일치"

    p.consume(three_row_snapshot)
    assert p.holdings().rows[0].status_label == "⚠불일치"


def test_clear_mismatch_removes_the_warning(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, ReconcileMismatch(
        stock_code="005930", internal_qty=316, broker_qty=300,
        verdict="INTERNAL_MORE", action_taken="PAUSED", at=AT)])
    p.clear_mismatch("005930")
    assert p.holdings().rows[0].status_label != "⚠불일치"


def test_cycle_closed_clears_the_mismatch(three_row_snapshot):
    """사이클이 끝나면 그 불일치는 더 이상 이 사이클의 문제가 아니다."""
    p = _presenter()
    p.consume_all([three_row_snapshot, ReconcileMismatch(
        stock_code="005930", internal_qty=316, broker_qty=300,
        verdict="INTERNAL_MORE", action_taken="PAUSED", at=AT)])
    p.consume(CycleClosed(config_id=1, cycle_id=2,
                          reason=CloseReason.NORMAL, realized_pnl=0, at=AT))
    assert p.holdings().rows[0].status_label != "⚠불일치"


def test_quote_fallback_flows_to_the_banner_and_status_bar():
    p = _presenter()
    p.consume(QuoteFallback(stock_codes=("005930",), active=True, at=AT))
    assert "폴백" in p.banner().connection_label
    assert "폴백" in p.status_bar().quote_source_label

    p.consume(QuoteFallback(stock_codes=("005930",), active=False, at=AT))
    assert "WS" in p.banner().connection_label


def test_status_bar_limit_usage_comes_from_the_snapshot(three_row_snapshot):
    from autotrading7s.domain import pnl

    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(),
                   _tick(code="035720", price=7_910)])
    bar = p.status_bar()
    assert bar.total_limit == three_row_snapshot.total_limit
    assert bar.total_used == sum(pnl.invested_amount(c.stages)
                                 for c in three_row_snapshot.configs)


def test_stage_detail_selects_by_config_id(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick()])
    view = p.stage_detail(1)
    assert view is not None and view.stock_name == "삼성전자"
    assert view.rows[0].gap_kind == "TARGET"
    assert p.stage_detail(999) is None


def test_engine_error_reaches_the_banner():
    """설계서 18.1 리스크 6 — 조용히 죽은 엔진이 최악이다."""
    p = _presenter()
    p.consume(EngineStopped(detail="시세 재연결 3회 실패", at=AT))
    assert p.banner().engine_error is not None

    p2 = _presenter()
    p2.note_engine_error("RuntimeError: 복구 실패")
    assert "복구 실패" in p2.banner().engine_error


# ── 로그 뷰 (설계서 14.1절 [로그]) ──────────────────────────────────────
def test_order_unknown_and_rejected_are_different_kinds():
    """2B 핸드오버 4 — 같은 색으로 그리면 안 된다.

    문구만 담으면 위젯이 문자열을 검사하게 되고 그것은 사각지대의 로직이다.
    """
    p = _presenter()
    p.consume_all([
        OrderUnknown(config_id=1, cycle_id=2, stage_no=3, client_ref="abc",
                     at=AT),
        OrderRejected(config_id=1, cycle_id=2, stage_no=3, api_code="40510",
                      api_message="거부", at=AT),
    ])
    kinds = [line.kind for line in p.log_lines()]
    assert kinds == ["OrderUnknown", "OrderRejected"]
    assert len(set(kinds)) == 2


def test_severities_separate_warnings_from_information():
    p = _presenter()
    p.consume_all([
        StageFilled(config_id=1, cycle_id=2, stage_no=1, side="BUY",
                    fill_price=10_000, fill_qty=100, at=AT),
        GuardBlocked(config_id=1, stage_no=4, side="BUY",
                     reason="전체 총한도 초과: 누적 1 + 예상 2 > 한도 1", at=AT),
        OrderUnknown(config_id=1, cycle_id=2, stage_no=3, client_ref="a",
                     at=AT),
        CycleLoadFailed(config_id=1, cycle_id=2, detail="corrupt row",
                        action_taken="PAUSED", at=AT),
    ])
    by_kind = {line.kind: line.severity for line in p.log_lines()}
    assert by_kind["StageFilled"] == "INFO"
    assert by_kind["GuardBlocked"] == "INFO"
    assert by_kind["OrderUnknown"] == "WARN"
    assert by_kind["CycleLoadFailed"] == "ERROR"


def test_guard_reason_is_carried_verbatim():
    """2B 핸드오버 7 — 도메인이 만든 문장을 다시 쓰지 않는다.

    다시 쓰면 한도 숫자의 서식이 두 곳에 생기고 도메인 테스트가 고정한 문구와
    화면의 문구가 어긋난다.
    """
    reason = "종목 총한도 초과: 누적 1,000,000 + 예상 500,000 > 한도 1,200,000"
    p = _presenter()
    p.consume(GuardBlocked(config_id=1, stage_no=4, side="BUY", reason=reason,
                           at=AT))
    assert p.log_lines()[0].text == reason


def test_log_is_bounded():
    """장중 내내 돌면 로그가 메모리를 먹는다."""
    p = Presenter("mock", log_capacity=10)
    for i in range(50):
        p.consume(StageFilled(config_id=1, cycle_id=2, stage_no=1, side="BUY",
                              fill_price=10_000, fill_qty=100,
                              at=AT + timedelta(seconds=i)))
    assert len(p.log_lines()) == 10
    assert p.log_lines()[-1].at == AT + timedelta(seconds=49)


def test_snapshots_and_ticks_do_not_flood_the_log(three_row_snapshot):
    """스냅샷과 틱은 초당 여러 번 온다 — 로그에 넣으면 사용자가 읽을 수 없다."""
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(), _tick(price=9_400)])
    assert p.log_lines() == ()


# ── 설정 저장 피드백 ────────────────────────────────────────────────────
def test_config_feedback_is_taken_once():
    """다이얼로그가 한 번 읽고 지운다 — 남아 있으면 다음에 열 때 옛 오류가 뜬다."""
    p = _presenter()
    p.consume(ConfigRejected(config_id=None, detail="max_stages must be 2..7",
                             at=AT))
    feedback = p.take_config_feedback()
    assert feedback is not None and feedback.ok is False
    assert "max_stages" in feedback.detail
    assert p.take_config_feedback() is None


def test_config_saved_feedback_carries_the_id():
    p = _presenter()
    p.consume(ConfigSaved(config_id=7, at=AT))
    feedback = p.take_config_feedback()
    assert feedback.ok is True and feedback.config_id == 7


# ── 긴급청산·강제 종료 다이얼로그 ───────────────────────────────────────
def test_emergency_attempts_accumulate_from_events(three_row_snapshot):
    """설계서 11.4절 — `청산 시도 3회, 마지막 15:28`.

    그 이력은 emergency_liquidation_log 에 있고 GUI 는 DB 를 읽을 수 없으므로
    프레젠터가 이벤트에서 누적한다. 강제 종료는 청산 실패 직후에 하는 일이므로
    같은 세션이 정상 경로다.
    """
    p = _presenter()
    p.consume(three_row_snapshot)
    for i in range(3):
        p.consume(EmergencyResult(
            scope="SINGLE", stock_code="005930", result="FAILED",
            qty_before=316, qty_after=316, canceled_orders=0,
            detail="거래정지", at=AT + timedelta(minutes=i)))

    view = p.force_close(1)
    assert view.attempts == 3
    assert view.last_attempt_at == AT + timedelta(minutes=2)
    assert "거래정지" in view.last_failure_detail


def test_successful_liquidation_does_not_count_as_a_failed_attempt(
    three_row_snapshot,
):
    """성공한 청산을 시도 횟수에 넣으면 강제 종료 다이얼로그의 근거가 흐려진다."""
    p = _presenter()
    p.consume(three_row_snapshot)
    p.consume(EmergencyResult(scope="SINGLE", stock_code="005930",
                              result="SUCCESS", qty_before=316, qty_after=0,
                              canceled_orders=1, detail=None, at=AT))
    view = p.force_close(1)
    assert view.attempts == 0


def test_emergency_view_uses_the_latest_price(three_row_snapshot):
    p = _presenter()
    p.consume_all([three_row_snapshot, _tick(price=9_340)])
    view = p.emergency(1, scope="SINGLE")
    assert view.current_price == 9_340
    assert view.estimated_amount == 316 * 9_340


def test_dialogs_return_none_for_an_unknown_config(three_row_snapshot):
    p = _presenter()
    p.consume(three_row_snapshot)
    assert p.emergency(999, scope="SINGLE") is None
    assert p.force_close(999) is None


def test_dialogs_return_none_when_there_is_nothing_to_sell(three_row_snapshot):
    """뷰모델이 ValueError 를 내는 경우를 프레젠터가 None 으로 바꾼다 —
    위젯이 예외를 처리하게 하면 그 처리 코드가 사각지대에 들어간다."""
    p = _presenter()
    p.consume(three_row_snapshot)
    assert p.emergency(3, scope="SINGLE") is None      # NAVER, 보유 0
    assert p.force_close(3) is None
```

- [ ] **Step 2: 실패 확인 → 구현**

`src/autotrading7s/ui/presenter.py` — `tkinter` 를 import 하지 않는다. 상태:

```python
self._env = env
self._snapshot: Snapshot | None = None
self._prices: dict[str, int] = {}
self._mismatched: dict[str, ReconcileMismatch] = {}
self._last_reconcile: ReconcileMismatch | None = None
self._fallback_active = False
self._engine_error: str | None = None
self._log: deque[LogLine] = deque(maxlen=log_capacity)
self._config_feedback: ConfigFeedback | None = None
self._failed_attempts: dict[str, list[EmergencyResult]] = {}
```

`consume` 은 이벤트 타입별 분기다. 로그에 넣지 않는 것은 `Snapshot` 과 `TickUpdate` 뿐이다 — 둘은 초당 여러 번 오므로 사용자가 읽을 수 없다.

`_SEVERITY: dict[str, str]` 로 종류 → 심각도를 고정한다. `OrderUnknown`·`OrderRejected`·`QuoteFallback`(active) 는 `WARN`, `CycleLoadFailed`·`EngineStopped`·`ReconcileMismatch`(INTERNAL_MORE) 는 `ERROR`, 나머지는 `INFO`.

`ConfigFeedback` 은 `ok: bool`, `config_id: int | None`, `detail: str` 을 담는 frozen dataclass 다.

`emergency`·`force_close` 는 뷰모델의 `ValueError` 를 잡아 `None` 을 반환한다 — 위젯이 예외를 처리하게 하면 그 처리 코드가 EC2 에서 검증되지 않는 곳에 들어간다.

`force_close` 의 `attempts` 는 `_failed_attempts` 의 길이이며, `EmergencyResult.result` 가 `SUCCESS`·`FORCED_CLOSE` 인 경우는 넣지 않고 그 종목의 목록을 비운다 — 성공한 청산을 횟수에 넣으면 다이얼로그의 근거가 흐려진다.

- [ ] **Step 3~4: 통과 확인 → 커밋**

```bash
git add src/autotrading7s/ui/presenter.py tests/ui/test_presenter.py
git commit -m "$(printf 'feat: 프레젠터 — 이벤트 소비 상태기계\n\nGUI 로직의 전부가 여기 있다. 위젯은 프레젠터가 만든 뷰를 그리고 사용자 입력을\n명령으로 되돌리는 일만 한다 — tkinter 가 EC2 에 없으므로 그 경계가 검증 가능한\n것과 그렇지 않은 것을 가른다.\n\n대사 불일치 경고는 사용자나 사이클 종료가 지울 때까지 남는다. 대사는 일치할 때\n이벤트를 내지 않으므로(설계서 10.2절) 해소를 알 방법이 없고, 새 스냅샷이 온다고\n지우면 다음 대사까지 5분간 사용자가 아무 문제도 없다고 믿는다.\n\n로그 줄이 이벤트 종류를 그대로 담는다(2B 핸드오버 4). OrderUnknown 과\nOrderRejected 를 같은 색으로 그리면 안 되고, 위젯이 그 구분을 하려면 종류\n이름이 필요하다 — 문구만 담으면 위젯이 문자열을 검사하게 되고 그것은 사각지대의\n로직이다.\n\n스냅샷과 틱은 로그에 넣지 않는다 — 초당 여러 번 오므로 사용자가 읽을 수 없다.\n\n긴급청산 시도 이력을 EmergencyResult 에서 누적한다(설계서 11.4절). 그 이력은\nDB 에 있고 GUI 는 DB 를 읽을 수 없으며, 강제 종료는 청산 실패 직후에 하는\n일이므로 같은 세션이 정상 경로다. 성공한 청산은 횟수에 넣지 않는다.\n\n다이얼로그가 ValueError 대신 None 을 반환한다 — 위젯이 예외를 처리하게 하면\n그 처리 코드가 검증되지 않는 곳에 들어간다.')"
```

---

## Task 9: ASCII 렌더러 (`ui/text_render.py`)

**이 태스크가 이 계획의 가장 큰 이득이다.** 설계서 14.1·14.2절의 목업은 **이 계획의 사양이자 이미 존재하는 산출물**이다. 그것을 문자열로 렌더링하면 **레이아웃(열 폭, 정렬, 서식, 방향 기호)이 EC2 에서 테스트된다** — Tkinter 위젯은 같은 뷰모델의 값을 Treeview 열에 옮기는 일만 하므로, 여기서 맞으면 위젯에서 틀릴 여지가 열 매핑뿐이다.

부수 효과로 `cli.py` 가 headless 상태 화면을 갖게 된다 (Task 10).

**Ruling: 목업을 문자 단위로 재현하지 않는다.** 목업은 사람이 그린 것이라 열 폭이 일정하지 않고 유니코드 박스 문자의 폭 계산이 한글에서 어긋난다. 렌더러는 **같은 열, 같은 순서, 같은 서식 규칙**을 재현하고 테스트는 그것을 단정한다 — 정확한 공백 수를 단정하면 열 하나를 넓히는 것이 스무 개 테스트를 깨뜨리고, 그 테스트는 레이아웃이 아니라 자기 자신을 지킨다. 틀렸을 경우 비용: 목업과 픽셀 단위로 다르다 — 목업은 의도를 전달하는 문서이지 렌더링 명세가 아니다.

**Ruling: 한글 폭을 고려해 정렬한다.** `len("삼성전자") == 4` 지만 고정폭 터미널에서 8칸을 차지한다. `unicodedata.east_asian_width` 로 폭을 세는 헬퍼를 두고 모든 열이 그것을 쓴다 — 쓰지 않으면 종목명이 있는 행만 표가 어긋나고, 그것은 화면을 본 사람만 아는 결함이다.

**Files:**
- Create: `src/autotrading7s/ui/text_render.py`
- Test: `tests/ui/test_text_render.py`

**Interfaces:**
- Produces: `display_width(text) -> int`, `pad(text, width, *, align="left") -> str`, `format_won(value) -> str`, `format_pct(value) -> str`, `format_gap(row) -> str`, `render_holdings(view) -> str`, `render_stage_detail(view) -> str`, `render_ladder_preview(view) -> str`, `render_status_bar(view) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/ui/test_text_render.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from autotrading7s.ui.text_render import (
    display_width,
    format_gap,
    format_pct,
    format_won,
    pad,
    render_holdings,
    render_ladder_preview,
    render_stage_detail,
    render_status_bar,
)
from autotrading7s.ui.view_model import (
    build_holdings,
    build_ladder_preview,
    build_stage_detail,
    build_status_bar,
)

from .conftest import PCT, config, idle_config, snapshot


# ── 폭과 서식 ───────────────────────────────────────────────────────────
def test_display_width_counts_hangul_as_two_columns():
    """`len("삼성전자") == 4` 지만 고정폭 터미널에서 8칸을 차지한다.

    쓰지 않으면 종목명이 있는 행만 표가 어긋나고, 그것은 화면을 본 사람만
    아는 결함이다.
    """
    assert display_width("삼성전자") == 8
    assert display_width("NAVER") == 5
    assert display_width("카카오뱅크") == 10
    assert display_width("") == 0


def test_pad_uses_display_width_not_character_count():
    assert display_width(pad("삼성전자", 12)) == 12
    assert display_width(pad("NAVER", 12)) == 12
    assert pad("NAVER", 12).startswith("NAVER")
    assert pad("NAVER", 12, align="right").endswith("NAVER")


def test_pad_does_not_truncate_silently():
    """잘라내면 종목명이 조용히 사라진다 — 넘치는 것이 낫다."""
    assert pad("아주긴종목이름입니다", 4) == "아주긴종목이름입니다"


def test_format_won_uses_thousands_separators():
    assert format_won(9_971_350) == "9,971,350"
    assert format_won(-430_880) == "-430,880"
    assert format_won(0) == "0"
    assert format_won(None) == "-"


def test_format_pct_keeps_the_sign():
    assert format_pct(Decimal("-1.25")) == "-1.25%"
    assert format_pct(Decimal("12.4")) == "+12.4%"
    assert format_pct(None) == "-"


def test_format_gap_marks_direction_and_meaning():
    """설계서 14.1절 — 보유는 `▲ +12.4% (1,160원)`, 대기는 `▼ -9.0% 하락 시 매수`."""
    view = build_stage_detail(config(), current_price=9_340)
    holding = format_gap(view.rows[0])
    assert holding.startswith("▲")
    assert "+" in holding and "원)" in holding
    waiting = format_gap(view.rows[3])
    assert waiting.startswith("▼")
    assert "하락 시 매수" in waiting


def test_format_gap_is_a_dash_without_a_reference():
    view = build_stage_detail(config(), current_price=None)
    assert format_gap(view.rows[0]) == "-"


# ── 보유현황 표 (설계서 14.1절) ─────────────────────────────────────────
def test_holdings_render_has_a_row_per_config(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot,
        prices={"005930": 9_340, "035720": 7_910, "035420": 161_200},
        mismatched_codes=()))
    assert "삼성전자" in text and "카카오" in text and "NAVER" in text
    assert "005930" in text                 # 종목코드가 함께 보인다


def test_holdings_render_shows_stage_progress_and_status(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={"005930": 9_340},
        mismatched_codes=()))
    assert "3/7" in text
    assert "7/7" in text
    assert "0/5" in text
    assert "감시" in text and "소진" in text and "IDLE" in text


def test_holdings_render_includes_the_totals_line(three_row_snapshot):
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={"005930": 9_340, "035720": 7_910},
        mismatched_codes=()))
    assert "합계" in text
    assert "투입" in text and "평가" in text and "손익" in text


def test_holdings_render_warns_about_missing_prices(three_row_snapshot):
    """합계가 일부 종목만 반영한다는 사실이 보여야 한다."""
    text = render_holdings(build_holdings(
        three_row_snapshot, prices={"005930": 9_340}, mismatched_codes=()))
    assert "035720" in text
    assert "시세" in text or "제외" in text


def test_holdings_render_carries_the_broker_notice(three_row_snapshot):
    """설계서 2.1절 — 없으면 사용자가 증권사 앱과 비교하고 프로그램이 틀렸다고
    판단한다."""
    text = render_holdings(build_holdings(three_row_snapshot, prices={},
                                          mismatched_codes=()))
    assert "증권사" in text


def test_every_rendered_line_has_the_same_display_width(three_row_snapshot):
    """열이 어긋나면 표가 아니다. 한글 폭을 잘못 세면 정확히 그렇게 된다."""
    text = render_holdings(build_holdings(
        three_row_snapshot,
        prices={"005930": 9_340, "035720": 7_910, "035420": 161_200},
        mismatched_codes=()))
    widths = {display_width(line) for line in text.splitlines() if line.strip()}
    assert len(widths) == 1, f"행 폭이 어긋난다: {sorted(widths)}"


# ── 단계별 상세 (설계서 14.1절) ─────────────────────────────────────────
def test_stage_detail_render_has_a_row_per_stage():
    text = render_stage_detail(build_stage_detail(config(),
                                                   current_price=9_340))
    for n in range(1, 8):
        assert f" {n} " in text or f"{n}|" in text
    assert "삼성전자" in text
    assert "사이클 #2" in text
    assert "10,000" in text                 # 앵커


def test_stage_detail_render_aligns():
    text = render_stage_detail(build_stage_detail(config(),
                                                   current_price=9_340))
    widths = {display_width(line) for line in text.splitlines() if line.strip()}
    assert len(widths) == 1


def test_stage_detail_render_of_an_idle_config_says_so():
    """단계가 없으면 빈 표를 그리지 않고 이유를 말한다."""
    text = render_stage_detail(build_stage_detail(idle_config(),
                                                   current_price=161_200))
    assert "사이클" in text


# ── 사다리 미리보기 (설계서 14.2절) ─────────────────────────────────────
def test_ladder_preview_render_matches_the_mockup_columns():
    view = build_ladder_preview(anchor_price=9_340, max_stages=7,
                                drop_pct=PCT, target_pct=PCT,
                                amount_per_stage=1_000_000,
                                stock_limit=7_000_000)
    text = render_ladder_preview(view)
    for header in ("단계", "발동가", "수량", "투입금액", "목표가", "누적투입"):
        assert header in text
    assert "예상 총투입" in text
    assert "전단계 보유 시 평단" in text
    assert "앵커 대비" in text
    assert "1단계 체결가" in text            # ⓘ 문구


def test_ladder_preview_render_shows_headroom_or_excess():
    fits = render_ladder_preview(build_ladder_preview(
        anchor_price=9_340, max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, stock_limit=7_000_000))
    assert "여유" in fits

    over = render_ladder_preview(build_ladder_preview(
        anchor_price=9_340, max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, stock_limit=1_000_000))
    assert "초과" in over


def test_ladder_preview_render_aligns():
    text = render_ladder_preview(build_ladder_preview(
        anchor_price=9_340, max_stages=7, drop_pct=PCT, target_pct=PCT,
        amount_per_stage=1_000_000, stock_limit=7_000_000))
    widths = {display_width(line) for line in text.splitlines() if line.strip()}
    assert len(widths) == 1


# ── 상태바 ──────────────────────────────────────────────────────────────
def test_status_bar_render_shows_the_three_facts():
    text = render_status_bar(build_status_bar(
        fallback_active=False, last_reconcile=None, total_used=9_971_350,
        total_limit=21_000_000))
    assert "WebSocket" in text
    assert "대사" in text
    assert "9,971,350" in text and "21,000,000" in text
    assert "47.5%" in text
```

- [ ] **Step 2: 실패 확인 → 구현**

`src/autotrading7s/ui/text_render.py` — `tkinter` 를 import 하지 않는다.

```python
"""ASCII 렌더러 — 설계서 14.1·14.2절 목업의 텍스트 재현.

**이 모듈이 레이아웃을 EC2 에서 테스트 가능하게 만든다.** Tkinter 위젯은 같은
뷰모델의 값을 Treeview 열에 옮기는 일만 하므로, 열·순서·서식·방향 기호가 여기서
맞으면 위젯에서 틀릴 여지가 열 매핑뿐이다. `cli.py` 의 headless 상태 화면도 이것을
쓴다.

**목업을 문자 단위로 재현하지 않는다.** 목업은 사람이 그린 것이라 열 폭이 일정하지
않다. 정확한 공백 수를 단정하면 열 하나를 넓히는 것이 스무 개 테스트를 깨뜨리고,
그 테스트는 레이아웃이 아니라 자기 자신을 지킨다.

**한글 폭을 세야 한다.** `len("삼성전자") == 4` 지만 고정폭에서 8칸이다. 쓰지
않으면 종목명이 있는 행만 표가 어긋나고, 그것은 화면을 본 사람만 아는 결함이다.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal

from autotrading7s.ui.view_model import (
    HoldingsView,
    LadderPreview,
    StageDetailView,
    StageRowView,
    StatusBarView,
)


def display_width(text: str) -> int:
    """고정폭 터미널에서 차지하는 칸 수. 한글·전각 문자는 2 다."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def pad(text: str, width: int, *, align: str = "left") -> str:
    """표시 폭 기준으로 채운다. **넘치면 자르지 않는다** — 잘라내면 종목명이
    조용히 사라지고, 어긋난 행이 잘린 이름보다 낫다."""
    fill = max(0, width - display_width(text))
    return (" " * fill + text) if align == "right" else (text + " " * fill)


def format_won(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def format_pct(value: Decimal | None) -> str:
    """부호를 유지한다 — `+12.4%` 와 `12.4%` 는 사용자에게 다른 의미다."""
    if value is None:
        return "-"
    return f"{value:+}%" if value > 0 else f"{value}%"


def format_gap(row: StageRowView) -> str:
    """설계서 14.1절 "목표까지 / 매수까지" 열.

    보유는 `▲ +12.4% (1,160원)`, 대기는 `▼ -9.0% 하락 시 매수`.
    """
    if row.gap_kind is None or row.gap_pct is None or row.gap_won is None:
        return "-"
    arrow = "▲" if row.gap_won > 0 else "▼"
    if row.gap_kind == "TARGET":
        return f"{arrow} {format_pct(row.gap_pct)} ({format_won(row.gap_won)}원)"
    return f"{arrow} {format_pct(row.gap_pct)} 하락 시 매수"
```

`render_*` 함수는 열 폭을 상수로 두고 `pad` 로 조립한다. 각 함수는 **모든 행이 같은 표시 폭**을 갖도록 만들며(테스트가 그것을 단정한다), 마지막에 합계·안내 줄을 같은 폭으로 붙인다.

- `render_holdings` — 헤더 / 종목 행(2줄: 이름 + 코드) / 합계 / 시세 없는 종목 안내 / 증권사 안내
- `render_stage_detail` — 헤더(`단계별 상세 — 삼성전자 / 기본 (사이클 #2, 앵커 10,000원)`) / 열 머리 / 단계 행 7개. 단계가 없으면 "사이클이 없습니다" 한 줄
- `render_ladder_preview` — 열 머리 / 단계 행 / 총투입·한도·여유(또는 초과) / 최종단계 하락률 / 전단계 평단 / ⓘ 문구
- `render_status_bar` — `시세 WebSocket │ 대사 09:40 일치 │ 총한도 … (47.5%)`

- [ ] **Step 3~4: 통과 확인 → 커밋**

```bash
git add src/autotrading7s/ui/text_render.py tests/ui/test_text_render.py
git commit -m "$(printf 'feat: ASCII 렌더러 — 설계서 14.1·14.2절 목업의 텍스트 재현\n\n이것이 레이아웃을 EC2 에서 테스트 가능하게 만든다. Tkinter 위젯은 같은 뷰모델의\n값을 Treeview 열에 옮기는 일만 하므로, 열·순서·서식·방향 기호가 여기서 맞으면\n위젯에서 틀릴 여지가 열 매핑뿐이다.\n\n한글 폭을 센다. len("삼성전자") == 4 지만 고정폭에서 8칸이고, 쓰지 않으면 종목명이\n있는 행만 표가 어긋난다 — 화면을 본 사람만 아는 결함이다. 모든 행이 같은 표시\n폭을 갖는지 테스트가 단정한다.\n\n목업을 문자 단위로 재현하지 않는다. 정확한 공백 수를 단정하면 열 하나를 넓히는\n것이 스무 개 테스트를 깨뜨리고, 그 테스트는 레이아웃이 아니라 자기 자신을 지킨다.\n\npad 는 넘쳐도 자르지 않는다 — 잘라내면 종목명이 조용히 사라지고, 어긋난 행이\n잘린 이름보다 낫다.')"
```

---

## Task 10: headless 상태 화면과 의존 방향 게이트

**Ruling: `cli.py` 에 `--status` 를 둔다.** Task 9 의 렌더러가 있으므로 거의 무료이고, **프레젠터 사슬 전체가 EC2 에서 end-to-end 로 돌아간다** — 스냅샷 발행 → 프레젠터 소비 → 뷰모델 → 렌더러. 그 사슬이 Windows 에서 처음 돌면 어디가 틀렸는지 알기 어렵다. 틀렸을 경우 비용: `cli.py` 가 조금 커진다.

**Files:**
- Modify: `src/autotrading7s/cli.py`
- Create: `tests/test_g4_prep_gate.py`
- Test: `tests/app/test_cli.py` (수정)

- [ ] **Step 1: 게이트를 쓴다**

`tests/test_g4_prep_gate.py`:

```python
"""G4 준비 게이트 — GUI 층의 경계를 못 박는다.

설계서 14.4절의 규칙("ui/ 는 표시·입력 수집·큐 넣기만 한다")은 EC2 에
`tkinter` 가 없다는 사실 때문에 단순한 스타일 규칙이 아니다: **위젯으로 넘어간
로직은 자동 검증이 영원히 닿지 않는다.** 그러므로 두 경계를 테스트가 지킨다.

이 게이트가 통과해도 "화면이 제대로 그려지는가" 는 검증되지 않는다. 그것은
Windows 에서 사람이 확인해야 하며, 그 절차가
`docs/superpowers/records/2026-09-02-plan4-windows-checklist.md` 에 있다.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path("src/autotrading7s")
PURE_UI = ("view_model.py", "presenter.py", "text_render.py")


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names.add(node.module or "")
    return names


@pytest.mark.parametrize("name", PURE_UI)
def test_pure_ui_modules_do_not_import_tkinter(name):
    """EC2 에 tkinter 가 아예 없다 — import 하는 순간 이 모듈이 테스트 밖으로 나간다."""
    imported = _imports(ROOT / "ui" / name)
    assert not any(m == "tkinter" or m.startswith("tkinter.")
                   for m in imported), f"{name} 이 tkinter 를 import 한다"


@pytest.mark.parametrize("name", PURE_UI)
def test_pure_ui_modules_do_not_touch_the_database(name):
    """설계서 14.4절 — ui/ 는 DB 를 건드리지 않는다.

    그 규칙이 리포지토리의 단일 작성자 전제를 성립시킨다 (2A 핸드오버 3).
    """
    imported = _imports(ROOT / "ui" / name)
    assert "sqlite3" not in imported
    assert not any("adapters" in m for m in imported)


def test_widget_modules_do_not_import_domain_or_engine():
    """위젯에 계산이 들어가면 그 계산은 영원히 사각지대다.

    `app` 과 `ui.view_model`·`ui.presenter` 만 쓴다 — 계산이 필요하면 뷰모델에
    함수를 추가해야 하고, 그러면 그 함수가 EC2 에서 테스트된다.
    """
    forbidden = ("autotrading7s.domain", "autotrading7s.engine",
                 "autotrading7s.ports", "autotrading7s.adapters")
    offenders: list[str] = []
    for path in (ROOT / "ui" / "widgets").rglob("*.py"):
        for module in _imports(path):
            if any(module.startswith(f) for f in forbidden):
                offenders.append(f"{path.name}: {module}")
    assert offenders == []


def test_widget_modules_exist():
    """설계서 7.2절이 나열한 여섯 화면이 모두 있어야 한다."""
    expected = {"main_window.py", "holdings_table.py", "stage_detail.py",
                "config_dialog.py", "emergency_dialog.py", "log_view.py"}
    present = {p.name for p in (ROOT / "ui" / "widgets").glob("*.py")}
    assert expected <= present


def test_engine_and_app_still_do_not_import_ui():
    """의존 방향은 안쪽을 향한다 — 엔진이 화면을 알면 안 된다."""
    offenders: list[str] = []
    for sub in ("engine", "app", "domain", "ports"):
        for path in (ROOT / sub).rglob("*.py"):
            for module in _imports(path):
                if module.startswith("autotrading7s.ui"):
                    offenders.append(f"{path}: {module}")
    assert offenders == []


def test_the_pure_layer_is_importable_without_tkinter():
    """이 테스트가 통과하는 것 자체가 증거다 — EC2 에 tkinter 가 없으므로,
    순수 층이 그것을 끌어들이면 이 import 가 실패한다."""
    import importlib

    for name in ("view_model", "presenter", "text_render"):
        importlib.import_module(f"autotrading7s.ui.{name}")
```

- [ ] **Step 2: `cli.py --status` 를 구현한다**

`--status` 를 주면 스냅샷마다 `render_holdings` + `render_stage_detail` + `render_status_bar` 를 표준출력에 찍는다. 프레젠터가 이벤트를 소비하므로 `event_q` 를 비우는 소비자가 필요하다 — `Orchestrator.run()` 과 나란히 도는 태스크로 둔다.

```python
    async def run() -> None:
        presenter = Presenter(args.env)
        stop = asyncio.Event()

        async def drain() -> None:
            """이벤트를 프레젠터에 먹이고 스냅샷마다 화면을 찍는다."""
            while not stop.is_set():
                drew = False
                while True:
                    try:
                        event = event_q.get_nowait()
                    except queue.Empty:
                        break
                    presenter.consume(event)
                    drew = drew or isinstance(event, Snapshot)
                if drew and args.status:
                    print(render_holdings(presenter.holdings()))
                    print(render_status_bar(presenter.status_bar()))
                await asyncio.sleep(0)

        await Recovery(...).run()
        drainer = asyncio.create_task(drain())
        try:
            await Orchestrator(...).run()
        finally:
            stop.set()
            await drainer
```

`await asyncio.sleep(0)` 은 양보만 하므로 실제로 잠들지 않는다 — 이 파일은 `app` 층이므로 오케스트레이터의 "주입된 sleep" 규칙이 적용되지 않는다. 그 이유를 주석으로 남긴다.

`tests/app/test_cli.py` 에 추가한다.

```python
def test_status_mode_prints_the_holdings_table(tmp_path, capsys):
    """프레젠터 사슬 전체가 EC2 에서 end-to-end 로 돈다 —
    스냅샷 발행 → 프레젠터 소비 → 뷰모델 → 렌더러."""
    settings = tmp_path / "settings.toml"
    settings.write_text("[engine]\ntotal_limit = 100000000\n",
                        encoding="utf-8")
    db = tmp_path / "cli.db"
    code = cli.main(["--env", "mock", "--settings", str(settings),
                     "--db", str(db), "--simulate", "10000,9500", "--status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "보유현황" in out or "합계" in out
    assert "총한도" in out
```

- [ ] **Step 3~5: 통과 확인 → 전체 회귀 → 커밋**

```bash
git add src/autotrading7s/cli.py tests/test_g4_prep_gate.py tests/app/test_cli.py
git commit -m "$(printf 'feat: headless 상태 화면과 GUI 층 의존 방향 게이트\n\ncli --status 가 프레젠터 사슬 전체를 EC2 에서 end-to-end 로 돌린다 — 스냅샷\n발행 → 프레젠터 소비 → 뷰모델 → 렌더러. 그 사슬이 Windows 에서 처음 돌면\n어디가 틀렸는지 알기 어렵다.\n\n게이트가 두 경계를 못 박는다. 순수 층은 tkinter 와 DB 를 import 하지 않고,\n위젯 층은 domain·engine·ports·adapters 를 import 하지 않는다. 설계서 14.4절의\n규칙은 EC2 에 tkinter 가 없다는 사실 때문에 스타일 규칙이 아니다 — 위젯으로\n넘어간 로직은 자동 검증이 영원히 닿지 않는다.\n\n순수 층이 tkinter 없이 import 되는지 확인하는 테스트를 뒀다. 그것이 통과하는\n것 자체가 증거다 — EC2 에 tkinter 가 없으므로 끌어들이면 import 가 실패한다.')"
```

---

## Task 11: Tkinter 셸 (`ui/widgets/`, `__main__.py`)

**이 태스크의 코드는 EC2 에서 검증되지 않는다.** import 조차 되지 않는다. 그러므로 **로직을 한 줄도 두지 않는 것**이 유일한 방어이며, Task 10 의 게이트가 그것을 강제한다. 여기서 하는 일은 세 가지뿐이다:

1. 뷰모델이 만든 값을 위젯에 옮긴다
2. 사용자 입력을 `parse_config_form` 에 넘기고 그 결과로 명령을 만든다
3. `root.after(200ms)` 로 이벤트를 비우고 다시 그린다

**Ruling: `_pump` 가 `raise_if_failed()` 를 매번 확인한다.** 엔진 스레드가 예외로 죽으면 아무도 보지 못하고, **조용히 죽은 엔진은 "프로그램이 켜져 있는데 트리거를 놓치는" 최악의 상태다** (설계서 18.1 리스크 6). 200ms 마다 확인하는 비용은 `None` 비교 하나다.

**Ruling: `python -m autotrading7s` 가 GUI, `python -m autotrading7s.cli` 가 headless 다** (설계서 16절). `__main__.py` 를 둔다.

**Files:**
- Create: `src/autotrading7s/ui/widgets/__init__.py`, `main_window.py`, `holdings_table.py`, `stage_detail.py`, `config_dialog.py`, `emergency_dialog.py`, `log_view.py`, `src/autotrading7s/__main__.py`
- Test: 없다 — Task 10 의 게이트가 경계만 지킨다. **이것이 이 태스크의 성질이며 완료 보고에 명시한다.**

- [ ] **Step 1: `main_window.py` — 펌프와 조립**

```python
"""메인 윈도우 — 설계서 14.1절.

**이 파일은 EC2 에서 import 되지 않는다** (tkinter 부재). 그러므로 로직을 한 줄도
두지 않는다 — 계산이 필요하면 `ui/view_model.py` 에 함수를 추가하고 그것을
호출한다. `tests/test_g4_prep_gate.py` 가 그 규칙을 강제한다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from autotrading7s.app.commands import (
    EmergencyLiquidate,
    ForceClose,
    PauseCycle,
    ResetReconcileBaseline,
    ResumeCycle,
    SaveConfig,
    Shutdown,
    StartCycle,
)
from autotrading7s.app.engine_thread import EngineThread
from autotrading7s.ui.presenter import Presenter
from autotrading7s.ui.widgets.config_dialog import ConfigDialog
from autotrading7s.ui.widgets.emergency_dialog import (
    EmergencyDialog,
    ForceCloseDialog,
)
from autotrading7s.ui.widgets.holdings_table import HoldingsTable
from autotrading7s.ui.widgets.log_view import LogView
from autotrading7s.ui.widgets.stage_detail import StageDetailTable

PUMP_MS = 200


class MainWindow:
    def __init__(self, *, thread: EngineThread, presenter: Presenter,
                 env: str) -> None:
        self._thread = thread
        self._presenter = presenter
        self.root = tk.Tk()
        self.root.title("AutoTrading 7s")
        self._selected: int | None = None

        banner = presenter.banner()
        self._banner = ttk.Label(
            self.root, text=f"{banner.env_label}   {banner.connection_label}",
            foreground="red" if banner.is_real else "black")
        self._banner.pack(fill="x")

        bar = ttk.Frame(self.root)
        bar.pack(fill="x")
        ttk.Button(bar, text="설정관리",
                   command=self._open_config).pack(side="left")
        ttk.Button(bar, text="시작", command=self._start).pack(side="left")
        ttk.Button(bar, text="일시정지", command=self._pause).pack(side="left")
        ttk.Button(bar, text="재개", command=self._resume).pack(side="left")
        ttk.Button(bar, text="긴급청산",
                   command=self._emergency).pack(side="left")
        ttk.Button(bar, text="강제 종료",
                   command=self._force_close).pack(side="left")
        # 2B 핸드오버 8 — 기준선 초기화의 UI 입구
        ttk.Button(bar, text="대사 기준선 초기화",
                   command=self._reset_baseline).pack(side="left")

        self._holdings = HoldingsTable(self.root, on_select=self._on_select)
        self._stages = StageDetailTable(self.root)
        self._log = LogView(self.root)
        self._status = ttk.Label(self.root, text="")
        self._status.pack(fill="x")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(PUMP_MS, self._pump)

    # ── 펌프 ────────────────────────────────────────────────────────────
    def _pump(self) -> None:
        self._presenter.consume_all(self._thread.drain_events())
        # 조용히 죽은 엔진은 "프로그램이 켜져 있는데 트리거를 놓치는" 최악의
        # 상태다 (설계서 18.1 리스크 6). 확인 비용은 None 비교 하나다.
        try:
            self._thread.raise_if_failed()
        except BaseException as exc:                      # noqa: BLE001
            self._presenter.note_engine_error(f"{type(exc).__name__}: {exc}")
        self._refresh()
        self.root.after(PUMP_MS, self._pump)

    def _refresh(self) -> None:
        banner = self._presenter.banner()
        text = f"{banner.env_label}   {banner.connection_label}"
        if banner.engine_error is not None:
            text += f"   ⚠ 엔진 정지: {banner.engine_error}"
        self._banner.configure(
            text=text,
            foreground="red" if banner.is_real or banner.engine_error else
            "black")
        self._holdings.render(self._presenter.holdings())
        if self._selected is not None:
            detail = self._presenter.stage_detail(self._selected)
            if detail is not None:
                self._stages.render(detail)
        self._log.render(self._presenter.log_lines())
        bar = self._presenter.status_bar()
        self._status.configure(
            text=f"{bar.quote_source_label} │ {bar.last_reconcile_label} │ "
                 f"총한도 {bar.total_used:,} / {bar.total_limit:,}")

    def _on_select(self, config_id: int) -> None:
        self._selected = config_id
        self._refresh()

    # ── 명령 ────────────────────────────────────────────────────────────
    def _start(self) -> None:
        if self._selected is not None:
            self._thread.send(StartCycle(config_id=self._selected))

    def _pause(self) -> None:
        if self._selected is not None:
            self._thread.send(PauseCycle(config_id=self._selected))

    def _resume(self) -> None:
        if self._selected is not None:
            self._thread.send(ResumeCycle(config_id=self._selected))

    def _reset_baseline(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self._thread.send(ResetReconcileBaseline(stock_code=row.stock_code))
        # 대사는 일치할 때 이벤트를 내지 않으므로 프레젠터가 해소를 알 수 없다.
        self._presenter.clear_mismatch(row.stock_code)

    def _selected_row(self):
        if self._selected is None:
            return None
        for row in self._presenter.holdings().rows:
            if row.config_id == self._selected:
                return row
        return None

    def _emergency(self) -> None:
        if self._selected is None:
            return
        view = self._presenter.emergency(self._selected, scope="SINGLE")
        if view is None:
            return
        result = EmergencyDialog(self.root, view).show()
        if result is not None:
            self._thread.send_priority(EmergencyLiquidate(
                scope="SINGLE", config_id=self._selected, reason=result.reason,
                confirmed_text=result.confirmed_text))

    def _force_close(self) -> None:
        if self._selected is None:
            return
        view = self._presenter.force_close(self._selected)
        if view is None:
            return
        result = ForceCloseDialog(self.root, view).show()
        if result is not None:
            self._thread.send_priority(ForceClose(
                config_id=self._selected, reason=result.reason,
                confirmed_text=result.confirmed_text))

    def _open_config(self) -> None:
        fields = ConfigDialog(self.root, self._presenter).show()
        if fields is not None:
            self._thread.send(SaveConfig(config_id=None, **fields))

    def _on_close(self) -> None:
        self._thread.send(Shutdown())
        self._thread.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
```

- [ ] **Step 2: 표 위젯 세 개**

`holdings_table.py`·`stage_detail.py`·`log_view.py` 는 같은 모양이다: `ttk.Treeview` 를 만들고 `render(view)` 에서 전부 지우고 다시 넣는다. **부분 갱신을 하지 않는다** — 어느 행이 바뀌었는지 계산하는 것이 로직이고, 그 로직은 사각지대에 들어간다. 200ms 마다 수십 행을 다시 그리는 비용은 무해하다.

`log_view.py` 는 `line.kind` 로 태그를 정한다 — **`OrderUnknown` 과 `OrderRejected` 가 서로 다른 태그를 받아야 한다** (2B 핸드오버 4).

```python
_TAGS = {"ERROR": {"foreground": "red"},
         "WARN": {"foreground": "darkorange"},
         "INFO": {"foreground": "black"}}
_KIND_TAGS = {"OrderUnknown": {"foreground": "blue"}}   # "확인 중" 은 실패가 아니다
```

- [ ] **Step 3: 다이얼로그 두 파일**

`config_dialog.py` — 폼 입력마다 `parse_config_form` 을 시도해 성공하면 `build_ladder_preview` + `render_ladder_preview` 로 미리보기를 갱신하고, `FormError` 면 그 메시지를 미리보기 영역에 표시한다. [저장]은 파싱 결과를 반환한다. `presenter.take_config_feedback()` 으로 엔진의 거부를 표시한다.

`emergency_dialog.py` — `EmergencyDialog` 는 `view.required_text` 가 `None` 이 아니면 그 문자열이 정확히 입력될 때만 [실행]을 활성화한다. `ForceCloseDialog` 는 `required_text` 와 **비어 있지 않은 사유**를 둘 다 요구한다 (설계서 11.4절). 두 다이얼로그가 `required_text` 를 자기가 정하지 않고 뷰에서 받는 것이 핵심이다 — 다시 쓰면 사용자가 정확히 입력했는데 버튼이 활성화되지 않는다.

- [ ] **Step 4: `__main__.py`**

```python
"""GUI 기동 — 설계서 16절 `python -m autotrading7s`.

headless 는 `python -m autotrading7s.cli` 다.
"""

from __future__ import annotations

import argparse
import queue
import sys
from datetime import UTC, datetime
from pathlib import Path

from autotrading7s.adapters.sqlite.migrations import apply_schema, connect
from autotrading7s.adapters.sqlite.repository import SqliteRepository
from autotrading7s.app.engine_thread import EngineThread
from autotrading7s.app.settings import load_settings
from autotrading7s.cli import db_path_for
from autotrading7s.engine.orchestrator import Orchestrator
from autotrading7s.engine.recovery import Recovery
from autotrading7s.ui.presenter import Presenter
from autotrading7s.ui.widgets.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autotrading7s")
    parser.add_argument("--env", choices=("mock", "real"), required=True)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--simulate", default=None,
                        help="쉼표로 구분한 가격 스크립트 (키움 어댑터 부재 시)")
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    db = db_path_for(args.env)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    apply_schema(conn)
    repo = SqliteRepository(conn)

    if args.simulate is None:
        print("키움 어댑터가 아직 구현되지 않았습니다 (Plan 3). 지금은 "
              "--simulate 로 시뮬레이션 브로커만 기동할 수 있습니다.",
              file=sys.stderr)
        return 2

    from autotrading7s.adapters.fake.broker import FakeBroker
    from autotrading7s.adapters.fake.clock import FakeClock

    broker = FakeBroker([int(p) for p in args.simulate.split(",")],
                        validate_account=True)
    clock = FakeClock(current=datetime.now(UTC))
    thread = EngineThread(
        orchestrator_factory=lambda **qs: Orchestrator(
            repo=repo, broker=broker, clock=clock, settings=settings,
            max_fallback_rounds=3, **qs),
        # `event_q` 를 받는 것이 중요하다 — 아래 EngineThread 변경 참조.
        recovery_factory=lambda **qs: Recovery(
            repo=repo, broker=broker, clock=clock, emit=qs["event_q"].put),
    )
    thread.start()
    MainWindow(thread=thread, presenter=Presenter(args.env),
               env=args.env).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**`EngineThread` 를 함께 고친다.** 지금 `_main` 은 `self._recovery_factory()` 를 인자 없이 부르므로 복구가 `event_q` 를 알 수 없고, 그러면 **복구 중의 `CycleLoadFailed`·`ReconcileMismatch` 가 화면에 도달하지 않는다** — 기동 직후가 그 두 이벤트가 가장 나올 만한 시점이므로 정확히 필요한 순간에 조용하다.

```python
    async def _main(self) -> None:
        # 복구도 이벤트를 낸다 — 기동 직후가 CycleLoadFailed·ReconcileMismatch
        # 가 가장 나올 만한 시점이므로 event_q 를 넘겨야 한다.
        await self._recovery_factory(event_q=self.event_q).run()
        self._orchestrator = self._orchestrator_factory(
            command_q=self.command_q, priority_q=self.priority_q,
            event_q=self.event_q,
        )
        await self._orchestrator.run()
```

`tests/app/test_engine_thread.py` 의 `_Recovery` 를 `def __init__(self, *, event_q=None)` 으로 고치고, `recovery_factory=_Recovery` 를 `recovery_factory=lambda **kw: _Recovery(**kw)` 로 바꾼다. **복구가 낸 이벤트가 `drain_events()` 에 나타나는지 확인하는 테스트를 추가한다** — 그것이 이 변경의 요점이다.

- [ ] **Step 5: 게이트 확인 → 커밋**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. 위젯 파일은 import 되지 않으므로 테스트 수가 늘지 않고, `test_g4_prep_gate.py` 가 파일 존재와 import 규칙만 확인한다.

```bash
git add src/autotrading7s/ui/widgets src/autotrading7s/__main__.py src/autotrading7s/app/engine_thread.py tests/app/test_engine_thread.py
git commit -m "$(printf 'feat: Tkinter 셸과 GUI 기동 (설계서 14절·16절)\n\n**이 커밋의 위젯 코드는 EC2 에서 검증되지 않는다** — tkinter 가 없어 import 조차\n되지 않는다. 그러므로 로직을 한 줄도 두지 않는 것이 유일한 방어이며, G4 준비\n게이트가 그 경계를 강제한다.\n\n_pump 가 200ms 마다 raise_if_failed 를 확인한다. 조용히 죽은 엔진은 프로그램이\n켜져 있는데 트리거를 놓치는 최악의 상태다(설계서 18.1 리스크 6).\n\n표 위젯은 부분 갱신을 하지 않는다 — 어느 행이 바뀌었는지 계산하는 것이 로직이고\n그 로직은 사각지대에 들어간다. 200ms 마다 수십 행을 다시 그리는 비용은 무해하다.\n\n로그 뷰가 OrderUnknown 에 별도 태그를 준다(2B 핸드오버 4). "확인 중" 은 실패가\n아니고, 같은 색으로 그리면 사용자가 개입 시점을 알 수 없다.\n\n다이얼로그가 확인 문자열을 자기가 정하지 않고 뷰에서 받는다 — 다시 쓰면 사용자가\n정확히 입력했는데 버튼이 활성화되지 않는다.\n\nRecovery 의 이벤트를 event_q 로 넘기도록 EngineThread 를 고쳤다. 그러지 않으면\n복구 중의 CycleLoadFailed·ReconcileMismatch 가 화면에 도달하지 않는다.')"
```

---

## Task 12: Windows 수동 검증 체크리스트와 README

**이 태스크가 이 계획의 완료를 정직하게 만든다.** EC2 에서 검증할 수 있는 것은 전부 검증했지만 "화면이 제대로 그려지는가" 는 검증되지 않았다. 그 사실을 문서로 남기고, 사용자가 Windows 에서 확인할 절차를 준다 — 그것이 G3(모의투자 검증)의 선행 절차다.

**Files:**
- Create: `docs/superpowers/records/2026-09-02-plan4-windows-checklist.md`
- Modify: `README.md`

- [ ] **Step 1: 체크리스트를 쓴다**

`docs/superpowers/records/2026-09-02-plan4-windows-checklist.md` 는 다음을 담는다.

**기동**
```
> python -m venv .venv & .venv\Scripts\activate
> pip install -e ".[dev]"
> python -m autotrading7s --env mock --settings settings.toml --simulate 10000,9500,9000,9450,9980,10500
```

**확인 항목** — 각각 "무엇을 보아야 하는가" 와 "틀렸다면 어디를 의심하는가" 를 짝지어 적는다.

| # | 확인 | 틀렸다면 |
|---|---|---|
| 1 | 창이 열리고 상단에 `▣ 모의투자` 가 검은색으로 보인다 | `build_banner` 는 테스트됨 → `main_window._refresh` 의 색 지정 |
| 2 | 보유현황 표에 설정이 행으로 보이고, 행을 클릭하면 아래 단계별 상세가 바뀐다 | `HoldingsTable.on_select` 배선 |
| 3 | 시세가 흐르면 현재가·평가손익 열이 갱신된다 | `_pump` 가 `drain_events` 를 부르는지 |
| 4 | "목표까지 / 매수까지" 열에 ▲▼ 가 보이고 보유 행과 대기 행이 구분된다 | `format_gap` 은 테스트됨 → Treeview 열 매핑 |
| 5 | [설정관리]에서 값을 입력하는 동안 사다리 미리보기가 갱신된다 | `ConfigDialog` 의 입력 이벤트 바인딩 |
| 6 | 잘못된 값(`abc`)을 넣으면 미리보기 영역에 필드 이름이 든 오류가 보인다 | `FormError` 는 테스트됨 → 표시 위치 |
| 7 | [저장] 후 표에 새 설정이 나타난다 | `ConfigSaved` → 스냅샷 발행 → `_refresh` |
| 8 | 한도를 넘는 설정에서 미리보기가 "초과" 를 보여준다 | `render_ladder_preview` 는 테스트됨 |
| 9 | [긴급청산]에서 보유수량·예상금액·취소될 미체결 건수가 보인다 | `build_emergency_view` 는 테스트됨 |
| 10 | 전체 청산 다이얼로그에서 `전체청산` 을 정확히 입력해야 버튼이 활성화된다 | 상수를 뷰에서 받는지 (직접 쓰면 어긋난다) |
| 11 | [강제 종료]는 `LIQUIDATING` 이 아닌 사이클에서 다이얼로그가 뜨지 않는다 | `presenter.force_close` 가 `None` 을 반환 |
| 12 | 로그 뷰에서 `OrderUnknown` 과 `OrderRejected` 의 색이 다르다 | `log_view._KIND_TAGS` |
| 13 | 엔진 스레드를 강제로 죽였을 때(테스트용) 배너에 붉은 경고가 뜬다 | `_pump` 의 `raise_if_failed` |
| 14 | 창을 닫으면 프로세스가 남지 않는다 | `_on_close` 의 `Shutdown` + `stop()` |
| 15 | 표의 열이 어긋나지 않는다 (한글 종목명 포함) | Treeview 는 폭을 스스로 계산하므로 무관 — 어긋나면 `width` 설정 |

**비교 기준**: `python -m autotrading7s.cli --env mock --settings settings.toml --simulate ... --status` 를 같은 스크립트로 돌려 나오는 ASCII 표와 화면의 숫자가 **일치해야 한다.** 두 경로가 같은 뷰모델을 쓰므로 숫자가 다르면 위젯의 열 매핑이 틀린 것이다. **이것이 이 체크리스트의 가장 강한 항목이다** — 사람의 눈이 아니라 두 렌더러의 대조가 판정한다.

- [ ] **Step 2: README 를 갱신한다**

```markdown
**Plan 4 (GUI) 완료 — 단, 화면 렌더링은 Windows 에서 확인해야 한다.**
`ui/` 가 순수 뷰모델(EC2 에서 전수 테스트)과 얇은 Tkinter 셸(검증 불가)로
나뉘어 있다. EC2 에는 `tkinter` 가 설치되어 있지 않아 위젯 파일은 import 조차
되지 않으므로, 그 경계를 `tests/test_g4_prep_gate.py` 가 강제한다 — 순수 층은
`tkinter`·DB 를 import 하지 않고, 위젯 층은 `domain`·`engine`·`ports`·
`adapters` 를 import 하지 않는다.

`python -m autotrading7s.cli --status` 로 같은 뷰모델의 ASCII 표를 headless 로
볼 수 있다. **Windows 검증 절차:**
`docs/superpowers/records/2026-09-02-plan4-windows-checklist.md`

미구현: 키움 어댑터(Plan 3).
```

- [ ] **Step 3: 커밋**

```bash
git add docs/superpowers/records/2026-09-02-plan4-windows-checklist.md README.md
git commit -m "$(printf 'docs: Windows 수동 검증 체크리스트\n\nEC2 에서 검증할 수 있는 것은 전부 검증했지만 "화면이 제대로 그려지는가" 는\n검증되지 않았다. 그 사실을 문서로 남기고 사용자가 Windows 에서 확인할 절차를\n준다 — 그것이 G3 의 선행 절차다.\n\n체크리스트의 가장 강한 항목은 사람의 눈이 아니다: cli --status 로 같은 스크립트를\n돌려 나오는 ASCII 표와 화면의 숫자를 대조한다. 두 경로가 같은 뷰모델을 쓰므로\n숫자가 다르면 위젯의 열 매핑이 틀린 것이다.\n\n각 항목에 "틀렸다면 어디를 의심하는가" 를 짝지었다 — 뷰모델은 테스트됐으므로\n대부분의 원인은 배선이다.')"
```

---

## G4 준비 게이트 통과 기준

Plan 4 완료 시 다음이 모두 통과해야 한다.

- [ ] 스냅샷이 `IDLE` 설정을 포함해 모든 설정을 담고 `config_id` 를 실어 보낸다
- [ ] 스냅샷이 상태가 변할 때만 발행된다 (유휴 틱에 큐가 자라지 않는다)
- [ ] 손상된 사이클도 스냅샷에 보인다 (`stages=()`)
- [ ] 설정 수정이 `IDLE` 설정만 대상이고, `ACTIVE` 는 거부된다
- [ ] 저장 거부가 `ConfigRejected` 로 사용자에게 돌아온다
- [ ] 보유현황의 수량·평단·평가손익이 전부 `domain/pnl.py` 의 값과 같다
- [ ] 상태 표기 여섯 가지가 모두 나오고 `⚠불일치` 가 나머지를 덮는다
- [ ] 가격이 없는 종목이 합계에서 제외되고 그 사실이 함께 나온다
- [ ] "목표까지 / 매수까지" 가 한 열에서 두 의미를 담고, 설계서 목업의 숫자와 일치한다
- [ ] 사다리 미리보기가 `Ladder` 와 같은 값을 내고, 한도 초과를 표시한다
- [ ] `parse_config_form` 이 `NaN`·`Infinity`·천 단위 쉼표를 처리하고 필드 이름이 든 오류를 낸다
- [ ] 파싱 결과를 그대로 `SaveConfig(**parsed)` 에 넘길 수 있다
- [ ] 확인 문자열이 `app/commands.py` 의 상수와 같다
- [ ] 프레젠터가 불일치 경고를 사용자·사이클 종료가 지울 때까지 유지한다
- [ ] 로그가 `OrderUnknown` 과 `OrderRejected` 를 다른 종류로 구분한다
- [ ] 로그가 유한하고, 스냅샷·틱이 로그를 채우지 않는다
- [ ] ASCII 렌더러의 모든 행이 같은 표시 폭을 갖는다 (한글 포함)
- [ ] `cli --status` 가 프레젠터 사슬 전체를 end-to-end 로 돌린다
- [ ] 순수 층이 `tkinter`·DB 를 import 하지 않는다
- [ ] 위젯 층이 `domain`·`engine`·`ports`·`adapters` 를 import 하지 않는다
- [ ] `engine`·`app`·`domain`·`ports` 가 `ui` 를 import 하지 않는다
- [ ] 설계서 7.2절이 나열한 여섯 위젯 파일이 존재한다
- [ ] 전체 커버리지 95% 이상 (위젯 파일은 import 되지 않아 집계에 들어가지 않는다 — 그 사실을 보고에 명시한다)

**이 게이트가 통과해도 화면 렌더링은 검증되지 않는다.** Windows 체크리스트가 남은 절반이다.

---

## Plan 4 이후

**Plan 3 (키움 어댑터 + 인증)** 만 남는다. 핸드오버는 `docs/superpowers/records/2026-09-02-plan2b-handover-to-3-and-4.md` 의 Plan 3 절 9건이며, 가장 먼저 확인할 것은 **키움 API 가 `client_ref` 를 에코하는지**다 — 설계서 9절 ⑤의 UNKNOWN 분기가 그 대조로만 접수 여부를 확인하므로, 에코가 안 되면 D12(중복 발주 금지)를 어떻게 지킬지가 첫 설계 문제가 된다.

Plan 4 가 Plan 3 에 넘기는 것:

1. **종목명 조회([조회] 버튼)가 비어 있다.** `stock_name` 을 사용자가 직접 입력하게 두었다. 브로커 조회가 붙으면 `ConfigDialog` 에 그 버튼을 배선하고, 그 경로도 명령·이벤트를 거쳐야 한다(GUI 는 브로커를 직접 부르지 않는다).
2. **`--simulate` 가 GUI 기동의 유일한 경로다.** `__main__.py` 의 브로커 조립을 키움 어댑터로 바꿀 때 `--env real` 의 경고를 함께 걷어낸다.
3. **`FakeClock` 이 GUI 기동에 쓰인다.** `__main__.py` 가 `FakeClock(current=datetime.now(UTC))` 를 쓰므로 **시계가 흐르지 않는다** — 재매수 쿨다운과 미체결 타임아웃이 실제로 동작하지 않는다. `KiwoomClock`(설계서 18.2절, 구현 2단계)이 그것을 대체해야 하며, 그때까지 GUI 기동은 화면 확인용이다. **이 제약을 Windows 체크리스트에도 적는다.**
