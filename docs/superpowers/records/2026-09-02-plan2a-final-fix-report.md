# Plan 2A 최종 리뷰 수정 라운드 (Fix Round 3) — 보고서

브랜치 `feat/persistence`. 11개 항목을 수정했고, 12번(FakeBroker 의 검증
부재)은 지시대로 문서화만 했다. 전체 테스트 660개 통과(기존 643 + 신규 17),
커버리지(도메인+어댑터+포트) 98.97%.

## 항목별 상세

### 1. `PARTIAL` 이 두 상태집합 사이에 낀 문제

`src/autotrading7s/adapters/sqlite/repository.py` 의 `_PENDING_STATUSES` 에
`"PARTIAL"` 을 추가했다(`("SENDING", "ACCEPTED", "PARTIAL", "UNKNOWN")`).

고정한 테스트:
- `tests/adapters/sqlite/test_repository_orders.py::test_partial_status_stays_pending`
  — PARTIAL 상태 주문이 `load_pending_orders()` 에 나타남을 직접 확인.
- `tests/adapters/sqlite/test_repository_orders.py::test_pending_and_terminal_statuses_partition_the_schema_vocabulary`
  — `_PENDING_STATUSES`∪`_TERMINAL_STATUSES` 가 `schema.sql` 의
  `order_log.status` CHECK 에서 정규식으로 뽑아낸 7개 값과 정확히 같고,
  두 집합의 교집합이 빈 집합임을 확인. 7개를 테스트에 하드코딩하지 않고
  스키마에서 직접 읽으므로, 스키마가 상태를 하나 더 추가하면 이 테스트가
  깨진다.

검증: `_PENDING_STATUSES` 에서 `"PARTIAL"` 을 빼고 두 테스트를 실행해 봤다
— `test_partial_status_stays_pending` 은 실패했고(PARTIAL 주문이
`load_pending_orders()` 에서 사라짐), 파티션 테스트도 합집합이 스키마의
7개보다 하나 적어 실패했다. 원복 후 재확인함.

### 2. `load_pending_orders` 가 codec 을 건너뛴 문제

`ports/repository.py` 에 `PendingOrderRow` 프로즌 데이터클래스를
`SplitConfig`·`HoldingRow` 옆에 추가했다. 필드: `order_log_id`,
`client_ref`, `broker_order_id`, `cycle_id`, `stage_state_id`,
`side`(`Side`), `path`(`OrderPath`), `req_price`, `req_qty`, `fill_price`,
`fill_qty`, `status`(`str`), `sent_at`(aware `datetime`).
`RepositoryPort.load_pending_orders` 의 반환형을 `list[PendingOrderRow]`
로 바꾸고 독스트링을 갱신했다.

`SqliteRepository.load_pending_orders` 를 `codec.text_to_dt`·
`Side(...)`·`OrderPath(...)` 를 거쳐 `PendingOrderRow` 를 만들도록 재작성
(`adapters/sqlite/repository.py`).

고정한 테스트:
- `test_load_pending_orders_decodes_through_the_codec` — `sent_at` 이
  tz-aware `datetime` 이고 `side`/`path` 가 enum 멤버임을 확인.

기존 테스트 변경(딕셔너리 접근 → 속성 접근, 반환형이 바뀌었으므로 불가피):
- `test_append_records_sending_status`: `pending[0]["client_ref"]` →
  `pending[0].client_ref` (2곳)
- `test_unknown_status_stays_pending`: `p["status"]` → `p.status`

### 3. D20 강제 종료 테스트의 거짓 주장

`tests/adapters/sqlite/test_mapping_config_cycle.py::test_cycle_round_trip_closed_forced`
를 `test_cycle_round_trip_closed_forced_restores_status_and_reason_only` 로
이름을 바꾸고 독스트링을 정정했다. `status`·`close_reason` 만 왕복함을
확인하고, `dataclasses.fields(Cycle)` 로 `forced_close_reason`·
`forced_close_qty` 필드가 `Cycle` 에 없다는 사실 자체를 명시적으로
assert 했다(회귀 시 필드가 생기면 이 assert 가 실패로 알려준다).

`Cycle` 에 필드를 추가하지 않았고 `force_close_cycle` 포트 메서드도
추가하지 않았다 — 지시대로 D20 의 계약은 Plan 2B 가 Emergency Control
Handler 와 함께 설계한다.

`SqliteRepository.save_cycle` 의 독스트링을 정정해, `FORCED` 사이클은 이
메서드로 쓸 수 없고(스키마 CHECK 가 `sqlite3.IntegrityError` 로 거부)
Plan 2B 가 도메인 필드·쓰기 경로를 함께 추가해야 함을 명시했다.

### 4. `save_cycle`·`set_config_status` 의 조용한 무동작

`ports/repository.py` 에 `RowNotFound(LookupError)` 를 `OrderLogNotFound`
옆에 추가했다(테이블마다 별도 예외를 두지 않고 하나로 통일).
`SqliteRepository.save_cycle`·`set_config_status` 모두 `cursor.rowcount
== 0` 이면 `RowNotFound` 를 낸다.

고정한 테스트(각 메서드마다 두 갈래):
- `test_save_cycle_raises_for_an_unknown_cycle_id` / `..._still_succeeds_for_a_real_id`
- `test_set_config_status_raises_for_an_unknown_config_id` / 기존
  `test_set_config_status` (실제 id 로 성공하는 경로는 이미 있었다)

### 5. `set_config_status` 가 벽시계를 읽는 문제

포트와 구현 모두 `set_config_status(self, config_id, status, *, at:
datetime)` 로 필수 키워드 인자를 추가했고, `datetime.now().astimezone()`
호출을 제거했다.

호출부 갱신: `tests/adapters/sqlite/test_repository_core.py::test_set_config_status`
가 `at=T0` 를 넘기도록 수정. 새 테스트
`test_set_config_status_does_not_take_the_wall_clock` 는 `at` 을 미래
시각으로 넘겨 `updated_at` 이 그 값 그대로 저장됨을 확인 — 벽시계였다면
이 assert 는 (거의 항상) 실패했을 것이다.

### 6. `apply_schema` 가 하지 않은 마이그레이션을 보고하는 문제

`migrations.py` 의 `apply_schema` 에 `if current > 0:` 분기를 추가해
`0 < current < SCHEMA_VERSION` 을 `RuntimeError` 로 거부한다. 모듈
독스트링에 "`CREATE TABLE IF NOT EXISTS` 로는 버전 1 을 넘는 업그레이드
경로가 없다" 는 한계를 명시했다.

고정한 테스트: `test_apply_schema_refuses_a_real_migration_it_cannot_perform`
— `SCHEMA_VERSION == 1` 에서는 이 구간에 들어올 정수가 없어(0 과 1 사이),
`monkeypatch` 로 `SCHEMA_VERSION` 을 2 로 올려 재현했다.

### 7. 토큰 테이블 보안 테스트가 스펠링 검사에 불과한 문제

`tests/adapters/sqlite/test_migrations.py::test_token_session_stores_no_token`
을 블랙리스트+부분집합에서 **정확한 집합 비교**(`cols ==
{"id","env","app_key_hash","issued_at","expires_at"}`)로 바꿨다. 스키마는
이미 올바르므로 코드 변경은 없다.

검증: 이 테스트에 `access_token TEXT` 컬럼을 임시로 추가해 실행해보면
(직접 실험, 파일은 원복함) 실패한다 — 정확한 집합 비교가 스펠링과 무관한
컬럼 추가를 잡는다는 것을 확인했다.

### 8. 스키마 제약 4개가 고정되지 않은 문제

`test_migrations.py` 에 4개 테스트를 `test_migrations.py:61-95` 의 모델대로
(원시 `INSERT` + `pytest.raises(sqlite3.IntegrityError)`) 추가했다:
- `test_stage_state_holding_requires_fill_data`
- `test_cycle_forced_close_requires_testimony_and_residual`
- `test_cycle_seq_is_unique_per_config`
- `test_stage_state_fill_price_must_be_positive`

검증(각 제약을 `schema.sql` 에서 실제로 지워 보고 대응 테스트가 실패하는지
확인, 이후 원복):
- HOLDING 체결값 CHECK 제거 → `DID NOT RAISE IntegrityError` 로 실패.
- D20 CHECK 제거 → 동일하게 실패.
- `UNIQUE(config_id, seq)` 제거 → 동일하게 실패.
- `fill_price > 0` CHECK 제거 → 동일하게 실패.

네 경우 모두 확인 후 `schema.sql` 을 백업(`cp`)과 `diff` 로 원상태임을
검증했다.

### 9. `holdings` 뷰의 `CLOSED` 제외가 고정되지 않은 문제

`schema.sql` 의 `holdings` 뷰에 `AND cy.status != 'CLOSED'` 를 지우면 왜
깨지는지 설명하는 주석을 추가했다(D20 잔량은
`emergency_liquidation_log.qty_after` 에 남고 이 뷰에는 없다는 점을
명시).

고정한 테스트:
`tests/adapters/sqlite/test_repository_logs.py::test_holdings_excludes_a_closed_cycle_even_with_holding_stages`
— stage_state 는 HOLDING 으로 두고 cycle 만 직접 SQL 로 `CLOSED` 로 만든
뒤 `repo.holdings() == []` 를 확인. G2a 게이트의 기존 assert 와 달리 이
케이스는 stage 상태 조건 하나로는 걸리지 않는다(HOLDING 이 그대로이므로).

검증: `AND cy.status != 'CLOSED'` 를 실제로 지우고 이 테스트를 실행해
`AssertionError`(빈 리스트가 아니라 CLOSED 사이클의 `HoldingRow` 1개가
나옴)로 실패함을 확인, 이후 원복.

### 10. 아키텍처 가드의 두 구멍

`tests/test_g2a_gate.py` 의 검사 로직을 `_find_forbidden_imports(source,
forbidden)` 함수로 추출해 재작성했다:
- `ast.Import` 는 이제 `node.names` 전체를 본다(기존은
  `node.names[0].name` 만 봐서 `import os, autotrading7s.adapters.x` 를
  놓쳤다).
- `ast.ImportFrom` 에서 `level > 0` 이고 `module` 이 없는 경우(예:
  `from .. import adapters`), 임포트되는 이름 자체를 금지 목록과 대조하는
  분기를 추가했다(기존은 `module` 문자열이나
  `module.split(".")[0]` 만 봐서 이 형태가 검사를 완전히 피해갔다).

`test_ports_and_adapters_import_only_inward` 는 이 함수를 실제 트리에
돌리도록 재작성됐고, 동작은 그대로다.

고정한 테스트(체커 자체를 합성 소스로 검증):
- `test_the_import_checker_catches_a_multi_name_plain_import` —
  `"import os, autotrading7s.adapters.x\n"` 가 잡히는지.
- `test_the_import_checker_catches_a_sibling_relative_import` —
  `"from .. import adapters\n"` 가 잡히는지.
- `test_the_import_checker_does_not_flag_an_unrelated_relative_import` —
  과잉 반응(금지되지 않은 이름까지 잡는 것)이 없는지.

세 테스트 모두 새 로직 없이(구 로직으로) 돌리면 첫 두 개가 실패함을
확인했다(구 로직 코드를 임시로 복원해 확인, 이후 새 로직으로 되돌림).

### 11. 체결 의미론 미문서화

세 곳에 "`fill_qty`/`filled_qty` 는 누적값, `fill_price`/`filled_price` 는
수량가중평균가" 를 명시했다:
- `ports/broker.py::BrokerPort.get_order`
- `domain/types.py::OrderStatus` (클래스 독스트링 신설)
- `ports/repository.py::RepositoryPort.update_order_log`

고정한 테스트:
`tests/adapters/sqlite/test_repository_orders.py::test_fill_qty_is_cumulative_not_incremental`
— PARTIAL(40주) 뒤 FILLED(누적 105주)로 갱신한 시퀀스에서, "증분으로
읽었다면" 나올 값(40+105=145주 매수 기준 손익)과 실제 누적 답이 다름을
보이고 누적 답이 맞음을 확인한다.

### 12. FakeBroker 의 검증 부재 (문서화만)

지시대로 거부 경로는 추가하지 않았다. `adapters/fake/broker.py` 의
`FakeBroker` 클래스에 독스트링을 신설해, 예수금·포지션 검증을 하지
않는다는 것과 Plan 2B 가 투입한도·긴급청산 가드를 이 더블로 검증하기
전에 거부 모드를 먼저 추가해야 한다는 것을 명시했다. 코드 로직은
변경하지 않았다.

## 자잘한 항목

- `ports/repository.py` — 미사용 `CloseReason` 임포트 삭제.
- `tests/adapters/sqlite/test_repository_core.py` — 미사용 `start` 임포트
  삭제.
- `tests/adapters/sqlite/test_repository_orders.py` — `SplitConfig` 를
  `adapters.sqlite.mapping` 대신 `ports.repository` 에서 임포트하도록
  변경.
- `pyproject.toml` — `[tool.coverage.run] source` 에
  `autotrading7s.adapters`·`autotrading7s.ports` 추가. 결과 커버리지
  98.97%(95% 이상, 우려 없음).
- `domain/cycle.py:193`(`is_cycle_complete` 독스트링) — "ValueError" →
  "DomainInvariantError(ValueError 의 하위)".
- `domain/ladder.py:63` 주석 — "LadderConfigError 는 ValueError 하위라서"
  → "DomainInvariantError 를 거쳐서만(간접적으로만) ValueError 의
  하위라서".
- `domain/cycle.py:126` `confirm_anchor` — 바른 `ValueError` 대신
  `DomainInvariantError` 를 던지도록 변경. 바른 타입을 단정하는 테스트는
  없었다(`tests/domain/test_cycle.py::test_confirm_anchor_rejects_anchor_not_matching_ladder`
  는 `pytest.raises(ValueError, ...)` 를 쓰므로 `DomainInvariantError`
  가 `ValueError` 의 하위인 한 그대로 통과) — 변경 없이 통과 확인.
- `ports/repository.py` — `load_config`/`load_cycle`/`load_stages` 에
  실패 모드(`KeyError`, `CorruptRowError`) 문서화, `CorruptRowError` 가
  `ValueError` 의 하위라 넓은 `except ValueError` 가 DB 손상을 삼킬 수
  있다는 경고 추가.

## 변경 파일

```
 pyproject.toml                                     |   2 +-
 src/autotrading7s/adapters/fake/broker.py          |  21 ++++
 src/autotrading7s/adapters/sqlite/migrations.py    |  21 ++++
 src/autotrading7s/adapters/sqlite/repository.py    |  77 ++++++++++++---
 src/autotrading7s/adapters/sqlite/schema.sql       |   5 +
 src/autotrading7s/domain/cycle.py                  |   8 +-
 src/autotrading7s/domain/ladder.py                 |   5 +-
 src/autotrading7s/domain/types.py                  |  10 ++
 src/autotrading7s/ports/broker.py                  |  11 ++-
 src/autotrading7s/ports/repository.py              | 102 +++++++++++++++++--
 tests/adapters/sqlite/test_mapping_config_cycle.py |  21 +++-
 tests/adapters/sqlite/test_migrations.py           | 108 ++++++++++++++++++++-
 tests/adapters/sqlite/test_repository_core.py      |  38 +++++++-
 tests/adapters/sqlite/test_repository_logs.py      |  22 +++++
 tests/adapters/sqlite/test_repository_orders.py    |  94 +++++++++++++++++-
 tests/test_g2a_gate.py                             |  92 +++++++++++++-----
 16 files changed, 574 insertions(+), 63 deletions(-)
```

(카운트는 `git diff --stat` 그대로.)

## 테스트·커버리지

`.venv/bin/python -m pytest` → **660 passed**(기존 643 + 신규 17).
`.venv/bin/python -m pytest --cov --cov-report=term-missing` → **총
98.97%**(도메인·어댑터·포트 전체, `pyproject.toml` 갱신 반영). 95% 바닥
위. `fail_under = 95` 를 낮추지 않았다.

신규 17개 테스트 목록(세보고 다시 확인함):
`test_repository_orders.py`(4) — `test_partial_status_stays_pending`,
`test_pending_and_terminal_statuses_partition_the_schema_vocabulary`,
`test_load_pending_orders_decodes_through_the_codec`,
`test_fill_qty_is_cumulative_not_incremental`;
`test_repository_core.py`(4) —
`test_set_config_status_raises_for_an_unknown_config_id`,
`test_set_config_status_does_not_take_the_wall_clock`,
`test_save_cycle_raises_for_an_unknown_cycle_id`,
`test_save_cycle_still_succeeds_for_a_real_id`;
`test_repository_logs.py`(1) —
`test_holdings_excludes_a_closed_cycle_even_with_holding_stages`;
`test_migrations.py`(5) —
`test_apply_schema_refuses_a_real_migration_it_cannot_perform`,
`test_stage_state_holding_requires_fill_data`,
`test_cycle_forced_close_requires_testimony_and_residual`,
`test_cycle_seq_is_unique_per_config`,
`test_stage_state_fill_price_must_be_positive`;
`test_g2a_gate.py`(3) —
`test_the_import_checker_catches_a_multi_name_plain_import`,
`test_the_import_checker_catches_a_sibling_relative_import`,
`test_the_import_checker_does_not_flag_an_unrelated_relative_import`.
(`test_mapping_config_cycle.py` 의 D20 테스트는 이름을 바꾼 기존
테스트라 신규 카운트에 넣지 않았다.)

## 기존 테스트 변경 사유

- `test_repository_orders.py::test_append_records_sending_status`,
  `::test_unknown_status_stays_pending` — `load_pending_orders()` 반환형이
  `dict` 에서 `PendingOrderRow` 로 바뀌어 딕셔너리 접근을 속성 접근으로
  바꿔야 했다.
- `test_repository_core.py::test_set_config_status` — `set_config_status`
  에 필수 키워드 `at` 이 추가되어 호출부를 갱신해야 했다.
- `test_mapping_config_cycle.py::test_cycle_round_trip_closed_forced` —
  이름과 독스트링이 실제로 검증하는 것과 달랐던 거짓 주장이라 이름을
  바꾸고 부재 확인 assert 를 추가했다(항목 3).
- `test_migrations.py::test_token_session_stores_no_token` — 블랙리스트를
  정확한 집합 비교로 바꿨다(항목 7).

## 우려 사항

없음. 커버리지는 바닥보다 훨씬 위(98.97%)이고, 11개 항목 모두 지시대로
구현했으며 12번은 지시대로 문서화만 했다. `Cycle`/포트에 새 필드나
메서드를 추가하지 않아 D20 계약을 Plan 2B 앞에 고정하지 않았다는 원칙도
지켰다.

---

## 추가 라운드 — `save_stage` 무방비 문제 (백그라운드 보안 리뷰, Fix Round 4)

11개 항목을 커밋한 뒤 백그라운드 보안 리뷰가 새로 찾은 결함: `save_stage`
가 `update_order_log` 가 받은 어떤 보호도 없었다. 재현(리뷰가 제시한 그대로,
아래에서 직접 재확인함):

```
stage 1 → HOLDING, fill 10,000 × 100        (확정, 저장됨)
save_stage(같은 단계, fill 1 × 1)            → 덮어쓰기 그대로 통과
save_stage(같은 단계, status=WAITING)        → 역행 그대로 통과, 체결값 소실
```

### 구현

`ports/repository.py` 에 `StageInvariantError(ValueError)` 를
`OrderLogInvariantError` 옆에 추가했다. `SqliteRepository.save_stage`
(`src/autotrading7s/adapters/sqlite/repository.py`) 에 두 불변식을
추가했다 — 기존 행이 있을 때만 검사하고(첫 저장은 그대로 통과):

1. **체결값 불변.** 저장된 `fill_price`/`fill_qty` 가 non-null 이고 새
   값도 non-null 이며 서로 다르면 거부. 같은 값의 재확인과 `None`(값
   지움, `after_sell` 이 하는 일)은 허용.
2. **전이 합법성.** 저장된 상태와 새 상태가 다르면 그 전이가
   `domain.stage._ALLOWED` 에 있어야 한다 — 표를 다시 베끼지 않고
   `from autotrading7s.domain.stage import _ALLOWED as _STAGE_TRANSITIONS`
   로 직접 가져다 쓴다(둘이 어긋날 수 없다). 같은 상태로의 재저장은 항상
   허용.

`ports/repository.py::RepositoryPort.save_stage` 의 독스트링도 이 계약을
명시하도록 갱신했다.

### 예외 이름을 나눈 이유 (요청받은 판단)

`StageInvariantError` 를 `OrderLogInvariantError` 와 별개로 두었다. 두
호출부를 다 보고 내린 판단이다:

- 두 테이블의 불변식 내용이 구조적으로 다르다 — `order_log` 는 종결
  상태 역행·체결값 덮어쓰기·`req_qty` 상한 세 가지를, `stage_state` 는
  체결값 덮어쓰기와(공유되는 부분) 도메인 전이표 준수(공유되지 않는
  부분)를 본다. 항목 4(`RowNotFound`)의 경우처럼 "이름 붙인 행이 없다"
  는 모든 테이블에서 문자 그대로 같은 사건이라 하나로 묶었지만, 여기는
  "이 갱신이 그 테이블 고유의 쓰기 규칙을 어겼다" 로 테이블마다 규칙의
  모양이 달라 하나로 묶으면 오히려 정보가 사라진다.
- Plan 2B 의 엔진이 두 예외를 다르게 다룰 가능성이 크다 —
  `OrderLogInvariantError` 는 브로커 응답(외부 데이터)의 손상을 뜻하고
  `StageInvariantError` 는 호출자가 `StageState` 를 잘못 합성한 것(내부
  버그)을 뜻한다. 하나로 묶으면 그 구분을 캐치 지점에서 다시 문자열
  파싱으로 복원해야 한다.

### 두 기존 테스트와의 충돌 — 조정 후 해결

가드를 넣자 `tests/adapters/sqlite/test_repository_core.py::test_save_stage_upserts`
와 `tests/test_g2a_gate.py::test_the_full_cycle_survives_a_database_round_trip`
이 실패했다 — 둘 다 `to_holding(to_buy_pending(...))`/`after_sell(to_sell_pending(...))`
를 메모리에서 합성해 한 번만 저장하는 패턴을 썼고, DB 관점에서는
WAITING→HOLDING·HOLDING→SOLD/WAITING 같은 단일 도약이 되어 전이표에
없었다. 멀티홉 도달성으로 가드를 완화하는 안은 검토 후 폐기했다 —
HOLDING→SELL_PENDING→WAITING 이 정확히 2홉이라 그 완화가 보안 리뷰가
잡으려던 HOLDING→WAITING 역행을 다시 열어준다(들어오는 WAITING 값에는
체결값이 없으므로 체결값 불변 검사도 못 잡는다). 이 충돌을 코디네이터에게
보고했고, 설계서 9절 ④·⑥(BUY_PENDING/SELL_PENDING 을 발주 전에 먼저
커밋한다, "잘못 기록된 쪽이 잘못 잊힌 쪽보다 항상 낫다")을 근거로
**"가드를 그대로 두고 두 테스트를 고친다"** 는 판정을 받았다.

두 테스트를 각 홉을 따로 저장하도록 고쳤다:

- `test_save_stage_upserts` — `to_buy_pending` 을 저장하고, 그 다음에
  `to_holding` 을 저장한다(2회 저장, 같은 (cycle_id, stage_no) 를 갱신하는
  것은 여전히 이 테스트의 목적 그대로다).
- `test_the_full_cycle_survives_a_database_round_trip`(`test_g2a_gate.py`)
  — 앵커 확정 지점과 `step()` 헬퍼의 매수·매도 분기, 총 세 곳에서 각
  홉을 따로 저장하도록 바꿨다. `step()` 에 설계서 9절 ④를 인용하는 주석을
  남겨, 나중에 누가 "단순화" 하며 두 홉을 다시 합치는 것을 막는다.

**G1 리터럴 불변 확인.** 결정 자체는 바뀌지 않았고 저장 횟수만 늘었으므로
`held_qty == 433`, 매도 순서 `[4, 3, 2, 1]`, 주문 7건이 모두 그대로임을
확인했다 — 세 assert(`tests/test_g2a_gate.py` 의 `assert held_qty(stages)
== 433`, `assert sold_order == [4, 3, 2, 1]`, `assert orders == 7`)를 코드
변경 없이 그대로 두었고, 테스트가 통과했다.

### 고정한 테스트 (모두 `test_repository_core.py`)

거부(각각 mutation kill 확인 — 가드를 지우면 `DID NOT RAISE
StageInvariantError` 로 실패함을 직접 재현·확인 후 원복):
- `test_save_stage_rejects_overwriting_a_settled_fill_with_a_different_value`
  — 체결값 불변 검사 블록을 지우고 재실행 → `Failed: DID NOT RAISE
  StageInvariantError`.
- `test_save_stage_rejects_a_domain_forbidden_transition` — 전이 합법성
  검사 블록을 지우고 재실행 → 동일하게 실패. (이 테스트가 정확히 보안
  리뷰의 HOLDING→WAITING 재현이다.)

허용(2B 가 깨지지 않으려면 계속 통과해야 하는 절반, 코디네이터가 특히
강조한 쪽):
- `test_save_stage_allows_resaving_an_identical_stage_unchanged`
- `test_save_stage_allows_waiting_to_buy_pending`
- `test_save_stage_allows_buy_pending_to_holding_writing_fill_data_first_time`
- `test_save_stage_allows_holding_to_sell_pending_carrying_fill_forward`
- `test_save_stage_allows_after_sell_clearing_to_sold`
- `test_save_stage_allows_after_sell_clearing_to_waiting`
- `test_save_stage_allows_cancel_buy_back_to_waiting`

추가로 원본 재현 스크립트(리뷰가 제시한 그대로)를 직접 돌려 확인했다 —
두 호출 모두 이제 `StageInvariantError` 를 낸다(위 두 예외 메시지:
"fill_price already 10000; refusing to overwrite with 1", "HOLDING →
WAITING 는 허용되지 않는 전이").

### 참고 — `force_sold` 와의 상호작용 (관찰, 차단 아님)

`domain/stage.py::force_sold` 는 긴급청산 전용으로 전이표를 의도적으로
우회한다(예: HOLDING 에서 곧장 SOLD). 오늘 시점에는 `force_sold` 결과를
`save_stage` 로 저장하는 경로가 코드에도 테스트에도 없어 충돌이 없었다.
Plan 2B 가 긴급청산 쓰기 경로를 설계할 때 `save_stage` 를 그대로 쓸지,
그 경로만을 위한 별도 메서드(또는 가드 우회)를 둘지 결정해야 한다 — 이번
라운드에서는 그 결정을 하지 않았다(계약을 소비자보다 먼저 고정하지 않기
위해).

### 변경 파일

```
 src/autotrading7s/adapters/sqlite/repository.py |  51 ++++++-
 src/autotrading7s/ports/repository.py           |  33 ++++-
 tests/adapters/sqlite/test_repository_core.py   | 171 ++++++++++++++++++++++--
 tests/test_g2a_gate.py                          |  28 +++-
 4 files changed, 267 insertions(+), 16 deletions(-)
```

### 테스트·커버리지 (이 라운드 이후)

`.venv/bin/python -m pytest` → **669 passed**(이전 660 + 신규 9: 거부 2 +
허용 7). 포커스 테스트 `tests/adapters/sqlite/test_repository_core.py`,
`tests/test_g2a_gate.py` 모두 통과.

### 우려 사항 (이 라운드)

없음. G1 리터럴은 움직이지 않았고, 두 예외 이름을 나눈 이유를 기록했으며,
`force_sold` 상호작용은 Plan 2B 의 몫으로 명시적으로 남겨뒀다.

---

## 추가 라운드 2 — `fill_qty` 규칙 정정 (코디네이터 지시의 오류 수정)

앞 라운드에서 구현한 `save_stage` 가드가 `fill_price` 와 똑같은 절대
불변 규칙을 `fill_qty` 에도 적용했다. 이것은 지시 자체의 오류였다 —
코디네이터가 "다섯 전이 도우미를 다 확인했다" 고 했지만 실제로는
`to_holding`·`to_sell_pending`·`after_sell`·`cancel_buy` 네 개만 확인하고
`cancel_sell` 을 빠뜨렸다. `cancel_sell` 이 정확히 `fill_qty` 를 바꾸는
도우미이고(`SELL_PENDING → HOLDING`, 잔량만큼 축소), 그 축소는 이전
계획의 과매도 결함(마감에 취소된 매도 잔량의 `fill_qty` 가 갱신되지 않음)
을 고친 경로다. 이 착오는 **새 발견이 아니라 이전 지시가 만든 회귀**다.

### 구현

`SqliteRepository.save_stage`(`src/autotrading7s/adapters/sqlite/repository.py`)
의 체결값 검사를 두 필드로 분리했다:

- `fill_price` — 그대로 절대 불변(변경 없음).
- `fill_qty` — 저장된 값과 새 값이 다를 때, `current_status is
  SELL_PENDING and stage.status is HOLDING and 0 < incoming_qty <
  stored_qty` 인 경우만 예외로 허용한다(`cancel_sell` 의 경로). 그 조건에
  못 미치면(다른 전이거나, 같은 전이라도 증가거나 같은 값이면 이미
  위에서 걸러짐) 여전히 거부한다.

`ports/repository.py::StageInvariantError`·`RepositoryPort.save_stage` 의
독스트링도 세 불변식(①`fill_price` 절대 불변, ②`fill_qty` 는
`SELL_PENDING → HOLDING` 축소만, ③전이 합법성)으로 갱신했다.

### 재현 확인 — 코디네이터가 제시한 정확한 시나리오

직접 스크립트로 재현했다(원문과 동일한 값):

```
HOLDING (9,480 × 105) → SELL_PENDING          저장됨
cancel_sell(remaining_qty=50) → save_stage    성공 (이전엔 StageInvariantError)
재로드: status=HOLDING, fill_qty=50, fill_price=9480
```

`test_save_stage_allows_cancel_sell_shrinking_fill_qty` 로 고정했다 — 왜
이 축소가 정당한지(당일 유효 주문, 마감 취소)와 이전 계획의 과매도
결함을 코멘트로 남겼다.

### 증가 방향은 여전히 거부 — 세 번째 거부 테스트

`test_save_stage_rejects_a_fill_qty_increase_via_sell_pending_to_holding`
을 추가했다 — 같은 `SELL_PENDING → HOLDING` 전이라도 `fill_qty` 가
저장된 값보다 **커지면** 여전히 `StageInvariantError` 를 낸다(과매도
방향이므로).

### "모듈이 직접 커버리지를 보증하는" 테스트

`test_save_stage_accepts_every_domain_transition_helper` 를 추가했다 —
`domain/stage.py` 에서 `inspect.getmembers` 로 공개 함수를 직접 가져와
(`to_buy_pending`·`to_holding`·`to_sell_pending`·`cancel_sell`·
`after_sell`·`cancel_buy`·`force_sold`, 총 7개) 각각에 적합한 시작
상태를 만들어 적용하고, `save_stage` 가 그 결과를 받아들이는지 확인한다.
`force_sold` 는 자신의 독스트링이 "전이표를 우회한다" 고 명시하는
긴급청산 전용 도우미라 `known_bypasses` 로 이름을 밝혀 제외했다(조용히
건너뛰지 않음). 나머지 6개는 전부 검사한다 — `after_sell` 은 목표가
`SOLD`·`WAITING` 둘이라 두 시나리오로 나눠 둘 다 검사한다.

이 테스트가 실제로 이번 착오를 잡는지 직접 확인했다: `cancel_sell`
시나리오 호출을 지우고(사람이 그 도우미를 빠뜨린 것과 같은 상황을
재현) 실행하면 —

```
AssertionError: domain/stage.py 에 이 테스트가 모르는 전이 도우미가
있다: ['cancel_sell'] — ...
assert {'cancel_sell'} == set()
```

로 즉시 실패한다(파일 원복 확인). 이것이 코디네이터의 나열 누락을 잡을
바로 그 검사다.

### 기존 거부 테스트 하나 수정 — mutation kill 검증 중 발견한 결함

`fill_price` 규칙을 지운 뒤 `test_save_stage_rejects_overwriting_a_settled_fill_with_a_different_value`
를 돌려보니 여전히 통과했다 — 그 테스트가 `fill_price` 와 `fill_qty` 를
동시에 다르게(각각 1) 바꿔서, `fill_price` 규칙이 없어도 `fill_qty`
규칙(105 → 1, 이 전이는 HOLDING→HOLDING 이므로 `cancel_sell` 예외에
해당하지 않아 여전히 거부)이 대신 걸려 거짓 통과를 만들었다. 이름을
`test_save_stage_rejects_overwriting_a_settled_fill_price_with_a_different_value`
로 바꾸고 `fill_qty` 는 저장된 값(105)과 맞춰 `fill_price` 규칙만
가르도록 고쳤다. 고친 뒤 다시 `fill_price` 규칙을 지우고 돌려 이번엔
정확히 `DID NOT RAISE` 로 실패함을 확인했다(원복함).

### 세 거부 테스트의 mutation kill 결과 (전부 재확인)

- `test_save_stage_rejects_overwriting_a_settled_fill_price_with_a_different_value`
  — `fill_price` 검사 블록 제거 → `DID NOT RAISE StageInvariantError`.
- `test_save_stage_rejects_a_domain_forbidden_transition` — 전이 합법성
  검사 블록 제거 → 동일하게 실패.
- `test_save_stage_rejects_a_fill_qty_increase_via_sell_pending_to_holding`
  — `is_the_sell_cancel_shrink` 를 무조건 `True` 로 바꿔(가드 무력화) →
  동일하게 실패.

모두 확인 후 `src/autotrading7s/adapters/sqlite/repository.py` 를
원복했다(diff 로 원상태 확인).

### G1 리터럴·기존 테스트 영향

결정 로직은 건드리지 않았고 `fill_qty` 규칙만 정교화했으므로
`held_qty == 433`·매도순서 `[4, 3, 2, 1]`·주문 7건은 이전 라운드와
동일하게 유지된다(재실행 확인). 이전 라운드에서 추가한 7개의 허용
테스트, 2개의 거부 테스트(이번에 그 중 하나를 개명·수정) 모두 여전히
통과한다.

### 변경 파일

```
 src/autotrading7s/adapters/sqlite/repository.py |  58 ++++++--
 src/autotrading7s/ports/repository.py           |  23 ++--
 tests/adapters/sqlite/test_repository_core.py   | 174 +++++++++++++++++++++++-
 3 files changed, 227 insertions(+), 28 deletions(-)
```

### 테스트·커버리지 (이 라운드 이후)

`.venv/bin/python -m pytest` → **672 passed**(이전 669 + 신규 3: 거부
1(증가 방향) + 허용 1(cancel_sell 축소) + 커버리지 보증 1). 커버리지
98.98%(95% 바닥 위, 낮추지 않음).

### 우려 사항 (이 라운드)

없음. 이번 정정은 새 발견이 아니라 앞선 지시의 누락(다섯 도우미
나열에서 `cancel_sell` 빠짐)을 고친 것이며, 그 종류의 누락이 다시
일어나도 이번에 추가한 모듈-구동 커버리지 테스트가 먼저 걸린다.
