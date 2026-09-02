# Plan 2A 실행에서 반복된 결함 패턴

최종 리뷰가 남은 코드에서 같은 패턴을 찾을 때 쓰라고 넘긴다.

## 1. 계약이 코드보다 넓게 쓰였다 (가장 많았다)

- `migrations.apply_schema` 독스트링이 "멱등" 을 약속했지만 직전 실행이
  완료됐을 때만 멱등이었다 (크래시 시 DB 가 기동 불가 상태로 남음)
- `update_order_log` 독스트링이 SQLite 가 제공하지 않는 원자성을 주장했다
  (`SELECT` 은 쓰기 락 밖에서 실행된다)
- `mapping.py` 모듈 독스트링이 "ValueError·TypeError 는 감싸지 않는다" 고
  적었으나 알 수 없는 enum 값(맨 ValueError)은 실제로 감싼다
- `json_to_ladder` 가 `except (JSONDecodeError, TypeError)` 로 호출자 버그를
  행 손상으로 위장시켰다 — 같은 파일의 형제 함수는 전부 계약을 지켰다
- G2a 게이트의 테스트 이름이 H4 검증을 주장했으나 본문은 H3 만 봤다

## 2. 두 방향 계약인데 한 방향만 지키거나 한 방향만 테스트했다

- `ratio_to_text` 는 float 를 막고 `text_to_ratio` 는 안 막았다 (58자 잡음 유입)
- 매핑의 감싸는 절반은 16건 테스트, 감싸지 않는 절반은 0건 → 결함이 초록불로 출하
- `rows_to_stages` 가 `cycle_id` 를 받고 대조하지 않았다 (중복 가드는 그 경우로만
  도달 가능했으므로 절반 방어 후 중단한 형태)
- 시뮬 브로커 실패 모드에서 거부 절반만 보고 허용 절반을 안 봤다

## 3. 사양 자체가 설계서와 어긋났다 (내 계획서의 오류)

- 실현손익 집계를 `status IN ('FILLED','PARTIAL')` 로 지정했으나, 설계서 200행의
  정상 절차(매수 부분체결 → 잔량 취소)가 만드는 CANCELED 행을 제외해
  **매입원가만큼 과대 계상된 이익**을 사용자에게 보고했다 (399,200 vs 19,200)
- 계약 DTO 를 어댑터 층에 두어 아키텍처 테스트를 약화시켜야 통과하게 만들었다
- `DISCONNECT` 가 REST 주문까지 막아 설계서 8.4절의 폴백을 검증 불가로 만들었다

## 4. 예외 계층의 MRO 를 확인하지 않았다

- `decimal.InvalidOperation` 은 `ArithmeticError` 계열이라 `except ValueError` 를
  통과한다 → NaN 비율이 어느 행인지 모르는 맨 예외로 표면화
- `asyncio.TimeoutError is TimeoutError` 이므로 `BrokerTimeout` 이 그것을
  상속하면 엔진이 브로커 타임아웃을 자기 것으로 삼킨다 (의도적으로 미상속)

## 5. 테스트가 존재하지만 아무것도 판별하지 못했다

- `holdings` 뷰의 절사 검증이 설계서 목업 수치로는 반올림과 같은 값(9,458)이라
  절사를 고정하지 못했다 (소수부 0.567 조합으로 교체)
- 실현손익의 상태 제외 테스트가 전부 `fill_price IS NULL` 인 퇴화 사례여서
  상태 필터를 지워도 통과했다
- `runtime_checkable` 의 `isinstance` 는 이름만 보므로 async 여부가 어긋난
  Stub 이 통과했다
