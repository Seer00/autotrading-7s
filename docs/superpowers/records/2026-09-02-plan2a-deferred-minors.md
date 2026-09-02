# Plan 2A 이연 항목 — 최종 전체 리뷰의 triage 대상

각 항목은 태스크 리뷰에서 Minor 로 분류돼 수정 루프에 들어가지 않았다.
최종 리뷰는 이 중 **병합 전에 고쳐야 하는 것**이 있는지 판단해야 한다.

- Task 1: minor (deferred): `cycle.py:193` `is_cycle_complete` docstring 이 아직 "ValueError 를 던진다" 로 읽힌다 (실제로는 DomainInvariantError)
- Task 1: minor (deferred): `ladder.py:63` 주석의 "LadderConfigError 는 ValueError 하위라서" 가 이제 한 단계 건너 참이다
- Task 1: minor (deferred): `tests/domain/test_errors.py` 의 `T0` 가 `__import__("datetime")` 인라인 — **계획서 본문이 그렇게 지시함**(내 계획서의 흠). Task 5 이후 테스트 파일들과 함께 정리 후보
- Task 2: minor (deferred): `tests/ports/test_broker.py` 의 함수 본문 안 `import dataclasses`/`import pytest` — **계획서 본문이 그렇게 지시함**
- Task 3: minor (deferred): `ports/repository.py:21` 의 `CloseReason` 이 미사용 import — **내 계획서 코드 블록에서 온 것**. 포트는 `Cycle` 을 주고받고 `CloseReason` 을 직접 이름 부르지 않는다. 계획서는 고쳤고(커밋 예정) 코드의 한 줄은 최종 리뷰에서 정리.
- Task 3: minor (deferred): 구현자 보고서가 메서드 수를 "18개" 로 적었으나 코드·테스트·브리프 모두 17개다. 코드는 정확하고 보고서 산술만 틀렸다.
- Task 4: minor (deferred): 브리프 본문이 "테스트 11건" 이라 적었으나 브리프의 테스트
- Task 4: minor (deferred): 새 테스트가 `migrations._SCHEMA_PATH`(비공개 심볼)에 손을 뻗는다. 실제 스키마와의 일치는 보장되지만 결합이 필요보다 강하다.
- Task 4: minor (deferred): `current > SCHEMA_VERSION` 거부 분기가 미테스트. SCHEMA_VERSION=1 이라 지금은 도달 불가.
- Task 8: minor (deferred): `tests/adapters/sqlite/test_repository_core.py` 가 `start` 를 import 하고 안 쓴다 — **계획서 코드 블록에서 온 것**
- Task 8: minor (deferred): `save_config`·`save_stage` 가 각자 `columns`/`placeholders` 를 만든다. 두 번뿐이라 지금 헬퍼 추출은 조급하다 — Task 9/10 에서 세 번째가 나오면 재검토
- Task 8: minor (deferred): 브리프 산문이 "14 tests" 라 적었으나 코드 블록에는 13개 — 내 계획서의 산술 오류(Task 4 와 같은 부류)
- Task 9: minor (deferred): 수정 보고서가 "중복 테스트 하나를 제거했다" 고 서술했으나
      amend 하지 않는다. minor (deferred).
- Task 10: minor (deferred): `SOLD` 상태 단계가 `holdings` 뷰에서 제외되는 것을 직접 검증하는 테스트가 없다 (`WAITING` 만 검증). **내 브리프의 테스트 목록에서 온 공백** — `seed()` 헬퍼가 `WAITING`/`HOLDING` 만 만든다. Task 13 의 G2a 게이트가 전 사이클을 재생하며 SOLD 를 만들므로 부수적으로 덮일 가능성 — 최종 리뷰에서 확인.
- Task 10: minor (deferred): `cy.status == 'CLOSED'` 사이클이 `holdings` 에서 제외되는 것도 미검증 — 같은 부류
- Task 10: minor (deferred): `holdings()` 에서 `cycle_id` 만 `int(...)` 로 감싸지 않았다 (다른 정수 필드는 감쌈). 무해하나 일관성 없음
- Task 11: minor (deferred): PARTIAL·DELAYED 체결의 예수금 반영이 미검증 (`_fill` 은
- Task 11: minor (deferred): `get_price` 와 `_current_price` 가 같은 본문 중복
- Task 11: minor (deferred): `subscribe_quotes(codes)` 가 `codes` 를 무시한다 (단일 종목
- Task 13: minor (deferred): AST 검사가 형제 형태 `from .. import adapters` 는 아직
