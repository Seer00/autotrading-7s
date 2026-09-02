# Plan 2A → Plan 2B 핸드오버 제약

Plan 2A 실행에서 확정된 것. 각각 실증했고 원장에 근거가 있다.
(원장: `docs/superpowers/records/2026-09-02-plan2a-execution-ledger.md`)

## 1. D20 강제 종료의 쓰기 경로가 통째로 없다

두 경로가 **모두** 막혀 있다:

- `save_cycle(close_reason=FORCED)` → `IntegrityError`
  스키마 CHECK 가 `forced_close_reason`·`forced_close_qty` 를 요구하지만 `Cycle` 에
  그 필드가 없다.
- `save_stage(force_sold(...))` → `StageInvariantError: HOLDING → SOLD 는 허용되지
  않는 전이`
  `force_sold` 는 전이표를 의도적으로 우회하는데 `save_stage` 가드는 그 표를 참조한다.

2B 는 Emergency Control Handler 와 **함께** 세 가지를 정해야 한다:

- `Cycle` 에 두 필드를 넣을지, 전용 포트 메서드를 둘지
- 긴급청산의 단계 쓰기가 엄격해진 `save_stage` 를 지나갈지, 별도 경로일지
- 강제 종료 후 잔여 주식이 `holdings` 뷰에서 사라지는 것을 그대로 둘지
  (`emergency_liquidation_log.qty_after` 에 기록됨)

가드가 이 불일치를 **드러낸 것이 이득이다.** 가드 전에는 `force_sold` 저장이 조용히
성공했을 것이고, 사이클 쓰기만 거부되어 절반만 강제 종료된 상태가 남았을 것이다.

## 2. `cycle.realized_pnl` 을 포트로 쓸 수 없다

`realized_pnl_for_cycle` 이 값을 계산하고 그 독스트링이 "사이클 종료 시 엔진이
기록한다" 고 적었지만, `cycle_to_row` 가 그 컬럼을 의도적으로 제외하고 다른 어떤
메서드도 건드리지 않는다. 2B 가 포트 메서드를 추가해야 한다.

## 3. `order_log` 쓰기는 엔진 스레드의 단일 연결에서만

`update_order_log` 의 확인-후-갱신은 SQLite 가 직렬화해서 안전한 것이 아니다 —
Python `sqlite3` 는 `SELECT` 앞에서 트랜잭션을 열지 않는다(확인: `with conn:` 안에서
`SELECT` 직후 `in_transaction` 이 `False`). 안전한 이유는 (1) 설계서 7.1절의 단일
작성자 구조(GUI 는 큐로만 통신하고 DB 를 건드리지 않는다), (2) 이 메서드에 `await`
지점이 없어 5개 asyncio 태스크 사이에서도 양보하지 않는다는 것이다.

두 번째 쓰기 연결이 생기면 가드를 `UPDATE` 의 `WHERE` 절로 옮기거나
`BEGIN IMMEDIATE` 를 써야 한다.

## 4. `FakeBroker` 는 거부할 줄 모른다

예수금 검사도 보유 검사도 없다 — `_cash` 가 음수가 되고, 보유 0 인 종목의 매도가
현금을 늘린다. **총투입 상한과 긴급청산 가드를 이 브로커로 검증하면 아무것도
검증하지 않는 것이다.** 2B 는 그 가드를 쓰기 전에 거부 모드를 추가해야 하며, 그때
계약을 정해야 한다: 어떤 예외인가, `INSTANT` 는 여전히 체결하는가, `fail_mode` 와
어떻게 상호작용하는가.

Plan 2A 가 이것을 의도적으로 이관한 이유: 거부를 붙이는 것은 계약 변경이고, 설계
없이 수정 wave 에서 붙이는 것이 이 계획이 41건을 고치며 배운 실패 방식이다.

## 5. 스키마는 버전 1 을 넘는 마이그레이션 경로가 없다

`CREATE TABLE IF NOT EXISTS` 는 기존 테이블을 변경하지 못한다. `apply_schema` 는
이제 `0 < current < SCHEMA_VERSION` 에서 `RuntimeError` 를 던진다 — 컬럼을 추가하려면
명시적 `ALTER TABLE` 단계를 먼저 만들어야 한다.

## 6. 체결 수량·가격의 의미론

`fill_qty` 는 **누적**이고 `fill_price` 는 **거래량 가중평균**이다. 두 포트
독스트링에 명시했고 테스트로 고정했다. 증분으로 쓰면 취득원가가 과소 계상되어
**사용자에게 보고되는 이익이 부풀려진다** — Plan 2A 최악의 결함과 같은 방향이다
(보고 399,200 / 진짜 19,200).

## 7. `load_stages` 는 fail-closed 이고 복구 API 가 없다

손상된 단계 행 하나가 사이클 전체를 로드 불가로 만든다(안전 기본값으로 옳다).
그러나 `delete_stage` 도, 격리도, 운영자 탈출구도 없다. 자동 손절매가 없는
프로그램이므로 2B 의 복구 경로는 `CorruptRowError` 에 대해 크래시 루프보다 나은
답이 필요하고, 사용자에게 나갈 길이 있어야 한다.

그리고 `CorruptRowError` 는 `ValueError` 의 하위이므로 **엔진 틱 루프에 넓은
`except ValueError` 를 두면 DB 손상을 삼킨다.**

## 8. `token_session` 은 접근자가 없다

테이블은 있지만 어떤 포트 메서드도 쓰지 않는다 — 설계서 13.1절의 감사 추적이
미구현이다. 추가할 때 포트 메서드가 앱키를 받아 **내부에서 해시**하도록 해서,
호출자가 원본 키를 `_hash` 컬럼에 넣을 수 없게 해야 한다.

## 9. `save_stage` 의 가드가 설계서 9절의 순서를 강제한다

`save_stage` 는 이제 도메인 전이표를 참조하므로, 두 홉을 합성한 뒤 한 번만 저장하면
거부된다. 설계서 9절 ④가 `BUY_PENDING` 을 **발주 전에** 커밋하라고 요구하므로 이것이
옳다 — 엔진은 각 홉을 저장해야 한다:

```
④ stage_state UPDATE  WAITING → BUY_PENDING   ← 여기서 커밋
⑤ broker.place_limit_order()
⑥ 체결 → HOLDING (fill_price, fill_qty)
```

`fill_price` 는 절대 불변이고, `fill_qty` 는 `SELL_PENDING → HOLDING`(즉
`cancel_sell`)에서 **축소만** 허용된다 — 한국 주식 주문은 당일에만 유효하므로
부분체결된 매도의 잔량이 마감에 취소되면 보유가 줄어드는 것이 일상적 경로다.
증가는 막힌다(과다매도 방향).
