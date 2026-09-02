# SDD ledger — plan: docs/superpowers/plans/2026-09-02-autotrading-7s-persistence.md

Spec: docs/superpowers/specs/2026-09-01-autotrading-7s-design.md (읽음 — 바인딩 권위)
Branch: feat/persistence (master a45b13e 에서 분기)
Python: .venv/bin/python (3.12). 테스트: `.venv/bin/python -m pytest`

## Ruling: 워크트리 대신 브랜치 격리
Plan 1 과 같은 방식으로 `feat/persistence` 브랜치만 만들고 별도 워크트리는 쓰지
않는다 — `.venv/` 가 저장소 루트에 있고 gitignore 되어 있어서, 새 워크트리에는
인터프리터가 없어 모든 태스크가 첫 스텝에서 막힌다.
틀렸을 경우 비용: master 오염 위험이 워크트리보다 한 단계 높다. 다만 master 는
계획서·설계서만 있고 병합은 사람이 결정하므로 실질 위험은 낮다.

---

# 사전 스캔 (Task 1 디스패치 전)

## A. 파일/인터페이스를 공유하는 태스크 쌍

| 쌍 | 무엇을 생산 → 무엇을 소비 | 발견 |
|---|---|---|
| 1 → 2 | `domain/types.py`: T1 이 `__post_init__` 의 `ValueError` 를 `DomainInvariantError` 로 전환 → T2 가 같은 파일에 `CancelAck` 추가 | 순서만 지키면 충돌 없음. `CancelAck` 은 `__post_init__` 이 없어 전환 대상이 아니다 |
| 2 → 11,12 | `BrokerPort` 8메서드 → `FakeBroker` | 8/8 구현됨. **★ 결함 1건 (아래 C-1)** |
| 3 → 8,9,10 | `RepositoryPort` 17메서드 → `SqliteRepository` | 17/17 구현됨. 미구현·초과 없음 |
| 3 → 13 | T3 의 `TYPE_CHECKING` import → T13 의 AST 의존성 테스트 | 계획서가 해법 (a)(`TYPE_CHECKING` 블록 건너뛰기)를 이미 명시 |
| 4 → 5..10 | `schema.sql` CHECK 제약 → 매핑·리포지토리의 쓰기 | T4 의 `CHECK(status NOT IN ('HOLDING','SELL_PENDING') OR fill_* IS NOT NULL)` 과 T7 의 `row_to_stage` 가 같은 불변식을 두 층에서 강제 — 의도된 이중화 |
| 4 → 5..10 | `tests/adapters/sqlite/__init__.py` (T4 생성) → T5~10 의 테스트 위치 | T4 가 먼저이므로 문제 없음. `tests/adapters/__init__.py` 는 Plan 1 이 이미 만들어 둠 |
| 6 → 7 | `mapping.py` (T6 생성) → T7 이 같은 파일에 stage 매핑 추가 | 추가(append)이며 T6 의 심볼을 재정의하지 않는다 |
| 8 → 9 → 10 | `repository.py` 를 세 태스크가 순차 확장 | T8 이 `__init__`·설정·사이클·단계, T9 가 주문, T10 이 로그·`holdings`. 메서드 이름 중복 없음 |
| 11 → 12 | `FakeBroker.__init__` (T11) → T12 가 `fail_mode`·`fail_after` 추가 | 둘 다 기본값이 있어 T11 의 테스트 34건이 그대로 통과한다 |
| 2 → 3 | `tests/ports/__init__.py` (T2 생성) → T3 의 테스트 | T2 가 먼저 |

## B. 태스크별 자기 일치 (명시한 테스트 vs 명시한 코드)

| 태스크 | 발견 |
|---|---|
| 1 | 전환/비전환 32개 사이트를 표로 열거 — 테스트가 그 표와 1:1 |
| 2 | **★ 결함 (C-1)** |
| 3 | 17개 이름을 테스트가 집합 비교로 고정 |
| 4 | `executescript` 와 `with conn:` 의 트랜잭션 경계 문제를 계획서가 스스로 표시하고 구현자에게 보고를 요구 — 자기 일치 |
| 5 | naive datetime 거부를 `tzinfo.utcoffset(value) is None` 으로 판정 (`tzinfo is None` 이 아니라) — `format(value,"f")` 로 지수표기 회피. 테스트가 둘 다 검사 |
| 6 | `row_to_config` 가 `to_ladder(anchor_price=10_000)` 로 검증하는 한계를 계획서가 명시 |
| 7 | H3·H4 를 한 함수에 모음. `ladder is None` 이면 H4 만 건너뜀 — 테스트가 그 경로도 검사 |
| 8 | 단계 upsert 를 `ON CONFLICT DO UPDATE` 로. 테스트가 같은 단계 두 번 저장을 검사 |
| 9 | H5 = SUM(SELL) − SUM(BUY) over FILLED/PARTIAL. 테스트가 부분체결 포함 |
| 10 | **★ 결함 1건 발견·수정 완료 (계획서 작성 시 자체 검토, 커밋 a45b13e)** — 비차별 어서션 |
| 11 | `delay_ticks` 를 실시간이 아니라 소비된 틱 수로 셈 — 결정론. 테스트가 명시 |
| 12 | `BrokerTimeout` 이 `TimeoutError` 를 의도적으로 상속하지 않음. TIMEOUT 은 주문을 등록한 *뒤* 예외 — 설계서 9절 ⑤ "접수됨" 분기가 테스트 가능해진다. 테스트가 둘 다 검사 |
| 13 | G1 시나리오를 DB 왕복으로 재생하고 433 / [4,3,2,1] / 7 을 리터럴로 단정 |

## C. 발견과 룰링

### C-1 (Important, 수정 완료 — 커밋 예정)
`BrokerPort.subscribe_quotes` 는 일반 `def` 인데(비동기 제너레이터를 반환해야
호출부가 `async for` 를 바로 쓸 수 있다), Task 2 의 `test_broker_port_is_runtime_checkable`
안 Stub 두 곳이 그것을 `async def` 로 선언했다. `@runtime_checkable` 의 `isinstance`
는 메서드 **이름의 존재만** 보고 `async` 여부를 보지 않으므로 테스트는 통과하고,
그 Stub 은 Plan 3 의 키움 어댑터 저자에게 잘못된 본보기가 된다.

Ruling: Stub 을 포트와 같은 일반 `def` 로 고치고,
`test_subscribe_quotes_is_not_a_coroutine_function` 을 추가해
`inspect.iscoroutinefunction` 으로 8개 메서드의 async 여부를 전부 고정한다.
근거: 설계서 8.1절이 스트림을 `async for` 로 소비한다고 적었고, Protocol 이
검사하지 않는 계약 부분은 테스트로 못 박는 것이 이 프로젝트의 기존 방식이다
(Plan 1 의 `test_domain_imports_nothing_external` 과 같은 계열).
틀렸을 경우 비용: 없음에 가깝다 — 테스트 2줄이 늘고, 만약 Plan 2B 가 실제로
`await broker.subscribe_quotes(...)` 를 원하게 되면 포트와 이 테스트를 함께 뒤집으면
된다. 반대로 놓쳤을 경우의 비용은 Plan 3 에서 스트림이 조용히 동작하지 않는 것이다.

---

Task 1: dispatched (model sonnet, BASE 04fe8d2) — 브리프 task-1-brief.md
Task 1: DONE_WITH_CONCERNS (commit 777da21, 463/463 통과, domain 커버리지 99.82%)

## Ruling: `rules.py` 의 `_require_aware` 는 맨 `ValueError` 로 남는다
구현자가 브리프의 두 표에 명시되지 않은 사이트 하나(`rules.py:278` `_require_aware`)를
산술과 의미론으로 추론해 전환하지 않았다. 판단은 맞다 — 다만 더 강한 근거가 있다.

`_require_aware` 는 `now`(엔진 공급)와 `state.last_sold_at`(복원 필드)를 검사한다.
`last_sold_at` 이 복원 필드라 손상 후보처럼 보이지만, **naive 시각은 `decide()` 에
도달할 수 없다** — Task 5 의 `text_to_dt` 가 오프셋 없는 TEXT 를 읽을 때
`DomainInvariantError` 를 던지고, Task 6 의 `_corrupt` 가 그것을 `CorruptRowError`
로 감싼다. 즉 naive datetime 손상의 강제 경계는 코덱이며 `decide()` 가 아니다.
`decide()` 에 naive 가 나타났다면 그것은 호출자가 `StageState` 를 직접 만든
것이므로 호출자 버그가 맞다.
틀렸을 경우 비용: 낮다. `mapping.py` 가 이 예외를 `CorruptRowError` 로 감싸지
못하지만, 그 경로로는 naive 가 오지 않는다. 만약 Plan 2B 에서 엔진이
`StageState` 를 코덱 없이 만드는 경로가 생기면 그때 다시 본다.
Task 1: minor (deferred): `cycle.py:193` `is_cycle_complete` docstring 이 아직 "ValueError 를 던진다" 로 읽힌다 (실제로는 DomainInvariantError)
Task 1: minor (deferred): `ladder.py:63` 주석의 "LadderConfigError 는 ValueError 하위라서" 가 이제 한 단계 건너 참이다
Task 1: minor (deferred): `tests/domain/test_errors.py` 의 `T0` 가 `__import__("datetime")` 인라인 — **계획서 본문이 그렇게 지시함**(내 계획서의 흠). Task 5 이후 테스트 파일들과 함께 정리 후보
Task 1: complete (commits 04fe8d2..777da21, review clean)
Task 2: dispatched (model haiku, BASE 777da21)
Task 2: DONE (commit 83cacd6, 468/468 통과) → 리뷰 디스패치 (sonnet)
Task 2: minor (deferred): `tests/ports/test_broker.py` 의 함수 본문 안 `import dataclasses`/`import pytest` — **계획서 본문이 그렇게 지시함**
Task 2: 패턴 관찰 — Task 1·2 의 이연 minor 3건 중 2건이 같은 뿌리다: 내 계획서의 테스트 코드 블록이 함수 안에서 import 한다. 최종 리뷰에서 한 번에 정리 후보로 묶는다.
Task 2: complete (commits 777da21..83cacd6, review clean)

## Ruling: 계약 DTO(`SplitConfig`·`HoldingRow`)를 어댑터에서 포트로 옮긴다
계획서 원안은 두 DTO 를 `adapters/sqlite/mapping.py` 에 두고, `ports/repository.py`
가 그것을 `TYPE_CHECKING` 으로 참조하게 했다. 그 결과 Task 13 의 의존 방향 AST
테스트가 Task 3 의 코드에서 실패하고, 계획서는 해법 (a)로 **테스트가 TYPE_CHECKING
블록을 건너뛰게** 만들라고 지시했다.

그것이 신호였다 — 아키텍처 테스트를 약화시켜야 통과하는 배치는 배치가 틀린 것이다.
두 DTO 는 SQLite 고유가 아니다. `SplitConfig` 는 사용자 설정의 DTO 이고
`HoldingRow` 는 UI 읽기 모델이며, **포트의 계약 자체가 그 두 타입으로 쓰여 있다.**
어댑터 층에 두면 포트를 소비하는 모든 코드(Plan 2B 의 `engine/`)가 DTO 하나를
얻으려고 `adapters/sqlite/` 를 import 해야 한다 — 화살표가 거꾸로 돈다.

결정: `ports/repository.py` 가 두 DTO 를 정의하고 `adapters/sqlite/mapping.py` 가
가져다 쓴다. Task 13 의 AST 테스트는 예외 처리 없이 엄격해진다(느슨해지는 것이
아니라). Task 3 가 `to_ladder` 의 동작 테스트 3건을 함께 가진다 — 그것이 이
태스크의 유일한 동작이므로.
지금 결정한 이유: Task 3 디스패치 직전이라 비용이 계획서 편집뿐이다. Task 6 이후에
발견하면 4개 태스크의 재작업이 된다.
틀렸을 경우 비용: 낮다. DTO 의 물리적 위치만 다르고 필드·동작은 동일하다. 되돌리려면
정의를 mapping.py 로 옮기고 import 방향을 뒤집으면 된다.
검증: `to_ladder` 관련 새 어서션 4개를 실제 도메인 코드로 실행해 확인했다
(trigger_price(1)=10000, (2)=9500, Ladder 동등성, anchor 0 → LadderConfigError).
Task 3: DONE (commit d374784, 473/473 통과)
Task 3: minor (deferred): `ports/repository.py:21` 의 `CloseReason` 이 미사용 import — **내 계획서 코드 블록에서 온 것**. 포트는 `Cycle` 을 주고받고 `CloseReason` 을 직접 이름 부르지 않는다. 계획서는 고쳤고(커밋 예정) 코드의 한 줄은 최종 리뷰에서 정리.
Task 3: minor (deferred): 구현자 보고서가 메서드 수를 "18개" 로 적었으나 코드·테스트·브리프 모두 17개다. 코드는 정확하고 보고서 산술만 틀렸다.
Task 3: 내 실수 — DTO 이관 시 import 재작성 정규식이 계획서 Task 3 코드 블록을 손상시켰다(도메인 import 3줄이 `HoldingRow` 본문 안으로 들어감). 구현자가 알아서 무시했고 리뷰어가 잡았다. 계획서 복구 완료. 이후 계획서 편집 뒤에는 python 블록 전수 `ast.parse` 검사를 돌린다.
Task 3: complete (commits 83cacd6..d374784, review clean)
Task 4: DONE_WITH_CONCERNS (commit 860a442, 483/483 통과)

## Ruling: `schema.sql` 의 모든 CREATE 문에 `IF NOT EXISTS` 를 넣는다 (수정 지시)
구현자가 브리프의 미해결 지점을 실증했다: `executescript()` 의 DDL 은 `with conn:`
범위 밖에서 즉시 커밋되므로, DDL 과 버전 행 기록 사이에서 프로세스가 죽으면
테이블은 있고 `schema_version` 행은 없는 상태가 남는다.

나도 직접 재현했다 — 그 상태에서 재기동하면:
  `OperationalError: table schema_version already exists`
앱이 기동할 수 없고, 사용자에게는 DB 파일을 지우는 것 외에 방법이 없다.

이것은 Plan 1 결함 분류 (5) "안전장치에 탈출구가 없다 / 래칫이 깨졌다" 다.
`migrations.py` 의 독스트링이 스스로 "**멱등이다 — 매 기동마다 호출해도 안전해야
하며**" 라고 적었지만, 실제로는 **직전 실행이 완료됐을 때만** 멱등이다. 계약이
코드보다 넓다.

결정: 11개 `CREATE TABLE/INDEX/VIEW` 전부에 `IF NOT EXISTS` 를 넣고, 절반만 적용된
DB 에서 `apply_schema` 가 스스로 복구하는 것을 테스트로 고정한다. `INSERT` 는
이미 `DELETE FROM schema_version` 이 선행하므로 그대로 둔다.
근거: 이 모듈의 독스트링이 약속한 성질을 코드가 실제로 갖게 만드는 것이고,
`IF NOT EXISTS` 는 부분 적용(executescript 중간에 죽은 경우)까지 자기 치유하게
만든다. v1 단일 버전이므로 "정의가 다른 동명 테이블을 조용히 수용" 하는 위험은
지금 없다 — 실제 마이그레이션(v1→v2)은 ALTER 를 쓰게 되고 그때 다시 본다.
틀렸을 경우 비용: 낮다. `IF NOT EXISTS` 를 되돌리면 원래 상태다.
검증: 패치한 스키마로 같은 크래시 상황을 재현해 재적용이 성공하고 버전이 1이
되는 것을 확인했다.

Task 4: minor (deferred): 브리프 본문이 "테스트 11건" 이라 적었으나 브리프의 테스트
파일에는 함수가 10개다 — 내 계획서의 산술 오류. 코드는 10건이 맞다.
Task 4: fix round 1/5 (1 addressed, 0 open — schema.sql 멱등성; commits 860a442..c8c8bc4)
Task 4: minor (deferred): 새 테스트가 `migrations._SCHEMA_PATH`(비공개 심볼)에 손을 뻗는다. 실제 스키마와의 일치는 보장되지만 결합이 필요보다 강하다.
Task 4: minor (deferred): `current > SCHEMA_VERSION` 거부 분기가 미테스트. SCHEMA_VERSION=1 이라 지금은 도달 불가.
Task 4: handover → Plan 3: `IF NOT EXISTS` 는 **현재 버전 객체의 생성**만 멱등하게 한다. SCHEMA_VERSION 을 2로 올려 컬럼을 추가하려면 `CREATE TABLE IF NOT EXISTS` 가 낡은 모양의 기존 테이블에 조용히 no-op 하므로, 그때는 명시적 `ALTER TABLE` 단계가 필요하다. 지금은 마이그레이션 프레임워크가 아니다.
Task 4: complete (commits db71270..c8c8bc4, review clean, 1 fix round)
Task 5: DONE (commit 11bf35e, 504/504 통과)

Task 5: 리뷰 ❌ — Important 2건(둘 다 내 계획서 참조 코드에서 온 plan-mandated)

## Ruling: 리뷰어의 Important 2건 모두 인정하고 고친다 + Minor 1건을 Important 로 올린다
직접 재현해 확인했다.

(1) `text_to_ratio` 에 타입 가드가 없다 — `text_to_ratio(0.05)` 가 예외 없이
    `Decimal('0.05000000000000000277555756156289135105907917022705078125')` (58자)를
    반환한다. 쓰는 쪽 `ratio_to_text` 는 `float` 를 `TypeError` 로 막는데 **읽는 쪽만
    안 막는다.** 비대칭이고 조용한 손상이다.

(2) `text_to_dt` 가 `except (ValueError, TypeError)` 로 묶어 `TypeError` 를
    `DomainInvariantError` 로 바꾼다. `text_to_dt(None)` → "not a valid timestamp:
    None". Task 6 이 이것을 `CorruptRowError` 로 감싸면 사용자는 "corrupt row in
    cycle (id=3)" 를 보지만 실제 원인은 매핑의 널 검사 누락이다 — H1 이 만들려던
    진단 능력을 정확히 반대로 쓴다.

(3) **리뷰어의 Minor #2 를 Important 로 올린다.** 리뷰어는 NaN/Infinity 통과를
    "이 도메인에서는 범위 밖일 것" 이라며 Minor 로 뒀지만, 하류 방어를 직접 확인해
    보니 그렇지 않다:
      `Ladder(drop_pct=Decimal("NaN"))` → **`decimal.InvalidOperation`**
    이며 `issubclass(InvalidOperation, ValueError)` 는 **False** 다
    (MRO: InvalidOperation → DecimalException → ArithmeticError → Exception).
    따라서 Task 6 의 `except DomainInvariantError` 도 `except ValueError` 도 잡지
    못하고, NaN 비율은 도메인 깊은 곳에서 맨 예외로 튀어나온다. Infinity 는
    `LadderConfigError` 로 잡히지만 NaN 은 안 잡힌다.
    결정: `text_to_ratio` 와 `ratio_to_text` 가 유한하지 않은 `Decimal` 을 거부한다.
    비율 컬럼이 "NaN" 을 담고 있는 것은 행 손상이므로 `text_to_ratio` 는
    `DomainInvariantError`, `ratio_to_text` 는 호출자 버그이므로 `TypeError` 가
    아니라 `DomainInvariantError` — 값 오류이므로 후자로 통일한다.
근거: 이 모듈의 존재 이유가 "타입 오류와 값 오류를 나누어 상위 계층이 골라 감쌀 수
있게 하는 것" 이다. 세 건 모두 그 계약을 어긴다.
틀렸을 경우 비용: 낮다. 가드 3개와 테스트 4개가 늘어난다. NaN 판정을
`value.is_finite()` 로 하므로 정상 비율에는 영향이 없다.
Task 5: fix round 1/5 (3 addressed + 1 folded minor, 0 open; commits 11bf35e..07871d1, 513/513 통과) → 범위 한정 재리뷰 디스패치
Task 5: 재리뷰 — 3건 전부 ADDRESSED, 되돌리면 실패하는 테스트가 각각 존재, 새 파손 없음
Task 5: complete (commits c8c8bc4..07871d1, review clean, 1 fix round)
Task 6: DONE_WITH_CONCERNS (commit 645bd14, 529/529 통과)

## Ruling: 구현자가 추가한 `CloseReason.FORCED` 를 승인한다 — 내 계획서의 빈틈이었다
Task 6 의 브리프에 있는 `test_cycle_round_trip_closed_forced` 가 `CloseReason.FORCED`
를 요구하는데, 그 멤버를 추가하는 태스크가 **계획서 어디에도 없었다.** Task 4 의
스키마는 이미 `close_reason IN ('NORMAL','EMERGENCY','FORCED')` 를 허용하고 설계서
D20 이 그 값을 정의하는데, 도메인 enum 에는 없었다 — 스키마와 도메인이 어긋난
상태로 계획서가 쓰여 있었다.

구현자는 `domain/types.py` 에 멤버 하나를 순수 추가로 넣었다(자기 태스크 범위 밖임을
알고 DONE_WITH_CONCERNS 로 보고). 승인한다.
근거: (1) Task 4 가 이미 출하한 스키마가 그 값을 요구한다. (2) 순수 추가이며 상태
전이를 만들지 않는다 — D20 의 `force_close` 는 Plan 2B 로 남는다. (3) 이 멤버가
열어주는 유일한 오용(정상 `close()` 경로로 FORCED 를 만드는 것)은 스키마가 막는다.
직접 확인했다:
  `close_reason='FORCED'` + `forced_close_reason`/`forced_close_qty` NULL
  → `IntegrityError: CHECK constraint failed: close_reason IS NOT 'FORCED'`
즉 D20 의 이중화가 이미 제 역할을 한다.
틀렸을 경우 비용: 없음에 가깝다. 멤버를 되돌리면 Task 6 의 브리프 테스트가 깨지므로
사실상 되돌릴 수 없는 방향이 아니라 **필요했던 변경**이다.
파급: 전역 제약의 "이 계획은 domain/ 을 두 곳만 건드린다" 가 이제 세 곳이다 —
계획서를 고친다.
Task 6: 리뷰 ❌ — Important 2건 (둘 다 plan-mandated)

## Ruling: 두 건 모두 인정하고 고친다. Minor 2건 중 1건은 접어 넣고 1건은 무조치
직접 재현했다:
  `json_to_ladder(42)` → `CorruptRowError: corrupt ladder_json: the JSON object
  must be str, bytes or bytearray, not int`
같은 파일의 형제 함수는 그렇지 않다 — `row_to_config` 에 float 비율을 주면
`TypeError` 가 그대로 올라온다. **한 파일 안에서 한 함수만 자기 모듈의 계약을
어긴다.**

(1) `json_to_ladder` 의 `except (json.JSONDecodeError, TypeError)` 에서 `TypeError`
    를 뺀다. `json.loads` 의 `TypeError` 는 비문자열 인자 — 호출자 버그다.
(2) **감싸지 않는 쪽 절반을 검증하는 테스트가 16건 중 0건이다.** 그래서 (1)이
    테스트를 통과한 채 출하됐다. 양쪽 절반을 다 검증하는 테스트를 넣는다.
    → 이것은 Task 5 의 "비차별 테스트" 와 같은 계열이며, **Task 7 이 같은 파일에서
      H3·H4 를 다루므로 그 디스패치에 이 요구를 명시해 넣는다.**
(3) Minor(미사용 import 3개: `dataclass`·`datetime`·`Decimal`) — 같은 파일이고
    한 줄씩이므로 이 라운드에 접어 넣는다. 라운드를 늘리지 않는다.
(4) Minor(모듈 독스트링이 아직 구현되지 않은 H3·H4 를 자기 것으로 적었다) —
    **무조치.** Task 7 이 바로 다음에 같은 파일에 H3·H4 를 넣으므로 한 태스크 뒤에
    사실이 된다. 지금 고치면 Task 7 이 되돌리게 된다.
틀렸을 경우 비용: (1)은 낮다 — 유일한 호출부(`row_to_cycle`)가 이미 널 가드를
하므로 정상 경로에 변화가 없다. (2)는 테스트만 늘어난다.
Task 6: fix round 1/5 (2 addressed + 1 folded minor, 0 open; commits 645bd14..a0359a4, 532/532 통과) → 범위 한정 재리뷰 디스패치
Task 6: 재리뷰 — 3건 전부 ADDRESSED, 새 파손 없음, 독스트링 무조치 지시 준수
Task 6: complete (commits 07871d1..a0359a4, review clean, 1 fix round)

## 반복되는 뿌리 (Task 5·6 공통) — Task 7 디스패치에 명시해 넣는다
계약이 두 방향인데 **한 방향만 지키고 한 방향만 테스트**했다.
- Task 5: `ratio_to_text` 는 `float` 를 막고 `text_to_ratio` 는 안 막았다.
- Task 6: 감싸는 절반은 16건 테스트, 감싸지 않는 절반은 0건.
Task 7 은 같은 파일에서 H3·H4 를 다루므로 같은 함정이 있다 — `rows_to_stages` 가
"완전하지 않은 집합을 거부한다" 만 테스트하고 "완전한 집합을 통과시킨다" 를
빠뜨리거나, `ladder is None` 일 때 H4 를 건너뛰는 경로를 안 볼 수 있다.
Task 7: DONE (commit 364a189, 549/549 통과)
Task 7: 리뷰 ❌ — Important 2건 + ⚠️ 1건 + Minor 6건

## ⚠️ 해소 (컨트롤러 몫): `ladder=None` 경로의 꼬리 절단 약점은 오늘 도달 불가
리뷰어가 지적한 대로 `ladder=None` 이면 `expected = set(range(1, len(seen)+1))` 이
행 자신에게서 유도되므로 **중간 구멍은 잡지만 꼬리 절단(1..5만 남은 7단계 사이클)은
못 잡는다.** 내가 확인한 결과 오늘은 도달 불가다:
- `Cycle` 전이표에 `ladder` 를 `None` 으로 되돌리는 경로가 없다 —
  `confirm_anchor` 는 일방향이다(소스에 `ladder=None` 없음).
- Task 8 의 `load_stages` 는 `SELECT ... WHERE cycle_id = ?` 로 필터하고
  `ladder=cycle.ladder` 를 넘긴다.
따라서 `ladder=None` 인 사이클은 앵커 미확정(STARTING)이며 단계 행이 없다 —
`rows_to_stages([], ladder=None)` → `[]` 가 유일하게 지나는 경로다.
**Task 8 디스패치에 요구를 넣는다**: STARTING 사이클에 단계 행이 0개임을 단정하는
테스트. 그 불변식이 깨지면 이 약점이 살아난다.

## Ruling: Important 2건 고침 + Minor 6건 중 5건 접어 넣기
(1) `rows_to_stages` 의 `cycle_id` 미검증 — 고친다. **리뷰어가 나보다 강한 논거를
    찾았다**: 중복 가드(mapping.py:240-244)는 스키마의
    `UNIQUE(cycle_id, stage_no)` 때문에 **오직 사이클 간 오염으로만 도달 가능**하다.
    즉 코드가 그 경우를 절반 방어하고 나서 닫기를 그만둔 것이다. 게다가
    `ladder=None` 경로에서는 H4 가 생략되므로 외래 행에 **아무 검사도 없다.**
(2) 새 코드의 감싸지 않는 절반이 테스트로 못 박히지 않았다 — 고친다. 이 파일에서
    직전 라운드에 깨진 것이 정확히 그 구멍이며, `except ValueError` 를
    `except (ValueError, TypeError)` 로 바꾸는 한 토큰 변경이 17건 전부 초록으로
    통과한다.
(3~7) Minor 접어 넣기: 집합 수준 오류 메시지에 테이블·rowid 추가,
    `"4" in str` → `"missing [4]" in str`, 초과 집합 테스트에 `match="unexpected"`,
    죽은 파라미터 `id_base` 제거, **모듈 독스트링의 "ValueError 는 감싸지 않는다"
    문장 교정** — 실제로는 알 수 없는 enum 값(맨 `ValueError`)을 행 손상으로 보고
    감싼다. 리뷰어 말대로 그 문장이 "가드를 좁혀도 된다는 허가" 로 읽히며, 이미 한
    번 그 일이 일어났다.
(8) 무조치: `ladder=None` 꼬리 절단 자체 — 위 ⚠️ 해소대로 도달 불가이고, 막으려면
    `max_stages` 를 사이클 밖에서 가져와야 해서 이 함수의 계약이 넓어진다.
틀렸을 경우 비용: (1)은 낮다 — 정상 경로(SQL 이 이미 필터)에 변화가 없고 외래 행만
거부된다. 나머지는 메시지·테스트뿐이다.
Task 7: fix round 1/5 (2 Important + 5 folded minor, 0 open; commits 364a189..a0dff41, 552/552 통과)
Task 7: 구현자가 작업 중 `git checkout --` 로 미커밋 수정을 날렸다가 `git diff --stat` 로
  발견해 재적용했다고 보고. 재적용이 불완전할 수 있는 상황이므로 7개 항목을 컨트롤러가
  전수 확인했다 — 전부 반영됨. (교훈: 구현자에게 백업은 `cp` 로 하라고 안내할 가치가 있다)
Task 7: 재리뷰 — 7건 전부 ADDRESSED, 각각 되돌리면 실패하는 테스트 존재, 새 파손 없음
Task 7: complete (commits a0359a4..a0dff41, review clean, 1 fix round)

## 정정: 위 ⚠️ 해소의 전제가 틀렸다 (결론은 유지, 논거가 다름)
나는 "`ladder=None` 인 사이클은 단계 행이 없다" 고 적었다. **틀렸다** — Task 8 의
브리프 테스트 `test_load_stages_of_a_starting_cycle_skips_h4` 가 STARTING 사이클에
단계 행 7개를 일부러 쓴다. 즉 그 상태는 정상 경로다.

실제로 확인한 노출: `ladder=None` + 행 6개(1..6) → 통과, 6단계 복원. 꼬리 절단을
잡지 못한다.

그런데 결론은 유지된다 — 다른 이유로:
  `Cycle.accepts_triggers` 가 STARTING 에서 `False` 이고,
  `decide()` 가 그 불완전한 6단계 집합으로도 `[]` 를 반환한다 (직접 실행 확인).
그리고 RUNNING 이 되면 `ladder` 가 있으므로 같은 6개 집합이
  `CorruptRowError: incomplete stage_state set for cycle 1: missing [7]`
로 거부된다.

즉 **불완전한 집합이 로드될 수 있는 유일한 상태에서는 그것으로 아무 결정도 내려지지
않으며, 결정이 내려지는 상태로 넘어가는 순간 검출된다.** 무조치 결정은 유지한다.
교훈: "도달 불가" 라고 적기 전에 그 상태를 만드는 테스트가 계획서 안에 있는지 봐야
한다. 나는 도달 경로를 도메인 전이표에서만 찾고 계획서의 테스트를 보지 않았다.
Task 8: DONE (commit e6ab49c, 565/565 통과)

## Ruling: `dict(row)` 변환을 SQL 경계에 두는 구현자의 수정을 승인한다 — 내 계획서의 빈틈
브리프의 `repository.py` 코드는 실제 SQLite 연결에서 동작하지 않았다.
`mapping.py` 가 오류 귀속을 위해 `row.get("id")` 를 4곳에서 쓰는데,
`connect()` 가 설정하는 `sqlite3.Row` 에는 **`.get()` 이 없다**(직접 확인:
`issubclass(sqlite3.Row, Mapping)` 은 `False`, `hasattr(sqlite3.Row, 'get')` 도
`False`). 내 매핑 테스트가 평범한 `dict` 만 넘겨서 이 이음새가 한 번도 검증되지
않았다 — Task 8 이 첫 실제 통합 지점이라 여기서 드러났다.

구현자는 5개 fetch 지점에서 `dict(row)` 로 변환하게 고쳤다. 승인한다. 그리고
**단순 우회가 아니라 더 나은 선택인 이유를 하나 더 확인했다**:
  `sqlite3.Row["없는키"]` → `IndexError`
  `dict["없는키"]`        → `KeyError`
즉 변환하지 않으면 테스트(dict)와 운영(Row)의 예외 클래스가 **달라진다.**
테스트로 재현할 수 없는 실패 모드가 생기는 것이다. `dict(row)` 는 그 괴리를
없앤다.

Tasks 9/10 이 잊을 위험도 검토했다 — 조용하지 않다:
`AttributeError: 'sqlite3.Row' object has no attribute 'get'` 가 즉시 나고,
Task 9/10 의 픽스처가 실제 연결을 쓰므로 자기 테스트에서 바로 실패한다.
비용도 무시 가능하다(0.48µs/행, 20만회 0.095초).
그래도 **두 디스패치에 요구로 명시해 넣는다.**
틀렸을 경우 비용: 낮다. 대안(연결의 `row_factory` 를 dict 로 바꾸기)으로 옮기려면
`migrations.py` 한 줄과 Task 4 의 테스트를 손대야 하는데, 그쪽이 더 넓은 변경이다.
Task 8: minor (deferred): `tests/adapters/sqlite/test_repository_core.py` 가 `start` 를 import 하고 안 쓴다 — **계획서 코드 블록에서 온 것**
Task 8: minor (deferred): `save_config`·`save_stage` 가 각자 `columns`/`placeholders` 를 만든다. 두 번뿐이라 지금 헬퍼 추출은 조급하다 — Task 9/10 에서 세 번째가 나오면 재검토
Task 8: minor (deferred): 브리프 산문이 "14 tests" 라 적었으나 코드 블록에는 13개 — 내 계획서의 산술 오류(Task 4 와 같은 부류)
Task 8: complete (commits a0dff41..e6ab49c, review clean) — 10/17 메서드
Task 9: DONE (commit 6376553, 578/578 통과)
Task 9: 컨트롤러 독립 검증 — H5 집계 범위 8개 경계 전부 정확했다:
  BUY FILLED -1,000,000 / +SELL FILLED = +50,000 / +BUY PARTIAL(9,500×40, 주문100) = -330,000
  CANCELED·REJECTED·ACCEPTED·UNKNOWN·SENDING 각각 추가 → 변화 없음
  주문 0건 사이클 → int 0 (None 아님), 반환 타입 int (float 아님), 사이클 간 격리 유지

## 배경 보안 리뷰 (커밋 6376553) — 3건 지적, 전부 실증됨 + 내가 1건 추가
상세 텍스트는 오지 않아 범주명만 받았고, 직접 재현해 확인했다.

(1) **fail-open-state-drift** — `update_order_log` 가 없는 `client_ref` 에 대해
    0행을 갱신하고 **조용히 성공한다**(반환 `None`, 예외 없음). 호출자는 기록된
    줄 알지만 DB 는 브로커와 영구히 어긋난다.

(2) **state-regression (단조성 가드 없음)** — `UPDATE ... SET status = ?` 가
    무조건이라 종결 상태를 되돌릴 수 있다. 실증:
      A 를 FILLED(10,000×100) → 실현손익 **-1,000,000**
      A 를 ACCEPTED 로 되돌림  → 실현손익 **0**
    `realized_pnl_for_cycle` 이 FILLED·PARTIAL 만 세므로, 늦게 도착한 낡은
    브로커 응답 하나가 체결을 손익에서 지운다.

(3) **financial-integrity (체결값 덧쓰기)** — `COALESCE(?, fill_price)` 는 이미
    확정된 체결값을 **덧쓸 수 있다.** 실증:
      B 체결 10,000×100 → 실현손익 **-1,000,000**
      같은 ref 로 1×1 재전송 → 실현손익 **-1**

(4) **내가 추가로 찾은 것** — `fill_qty > req_qty` 가 막히지 않는다. 100주 주문에
    99,999주 체결을 기록할 수 있다.

이것들이 가설이 아닌 이유: 설계서 9절의 UNKNOWN 분기가 **브로커에 재조회해서
갱신하는 것을 정상 절차로 규정한다.** 즉 중복·지연 갱신은 기대되는 시나리오다.

## Ruling: 네 건 모두 Task 9 에서 고친다 (Plan 2B 로 넘기지 않는다)
불변식은 리포지토리가 지고, **거부된 쓰기를 어떻게 처리할지의 정책만** Plan 2B 가
진다 — `mapping.py` 가 `CorruptRowError` 를 던지고 엔진이 판단하는 것과 같은 분할이다.
지금 고쳐야 하는 이유: Plan 2B 의 executor 가 이 API 를 상대로 작성되므로, 가드가
없으면 2B 는 `update_order_log` 를 자유롭게 불러도 된다는 가정 위에 쓰이고, 나중에
가드를 넣으면 2B 가 깨진다.
설계: 주문 이력은 **append-mostly 이고 종결된 행은 불변**이다.
  - 0행 갱신 → `OrderLogNotFound` (ports/repository.py 에 정의, `LookupError` 상속 —
    `ValueError`·`TypeError` 와 겹치지 않아 감싸기 계약과 충돌하지 않는다)
  - 종결 상태(FILLED·CANCELED·REJECTED)에서 다른 상태로의 변경 거부.
    같은 종결 상태의 재확인(멱등 재시도)은 허용.
  - 종결된 행의 `fill_price`·`fill_qty` 덧쓰기 거부.
  - `fill_qty > req_qty` 거부.
틀렸을 경우 비용: 중간. 가드가 너무 엄격하면 Plan 2B 의 정상 재시도가 막힐 수 있다 —
그래서 같은 종결 상태의 멱등 재확인은 명시적으로 허용한다. `fill_qty > req_qty` 를
스키마 CHECK 로 옮기는 것은 Plan 3 의 마이그레이션 항목으로 남긴다(`IF NOT EXISTS` 는
기존 테이블에 CHECK 를 추가하지 못한다).
진행: Task 9 의 태스크 리뷰가 진행 중이므로, 그 결과와 **합쳐 한 라운드로** 고친다.

## Ruling (Critical): 실현손익은 **상태가 아니라 체결 데이터**로 집계한다 — 내 사양 오류
리뷰어의 "상태 필터가 테스트로 못 박히지 않았다" 를 따라가다 **필터 자체가 틀렸음**을
찾았다. 설계서 200행이 매수 부분체결의 정상 절차를 규정한다:
  "체결 수량만으로 `HOLDING` 확정, **잔량 주문 취소**"
즉 부분체결된 매수 주문은 **정상적으로 `CANCELED` 로 끝나면서 실제 체결 데이터를
갖는다.** 내가 브리프에 지정한 `status IN ('FILLED','PARTIAL')` 은 그 행을 제외한다.

실증:
  ① 매수 105주, 40주 부분체결                 → 실현손익 -380,000
  ② 잔량 65주 취소 (설계서의 정상 절차)         →                0  ← 매입원가 소멸
  ③ 그 40주를 9,980 에 매도                   → 보고 399,200 / 진짜 19,200
  즉 **380,000원 과대 계상.** 사용자에게 이익으로 보고된다.

결정: 필터를 `fill_price IS NOT NULL AND fill_qty IS NOT NULL AND fill_qty > 0`
으로 바꾸고 `status` 결합을 없앤다. 단 `REJECTED` 는 명시적으로 제외한다 —
거부된 주문은 접수조차 되지 않았으므로 체결이 있을 수 없고, 있다면 그것은 손상이다.
근거: **상태는 주문의 생애 끝을 말하고 체결 데이터는 실제로 오간 것을 말한다.**
손익은 후자다. 이중 계상 위험은 없다 — 한 주문은 한 행이고 갱신되므로 한 번만
기여한다. 매도 부분체결의 잔량 재발주(설계서 201행)는 새 `client_ref` 의 새 행이므로
각자 자기 체결분을 기여하는 것이 맞다.
틀렸을 경우 비용: 낮다 — 되돌리려면 `WHERE` 절 한 줄이다. 반대로 놓쳤을 경우의
비용은 사용자가 보는 손익 숫자가 매입원가만큼 과대 계상되는 것이다.
파급: 이 결정 때문에 체결 데이터가 손익의 **유일한** 근거가 되므로, 보안 리뷰가
지적한 (2)상태 되돌림·(3)체결값 덧쓰기 가드가 더 중요해진다. 같은 라운드에서 고친다.
Task 9: fix round 1/5 (Critical 1 + Important 5, 0 open; commits 6376553..e61e0e8, 589/589 통과)
Task 9: 컨트롤러 독립 검증 — 거부되는 절반 4건과 **허용돼야 하는 절반 4건** 모두 확인:
  거부: 없는 ref → OrderLogNotFound / FILLED→ACCEPTED, 체결값 덧쓰기, fill_qty>req_qty → OrderLogInvariantError
  허용: 같은 값 멱등 재확인 / PARTIAL→CANCELED (설계서 200행) / SENDING→ACCEPTED / ACCEPTED→FILLED
  Critical 시나리오: 보고 19,200 = 진짜 19,200 (이전 399,200)
  REJECTED + 체결데이터 → 0 (세지 않음), PARTIAL 의 체결값은 CANCELED 후에도 9,500×40 보존
Task 9: 재리뷰 — 6건 전부 ADDRESSED (거부·허용 양쪽 절반 다 못 박힘). 새 파손 Important 1건.

## Ruling: 원자성 지적은 맞다 — 보장은 성립하되 **독스트링의 이유가 틀렸다**
재리뷰어 주장을 직접 확인했다 (Python 3.12.13):
  `with conn:` 안에서 `SELECT` 직후 `in_transaction = False`
  `UPDATE` 직후에야 `True`
즉 Python `sqlite3` 는 DML 앞에서만 암묵적 트랜잭션을 열므로, `SELECT` 는 쓰기
락 밖에서 실행된다. 독스트링의 "확인과 갱신 사이에 다른 갱신이 끼어들 수 없다" 는
SQLite 가 보장하는 것이 아니다.

그런데 보장 자체는 성립한다 — 두 가지 다른 이유로:
(1) 설계서 7.1절(D7): 단일 프로세스, GUI 메인스레드 + 엔진 asyncio 워커스레드.
    **GUI 는 큐로만 통신하고 DB 를 직접 건드리지 않는다.** DB 쓰기는 엔진 스레드의
    단일 연결에서만 일어난다 — 동시 작성자가 없다.
(2) `update_order_log` 는 **동기 메서드이며 `await` 지점이 없다.** 단일 asyncio
    루프의 5개 태스크(명령 소비·시세·트리거·미체결 감시·대사)가 협조적으로 돌아도
    이 메서드 안에서는 양보하지 않으므로 끼어들 수 없다.

결정: 코드를 바꾸지 않고 **독스트링을 정직하게 고친다** — 실제 근거(단일 작성자
아키텍처 + await 없음)를 적고, **전제조건을 명시**한다.
근거: 대안 두 개가 다 비싸다. (a) `connect()` 에 `autocommit=False` 를 넣으면
`SELECT` 도 트랜잭션에 들어가지만(3.12 에서 확인) 모든 읽기가 트랜잭션을 열어두게
되어 이미 리뷰를 통과한 어댑터 전체의 동작이 바뀐다. (b) 가드를 UPDATE 의 `WHERE`
절로 옮기면 진짜 원자적이지만, **막 리뷰를 통과한 돈 관련 코드를 도달 불가능한
경합 때문에 다시 쓰는 것**이고 진단 경로가 복잡해진다.
틀렸을 경우 비용: Plan 2B/2C 가 두 번째 쓰기 연결을 도입하면 이 가드가 뚫린다.
그래서 **핸드오버 제약으로 명시 기록한다** (아래).
Task 9: handover → Plan 2B/2C: `order_log` 쓰기는 **엔진 스레드의 단일 연결**에서만
  해야 한다. 두 번째 쓰기 연결이 생기면 `update_order_log` 의 확인-후-갱신이
  원자적이지 않으므로, 그때는 가드를 UPDATE 의 `WHERE` 절로 옮기거나
  `BEGIN IMMEDIATE` 를 써야 한다. GUI 는 큐로만 통신한다(설계서 7.1절).
Task 9: minor (deferred): 수정 보고서가 "중복 테스트 하나를 제거했다" 고 서술했으나
  diff 는 `124 insertions(+), 0 deletions` 로 삭제가 없다. 최종 테스트 수와 커버리지는
  맞지만 서술이 부정확하다 — Task 3 의 "18개 메서드" 와 같은 부류(보고서 정확도).
Task 9: fix round 2/5 (1 addressed, 0 open; commit 99df34c — 독스트링만, 실행 코드 무변경)
Task 9: complete (commits c7b28af..99df34c, review clean, 2 fix rounds, 7 findings)
  — 이 계획에서 가장 무거운 태스크였다: 배경 보안 리뷰 3건, 내가 찾은 2건(과체결 기록 +
    사양 오류), 태스크 리뷰 1건, 재리뷰 1건.
Task 10: DONE (commit ad7001a, 599/599 통과) — 17/17 메서드, isinstance(repo, RepositoryPort) 참
Task 10: 구현자가 내 계획서 결함 2건을 조용히 옮겨 쓰지 않고 보고했다:
  (1) 커밋 메시지 템플릿의 "18개 메서드" — 실제 17개. **내 계획서 산술 오류 세 번째**
      (Task 4 "11 tests" vs 10, Task 8 "14 tests" vs 13). 계획서 3곳 수정(f77f4b5).
      다만 구현자가 지시대로 커밋 메시지에 옮겨 썼으므로 커밋 ad7001a 의 메시지에는
      "18개" 가 남아 있다 — 브랜치가 미푸시이지만 리뷰 패키지가 그 SHA 를 참조하므로
      amend 하지 않는다. minor (deferred).
  (2) `test_holdings_avg_price_truncates` 가 `repo` 픽스처를 쓰는데 이웃 테스트에서
      `conn.close()` 를 함께 복사해 와 NameError 가 됐다 — **내가 자체 검토에서 이
      테스트를 끼워 넣을 때 낸 실수다.** 계획서 수정(f77f4b5).
Task 10: 컨트롤러 검증 — isinstance 참, 절사 테스트의 판별력 확인(소수부 0.567 →
  절사 9,695 vs 반올림 9,696)
Task 10: minor (deferred): `SOLD` 상태 단계가 `holdings` 뷰에서 제외되는 것을 직접 검증하는 테스트가 없다 (`WAITING` 만 검증). **내 브리프의 테스트 목록에서 온 공백** — `seed()` 헬퍼가 `WAITING`/`HOLDING` 만 만든다. Task 13 의 G2a 게이트가 전 사이클을 재생하며 SOLD 를 만들므로 부수적으로 덮일 가능성 — 최종 리뷰에서 확인.
Task 10: minor (deferred): `cy.status == 'CLOSED'` 사이클이 `holdings` 에서 제외되는 것도 미검증 — 같은 부류
Task 10: minor (deferred): `holdings()` 에서 `cycle_id` 만 `int(...)` 로 감싸지 않았다 (다른 정수 필드는 감쌈). 무해하나 일관성 없음
Task 10: complete (commits 99df34c..ad7001a, review clean) — **17/17 메서드, 포트 완성**
Task 11: DONE (commit 475c436, 618/618 통과, 경고 0)
Task 11: 구현자가 계획서 결함 2건을 보고했다 — 안 쓰는 `field` import, 그리고 모듈 수준
  `pytestmark = pytest.mark.asyncio` 가 동기 테스트 하나에 `PytestWarning` 을 낸다는 것.
  둘 다 고쳤다. 후자는 전역 제약("테스트 출력은 깨끗해야 한다")에 걸리는 실질 결함이다.
Task 11: 컨트롤러 검증 — 4개 체결 모드 전부 결정론적(같은 스크립트로 두 번 → 같은 결과),
  `subscribe_quotes` 만 일반 def, PARTIAL 이 1주 주문에서 최소 1주 보장, Tick.at tz-aware

## Ruling (근본 원인): 계획서의 절대 테스트 수를 검증 가능한 형태로 바꿨다 (커밋 c10018d)
이 계획서의 산술 오류가 **네 번** 반복됐다 (Task 4 "11 tests" vs 10, Task 7 "18" vs 17,
Task 8 "14" vs 13, Task 9 "14" vs 13) — 여기에 Task 10 의 "18개 메서드" 와 Task 11 의
낡은 "기존 453개" 까지 여섯 번이다.
뿌리는 개별 실수가 아니라 **습관**이다: 태스크마다 절대 수를 적었는데 그 숫자는
태스크마다·수정 라운드마다 바뀌므로 원리적으로 정확할 수 없다.
결정: "PASS (N tests + 기존 전부)" 7곳을 "PASS (이 파일의 테스트 전부 + 기존 테스트
전부)" 로 바꾸고, Task 11 이후의 낡은 "453개" 2곳을 고쳤다. Task 1 의 453 은 그
시점에 정확했으므로 유지.
근거: 브리프의 테스트 코드 자체가 권위이며, 구현자가 실제로 검증할 수 있는 형태여야
한다. 검증 불가능한 숫자는 구현자를 오도하거나(Task 10 은 그 숫자를 커밋 메시지에
옮겨 썼다) 무시되거나 둘 중 하나다.
틀렸을 경우 비용: 없음에 가깝다 — 산문만 바뀌고 테스트 코드는 그대로다.
Task 11: 리뷰 승인 — 8개 메서드 시그니처 개별 대조, 4개 모드 전부 분기 삭제 시 실패하는
  테스트 존재, 결정론 자체도 테스트로 못 박힘, async 마커 18개 전부 확인
Task 11: minor → **Task 12 에 접어 넣는다**: 긴급청산 시장가 매도가 `fill_mode` 를
  우회한다는 보장이 테스트로 못 박히지 않았다. 커밋 메시지가 "NEVER 모드에서도
  검증할 수 있다" 고 주장하는데 테스트는 `INSTANT` 로만 돌린다 — 그 모드에서는 두
  조건이 구분되지 않는다. 내가 직접 확인한 결과 **동작은 정확하다**:
    INSTANT/NEVER/PARTIAL/DELAYED 네 모드 전부에서 긴급 시장가 매도가 100주 즉시 체결.
  그러나 `_accept` 의 `price is None` 단축이 나중에 옮겨지면 조용히 깨지고 아무도
  모른다. 설계서 6절이 "긴급 기능의 즉시성" 을 원칙으로 규정하므로 못 박을 값이 있다.
  Task 12 가 같은 파일·같은 테스트 영역을 다루므로 라운드를 늘리지 않고 접어 넣는다.
Task 11: minor (deferred): PARTIAL·DELAYED 체결의 예수금 반영이 미검증 (`_fill` 은
  체결 수량으로 계산하지만 테스트가 `get_balance()` 를 안 본다) — 내 브리프의 공백
Task 11: minor (deferred): `get_price` 와 `_current_price` 가 같은 본문 중복
Task 11: minor (deferred): `subscribe_quotes(codes)` 가 `codes` 를 무시한다 (단일 종목
  범위에서는 무해하나 독스트링에 명시할 가치)
Task 11: complete (commits f77f4b5..475c436, review clean)
Task 12: DONE (commit f94d2c6, 632/632 통과, 경고 0)
Task 12: 컨트롤러 검증 — 5개 계약 전부 확인:
  `BrokerTimeout` MRO = [BrokerTimeout, Exception, ...] — `TimeoutError` 미상속.
    `asyncio.TimeoutError is TimeoutError` 가 True 이므로 이 결정이 실질적 의미를 갖는다.
  TIMEOUT 후 당일주문 1건(접수됨 분기 재현 가능) / REJECT 후 0건(존재하지 않음)
  `BrokerRejected(code='40510', message='주문 거부 (시뮬레이션)')`
  fail_after=2 → ['성공','성공','타임아웃']
  **접어 넣은 테스트**: NEVER 모드 + 긴급 시장가 매도 → FILLED 100주
  DISCONNECT 후 clear_failure() → [9500,9400] 다음 [9300,9200] 이어서 재생
Task 12: 리뷰 ❌ — Important 2건, 둘 다 실증됨

## Ruling: 두 건 모두 고친다. 두 번째는 설계서가 답을 준다
(1) **`get_balance()` 가 `fail_after` 예산을 잠식한다** (plan-mandated — 내 브리프 코드).
    `_should_fail()` 이 `fail_mode is not NONE` 이면 무조건 `_calls` 를 올리는데,
    `get_balance()` 는 `TIMEOUT` 일 때만 그 결과에 반응한다. 실증:
      `FakeBroker(fail_mode=REJECT, fail_after=1)` 에서
      `get_balance()` 한 번 → 그 다음 **첫** 주문이 거부됨 (통과해야 하는데)
      대조군(get_balance 없음)은 첫 주문 성공
    "결정론이 이 모듈의 계약" 인데 실패 지점이 **무관한 호출 패턴에 의존**한다.
    Plan 2B 의 엔진은 5분 주기로 잔고 대사를 하므로, `fail_after=3` 으로 4번째
    주문을 거부시키려는 2B 테스트가 대사 호출 때문에 어긋난다.
    결정: `_should_fail()` 이 **자기가 실패시킬 수 있는 호출만 센다.** 호출 지점이
    자기가 존중하는 모드를 넘기고, 현재 모드가 거기 없으면 카운터를 올리지 않고
    `False` 를 반환한다. 그러면 `fail_after` 의 의미가 "실패할 수 있었던 호출 N번" 으로
    분명해진다.

(2) **`_accept` 의 `DISCONNECT` 분기를 제거한다** — 내 브리프에 없던 것이고,
    **설계서와 모순된다.** 설계서 8.4절:
      "WebSocket 끊김 → REST 폴링(source=REST_POLL, 3초 주기), **트리거 판정은 계속
       수행**"
    즉 스트림이 끊겨도 REST 주문은 나가야 하며, 그것이 이 모드로 검증할 **바로 그
    대상**이다. 실증: 지금은 `DISCONNECT` 에서 `place_limit_order` 가
    `BrokerDisconnected` 를 던지므로 8.4절의 폴백 경로를 시뮬레이션할 수 없다.
    게다가 리뷰어가 짚은 대로 같은 `FailMode` 값이 두 가지 검출 기제를 쓴다 —
    스트림은 틱 수, `_accept` 는 호출 수.
    결정: 분기 제거. `DISCONNECT` 는 시세 스트림 전용이다. 그리고 **끊긴 상태에서도
    주문이 나간다는 것을 못 박는 테스트**를 추가한다 — 그것이 8.4절 폴백의 전제다.
근거: (2)는 리뷰어가 "테스트를 추가하거나 분기를 제거하라" 고 열어 두었는데, 설계서가
어느 쪽인지 정한다. 분기를 남기고 테스트를 붙이면 설계서 8.4 를 검증 불가능하게
만드는 동작을 고정하는 셈이다.
틀렸을 경우 비용: (1) 낮음 — `fail_after` 의 의미가 분명해질 뿐이다. (2) 낮음 —
분기가 아직 아무도 쓰지 않는다. 만약 Plan 2B 가 "REST 도 죽는 완전 단절" 을 원하면
그때 별도 모드(`FailMode.OFFLINE` 등)를 추가하는 것이 옳다.
Task 12: fix round 1/5 (Important 2 + folded minor 2, 0 open; commits f94d2c6..f6c8762, 635/635 통과)
Task 12: 컨트롤러 검증 — 예산 누수 닫힘(REJECT 후 첫 주문 성공 / TIMEOUT 은 여전히 예산
  소비 후 실패), DISCONNECT 에서 REST 주문 FILLED + 스트림은 여전히 끊김(설계서 8.4),
  Task 11 의 4개 모드 결정론 유지
Task 12: 재리뷰 — 4건 전부 ADDRESSED. `_should_fail` 호출 지점 2곳 전수 확인
  (`_accept` → (REJECT, TIMEOUT), `get_balance` → (TIMEOUT,)), 양쪽 절반 다 못 박힘
  (과잉 수정도 잡히는 구조), `_replay` 의 DISCONNECT 는 의도대로 `_should_fail` 독립
Task 12: complete (commits c10018d..f6c8762, review clean, 1 fix round)
Task 13: DONE (commit a0b1509, 642/642 통과, 전체 커버리지 98.94%)
Task 13: 컨트롤러 검증 — G2a 게이트 7건 통과, AST 의존 방향 테스트에 TYPE_CHECKING
  예외 처리 없음(엄격), G1 리터럴이 DB 왕복 후에도 동일: 433주 / [4,3,2,1] / 7건
Task 13: Task 10 의 커버리지 공백 두 개가 메워졌다 — 사이클 CLOSED 후 holdings() == []
Task 13: 리뷰 승인 — 왕복이 진짜 왕복임을 확인(매 결정마다 `load_stages`·`load_cycle`
  재조회), G1 리터럴 3개가 DB 유래 상태에 대해 단정됨, H2 가 왕복된 시각으로 쿨다운
  산술 수행, decision-4 의 holdings() == [] 도 진짜 CLOSED·SOLD 뒤에 실행됨.
  AST 테스트가 비어 있지 않음(순회 대상 존재 확인)이고 G1 의 테스트보다 오히려 엄격하다.

## Ruling: Important 1건 + Minor 3건을 한 라운드로 고친다
(1) **[Important, plan-mandated] `test_h3_and_h4_hold_at_the_repository_boundary` 가
    H4 를 이름으로 주장하는데 본문은 H3 만 본다.** 확인: 본문이 stage_no=4 행을
    삭제하고 "incomplete" 를 기대할 뿐, `trigger_price` 를 어긋나게 하지 않는다.
    H4 커버리지 자체는 실재한다(`tests/adapters/sqlite/test_mapping_stage.py`) —
    그러나 **이 게이트 파일만 읽는 사람은 G2a 가 H4 를 검증한다고 잘못 결론짓는다.**
    결정: H4 어서션을 실제로 추가한다(이름만 고치는 것이 아니라). 이 게이트의
    체크리스트가 H1~H5 를 주장하므로 주장을 사실로 만드는 것이 맞고, 비용은
    `trigger_price` 하나를 어긋나게 하고 `CorruptRowError` 를 기대하는 몇 줄이다.
    **이 계획 전체를 관통한 교훈이 "계약과 코드를 일치시켜라" 였고, 게이트 자신이
    그것을 어기는 것은 특히 나쁘다.**
(2) [Minor] AST 의존 방향 검사가 **상대 import 를 놓친다.** 실증:
      `from ..adapters.sqlite import mapping` → `node.module = 'adapters.sqlite'`,
      `node.level = 2` → `'autotrading7s.adapters' in module` 은 False
    현재 코드베이스는 100% 절대 import 이므로 실질 위험은 낮지만, **아키텍처
    테스트에 구멍이 있는 것**이고 막는 비용이 두 줄이다. 같은 라운드에 접어 넣는다.
(3) [Minor] 미사용 `Ladder` import (파일 내 등장 1회 = import 뿐).
(4) [Minor] `orders_last_minute=orders % 10` 의 modulo 가 무의미하다(최대 6).
(5) [Minor] H1 이 한쪽 절반만 보인다 — "복원 실패" 만 있고 "호출자 버그" 가 없다.
    이 계획에서 반복된 결함이 정확히 그 비대칭이므로 양쪽을 보인다.
틀렸을 경우 비용: 낮다. 전부 테스트 파일 안이고 실행 코드는 건드리지 않는다.
Task 13: fix round 1/5 (Important 1 + folded minor 4, 0 open; commits a0b1509..ad1ba00, 643/643 통과, 커버리지 98.94%)
Task 13: 컨트롤러가 계획서의 G2a 통과 기준을 독립 전수 확인 — 15/15 통과:
  H1 예외 계층 / Decimal("0.1666") TEXT 왕복 후 7단계 발동가 동일 / H2 양방향 naive 거부 +
  tz-aware 유지 / 8테이블+holdings 뷰 / 마이그레이션 멱등 / 외래키 강제 /
  UNIQUE(cycle_id,stage_no) / client_ref UNIQUE / 스키마의 도메인 불변식 2개 /
  포트 17개 만족 / FakeBroker 포트 만족 / 4개 체결 모드 + PARTIAL 0주 아님 /
  TIMEOUT 이 접수 후 던짐
  (H3·H4·H5·G1 동등성·의존 방향·커버리지는 앞서 확인)
Task 13: 재리뷰 — 5건 전부 ADDRESSED. H4 어서션이 판별력 확인됨(완전성 검사를 먼저
  통과시킨 뒤 trigger_price 만 어긋나게 하므로 H4 분기에서만 발생, match 문자열이 둘을
  가른다), H3 절반도 온전, H1 의 TypeError 가 감싸이지 않고 올라오는 것 확인
Task 13: minor (deferred): AST 검사가 형제 형태 `from .. import adapters` 는 아직
  놓친다 (`node.module` 이 None → `"".split(".")[0]` 불일치). 수정 전 부분문자열 검사도
  동등하게 못 잡았으므로 새 구멍이 아니다 — 최종 리뷰 triage 대상.
Task 13: complete (commits f6c8762..ad1ba00, review clean, 1 fix round)

---

# 전체 태스크 완료 — 최종 전체 리뷰로 넘어간다
13/13 태스크 완료, 643 테스트, 커버리지 98.94%, 실질 결함 27건 처리

# 최종 전체 리뷰 (opus, 29커밋 268KB) — Critical 0, Important 12, 변이 스윕 23건

## 리뷰어가 내 검증 방식의 약점을 정확히 짚었다 (기록)
"컨트롤러가 16항목 게이트 체크리스트에서 확인했다고 보고한 세 항목(HOLDING 의 fill
필수 CHECK, cycle 의 UNIQUE, token_session 에 토큰 컬럼 없음)은 **출하된 스키마에
대해서는 참이지만 어떤 테스트도 그것을 지키지 않는다.** 체크리스트가 회귀 테스트로
인코딩되지 않고 눈으로 확인됐으므로, 그 초록색은 저장소에 남지 않는다 — 나중에 누가
지워도 스위트는 초록이다."
→ 맞다. 나는 15/15 통과를 보고했지만 그것은 **그 순간의 스냅샷**이었다. 체크리스트
항목이 코드의 성질을 주장한다면 확인은 테스트로 착륙해야 한다. 이번 수정 wave 에
그것을 반영한다.

## Ruling: 단 한 번의 수정 wave — Important 11건 수정, 1건은 2B 로 문서화하며 이관
직접 실증한 것:
  PARTIAL 이 PENDING·TERMINAL 어느 쪽에도 없다 → 잔량 65주가 살아있는데
    `load_pending_orders()` 가 0건 (재시작 복구가 그 주문을 못 본다)
  `load_pending_orders` 의 `sent_at` = `'2026-09-01T09:00:00+00:00'` (str) — 코덱 우회
  `save_cycle`(없는 사이클) → 반환 None, 예외 없음, 행 0개
  `set_config_status`(99999) → 반환 None, 예외 없음
  FORCED 사이클 저장 → `IntegrityError: CHECK constraint failed`
  `[tool.coverage.run] source = ['autotrading7s.domain']` — 어댑터는 floor 밖

수정하는 것 (11): PARTIAL 을 PENDING 에 추가 / `load_pending_orders` 에 타입 있는
`PendingOrderRow` DTO 도입(포트의 가장 위험한 메서드가 가장 덜 명세된 상태를 끝낸다) /
FORCED 관련 **거짓 서술 교정**(테스트 독스트링·어서션, `save_cycle` 독스트링) /
`save_cycle`·`set_config_status` 의 조용한 no-op 제거 / `set_config_status` 에 `at`
파라미터 / `apply_schema` 의 중간 버전 분기에서 raise / `token_session` 테스트를 정확
집합 비교로 / 못 박히지 않은 스키마 CHECK 4개에 IntegrityError 테스트 / `holdings` 뷰의
CLOSED 절 테스트 / AST 가드의 두 구멍 / 체결 수량·가격의 누적·가중평균 의미를 두 포트
독스트링에 명시 + 규약을 고정하는 테스트

**2B 로 이관하는 것 (1): `FakeBroker` 의 예수금·보유 거부 경로.**
근거: 거부를 추가하면 계약이 바뀐다 — 어떤 예외인가, `INSTANT` 는 여전히 체결하는가,
`fail_mode` 와 어떻게 상호작용하는가. **설계 없이 수정 wave 에서 붙이는 것이 이 계획이
27건을 고치며 배운 실패 방식이다.** 대신 `FakeBroker` 독스트링에 한계를 명시해
2B 가 놓칠 수 없게 하고 핸드오버로 기록한다.
틀렸을 경우 비용: 2B 의 총투입 상한·긴급청산 가드 테스트가 "거부할 줄 모르는
브로커" 를 상대로 초록이 된다. 그래서 독스트링과 핸드오버 양쪽에 적는다.

**D20 강제 종료는 능력이 아니라 정직성만 고친다** — 원래 룰링(도메인 함수 `force_close`
는 소비자인 Emergency Control Handler 와 함께 설계해야 한다)이 유효하다. 지금 `Cycle` 에
필드를 붙이면 그 핸들러 없이 계약을 먼저 굳히는 것이고, 그것이 Plan 1 에서 겪은
"계약이 소비자보다 먼저 정해져 어긋나는" 문제다.

## 정정: 내 변이 스윕이 거짓 음성을 냈다
나는 최종 리뷰가 지적한 5개 제약이 "수정 후에도 못 박히지 않았다" 고 보고했다.
**틀렸다 — 내 스크립트 버그였다.** 쉘 함수 안에서 heredoc 과 위치 인자를 섞어
변이가 적용되지 않은 채 pytest 가 돌았고, 게다가 이 프로젝트의 `-q` 설정이 요약
줄을 억제해 "passed" 문자열 검사도 무의미했다. 반환코드로 판정하도록 고쳐 다시
돌린 결과 **5건 전부 테스트가 잡는다**:
  HOLDING 의 fill 필수 CHECK → test_stage_state_holding_requires_fill_data
  D20 FORCED CHECK → test_cycle_forced_close_requires_testimony_and_residual
  cycle 의 UNIQUE(config_id, seq) → test_cycle_seq_is_unique_per_config
  stage_state 의 fill_price > 0 → test_stage_state_fill_price_must_be_positive
  holdings 뷰의 CLOSED 제외 → test_holdings_excludes_a_closed_cycle_even_with_holding_stages
구현자 보고가 맞았다. 교훈: 검증 스크립트의 음성 결과는 스크립트를 먼저 의심해야
한다 — 양성은 자기 검증적이지만 음성은 아니다.

## Ruling (wave 이후 신규): `save_stage` 에 체결정보 불변·전이 합법성 가드를 넣는다
배경 보안 리뷰가 커밋 후 `missing-invariant-financial-integrity` 를 지적했다.
실증한 비대칭 — Task 9 가 `order_log` 를 강화했지만 `stage_state` 는 그대로다:
  1단계 HOLDING 10,000×100 확정 → 같은 단계를 1×1 로 재저장 → **덧쓰기 통과**
  HOLDING → WAITING 재저장 → **역행 통과, 체결정보 소실**
  대조: `order_log` 는 같은 일을 `OrderLogInvariantError` 로 거부한다

**하중을 받는다:**
  도메인 전이표는 `HOLDING → WAITING` 을 **금지**한다(`HOLDING → ['SELL_PENDING']`).
  즉 그 저장은 호출자가 `StageState` 를 직접 만든 경우뿐이며, Plan 1 에서 가장 많이
  나온 결함 부류가 정확히 그것이다(`to_holding` 오적용 4건).
  그리고 결과가 무겁다: `target_price(fill_price, 5%)` 이므로
    fill_price=10,000 → 목표가 10,500원
    fill_price=1      → 목표가 **2원** → 어떤 가격에도 즉시 매도
  게다가 `holdings` 뷰의 수량·평단이 `stage_state` 에서 오므로 사용자에게 보이는
  포지션도 틀린다. (실현손익은 `order_log` 기준이라 영향 없음)

**가드가 정밀하게 정해진다** — 합법 전이를 조사한 결과:
  `HOLDING → SELL_PENDING` 체결정보 유지 / `after_sell` 은 비움(H5 의 이유) /
  `to_holding` 이 최초 기입
  → **어떤 합법 전이도 non-null 체결값을 다른 non-null 값으로 바꾸지 않는다.**
  그래서 `order_log` 에서 이미 검증된 규칙이 그대로 적용된다.

결정: 고친다. 이것은 같은 findings 에 대한 두 번째 wave 가 아니라 **wave 이후 도착한
새 입력**이며, 프로세스의 "한 번의 wave" 규칙은 루프 종료를 위한 것이지 새 보안
발견을 출하하라는 뜻이 아니다. 미루면 2B 가 "취득원가를 조용히 지울 수 있고 즉시
매도를 유발할 수 있는" 리포지토리 위에 엔진을 짓는다.
틀렸을 경우 비용: 가드가 너무 엄격하면 2B 의 정상 전이가 막힌다 — 그래서 합법 전이
4가지를 모두 통과시키는 테스트를 함께 요구한다.

## Ruling: 테스트를 고친다 (가드가 아니라) — 설계서 9절이 정한다
구현자가 가드를 느슨하게 만드는 대신 멈추고 물어 왔다. 옳은 판단이었고, 그가 제시한
근거도 정확했다: 다중 홉 도달성으로 완화하면 `HOLDING → SELL_PENDING → WAITING` 이
2홉으로 도달 가능하므로 **이 수정이 막으려던 구멍이 그대로 다시 열린다.**

설계서 9절이 어느 쪽이 옳은지 정한다:
  ④ stage_state UPDATE  WAITING → BUY_PENDING          ← **여기서 커밋**
  ⑤ broker.place_limit_order()
  ⑥ 체결 대기 → 전량체결 → stage → HOLDING (fill_price, fill_qty)
그리고 근거까지 적혀 있다: "발주보다 먼저 기록하고 커밋한다 ... **잘못 기록된 쪽이
잘못 잊힌 쪽보다 항상 낫다.**"

즉 `BUY_PENDING` 은 발주 **전에** 커밋되고 `HOLDING` 은 체결 후에 온다 — **두 번의
별도 저장**이며, 이것이 설계서의 요구다. `to_holding(to_buy_pending(...))` 를 합성한
뒤 한 번만 저장하는 테스트는 **엔진이 결코 만들어서는 안 되는 순서**를 모델하고 있다.
`domain/stage.py` 의 독스트링도 BUY_PENDING·SELL_PENDING 이 "비행 중 크래시를 저장된
상태에서 복구하기 위해" 존재한다고 적었는데, 저장하지 않는 테스트는 그 상태의 존재
이유를 검증하지 않는다.

결정: (a) 두 테스트가 각 홉을 저장하도록 고친다. 이것은 가드를 통과시키기 위한 양보가
아니라 **테스트를 설계서 9절의 실제 파이프라인에 맞추는 것**이며, G2a 게이트는 그 결과
더 강해진다 — §9 ④가 요구하는 "지속된 pending 상태" 를 이제 실제로 지나간다.
부수 효과로 가드가 설계서의 발주-전-기록 순서를 **강제**한다: 2B 의 엔진이 홉을
저장 없이 합성하면 가드가 잡는다.
틀렸을 경우 비용: 낮다. 결정(decide 결과)은 그대로이고 저장 횟수만 늘어나므로 G1
리터럴(433 / [4,3,2,1] / 7)은 변하지 않아야 한다 — 구현자가 확인한다.

## 최종 수정 wave 완료 (669 테스트, 커버리지 98.98%)
커밋: ab755a1(포트 계약) / 8e8fd74(스키마·마이그레이션·D20 테스트) /
      a5ef754(아키텍처 가드) / 80a8508(문서) / a6f8ad0(save_stage 불변식)

## handover → Plan 2B (핵심): D20 강제 종료의 쓰기 경로가 **통째로 없다**
구현자가 `force_sold` 우려를 남겼고 실증했다. 두 경로가 **모두** 막혀 있다:
  (1) `save_cycle(close_reason=FORCED)` → `IntegrityError`
      (스키마 CHECK 가 forced_close_reason·qty 를 요구하지만 `Cycle` 에 그 필드가 없다)
  (2) `save_stage(force_sold(...))` → `StageInvariantError: HOLDING → SOLD 는 허용되지
      않는 전이` (`force_sold` 는 전이표를 **의도적으로** 우회하는데 새 가드는
      전이표를 참조한다)
둘이 같은 방향을 가리킨다 — D20 은 스키마 컬럼만 있고 쓰기 경로가 없다. 원래 룰링
(도메인 함수 `force_close` 를 소비자인 Emergency Control Handler 와 함께 설계)이
그대로 유효하며, 2B 는 세 가지를 함께 정해야 한다:
  · `Cycle` 에 두 필드를 넣을지, 아니면 전용 포트 메서드를 둘지
  · 긴급청산의 단계 쓰기가 엄격해진 `save_stage` 를 지나갈지, 별도 경로일지
  · 강제 종료 후 잔여 주식이 `holdings` 뷰에서 사라지는 것을 그대로 둘지
    (`emergency_liquidation_log.qty_after` 에 기록됨)

**가드가 이 불일치를 드러낸 것이 오히려 이득이다.** 가드 전에는 `force_sold` 저장이
조용히 성공했을 것이고, 그러면 D20 의 단계 쓰기는 되는데 사이클 쓰기는 거부되어
**절반만 강제 종료된 상태**가 남았을 것이다.

## 내 실수: `save_stage` 가드가 `cancel_sell` 의 정상 경로를 막는다
재리뷰가 내 수정이 만든 결함을 찾았다. 나는 디스패치에서 이렇게 썼다:
  "합법 전이를 조사한 결과 **어떤 합법 전이도 non-null 체결값을 다른 non-null 값으로
   바꾸지 않는다**"
**틀렸다.** 나는 전이 헬퍼 네 개(`to_holding`·`to_sell_pending`·`after_sell`·
`cancel_buy`)만 열거하고 **다섯 번째인 `cancel_sell` 을 빠뜨렸다** — 그리고 그것이
정확히 그 일을 하는 함수다:
  `cancel_sell(state, *, remaining_qty)` → `replace(..., fill_qty=remaining_qty)`
  where `0 < remaining_qty <= state.fill_qty`
독스트링이 그것을 "**일상적인 경로**" 라고 명시한다: 한국 주식 주문은 당일에만
유효하므로, 부분체결된 매도의 미체결 잔량이 마감과 함께 취소되면 처음 보유 수량보다
적은 채로 HOLDING 에 복귀한다.

더 나쁜 것은 — **이것이 Plan 1 의 25개 결함 중 하나를 고친 함수다.** 원장 기록:
"cancel_sell 이 부분 매도 후 마감에 잔량이 만료됐을 때 낡은 fill_qty 를 남겨
과다매도로 이어졌다." 즉 `fill_qty` 축소는 그 결함의 **수정 결과**이고, 내 가드가
그 수정을 되돌리는 방향으로 막는다.

## Ruling: 가드를 정밀화한다 — `fill_price` 는 절대 불변, `fill_qty` 는 축소만 허용
전이 헬퍼 다섯 개를 이번엔 전부 조사했다:
  `to_holding`      : null → 값 (최초 기입)
  `to_sell_pending` : 유지
  `cancel_sell`     : **fill_qty 를 축소** (fill_price 는 유지)
  `after_sell`      : 둘 다 비움
  `cancel_buy`      : BUY_PENDING 출발이므로 체결정보가 null
→ **`fill_price` 를 다른 non-null 값으로 바꾸는 합법 전이는 없다** (절대 불변 유지)
→ **`fill_qty` 는 `SELL_PENDING → HOLDING` 에서만, 그리고 축소 방향으로만** 바뀐다
   (도메인 자신이 `0 < remaining_qty <= fill_qty` 로 제한한다)
증가 방향은 계속 막힌다 — 그것이 과다매도 쪽이다.
틀렸을 경우 비용: 이번엔 다섯 헬퍼를 전부 나열해 확인했으므로 낮다. 그래도 가드가
도메인 전이표보다 엄격하면 안 된다는 원칙을 테스트로 못 박게 한다 —
**모든 전이 헬퍼를 순회하며 각각의 결과가 `save_stage` 를 통과하는지** 검사하는
테스트를 요구한다. 그러면 여섯 번째 헬퍼가 생겨도 잡힌다.

## 가드 정밀화 완료 (커밋 0872290, 672 테스트, 커버리지 98.98%)
컨트롤러가 케이스별 새 DB 로 전수 확인:
  합법 8가지 전부 통과 — to_buy_pending / to_holding / to_sell_pending /
    **cancel_sell 105→50 (HOLDING 9480×50)** / after_sell→WAITING / after_sell→SOLD /
    cancel_buy / 변경 없는 재저장(매 틱의 정상 모드)
  거부 4가지 전부 차단 — HOLDING→WAITING 역행 / fill_price 9480→1 / fill_qty 105→1 /
    force_sold(HOLDING→SOLD, D20 우회)
구현자가 추가로 잡은 것: 기존 거부 테스트 하나가 두 필드를 동시에 바꿔서
`fill_price` 검사만 지워도 실패하지 않았다 — 필드별로 분리해 변이 검증을 통과시켰다.
그리고 모듈 순회 테스트가 `cancel_sell` 시나리오를 지우면 `missing: ['cancel_sell']`
로 실패하는 것을 확인했다 — **내 원래 실수를 잡을 테스트다.**

---

# Plan 2A 실행 종료
13/13 태스크 · 34커밋 · 672 테스트 · 커버리지 98.98% (domain+adapters+ports)
실질 결함 41건 처리 (태스크 27 + 최종 리뷰 12 + 보안 리뷰 1 + 내 지시가 만든 1)
룰링 19건 · 이연 minor 20건 triage 완료 · 핸드오버 3건
