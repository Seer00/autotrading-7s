# 이월 Minor 목록 — 최종 전체 리뷰 트리아지용

각 항목은 태스크 리뷰 또는 재리뷰에서 Minor 로 판정되어 수정 루프에 넣지 않은 것이다.
최종 전체 리뷰는 이 중 머지 전에 고쳐야 할 것을 골라내야 한다.

- Task 1: minor (deferred): Tick·Holding·Balance·OrderAck·OrderStatus에 불변식
- Task 1: minor (deferred): `MarketSellRequest`에도 신용 필드 부재를 검사하는
- Task 2: minor (deferred): SELL 구간 경계 교차 테스트가 6개 중 2개만 있다
- Task 2: minor (deferred): `normalize_tick`을 bare `int`로 호출하는 테스트가 없다
- Task 2: minor (deferred): `normalize_tick(True, ...)`이 1을 반환한다. 사전 판단대로
- Task 3: minor (deferred): max_stages=2(최소 유효 경계)가 정상 생성되는지 검사하는
- Task 3: minor (deferred): test_ladder.py:153 주석 오류 — "1000*(1-0.1666*6)=0.004"로
- Task 4: minor (deferred): 비보유 상태(WAITING/BUY_PENDING/SOLD)에 묵은 체결정보가
- Task 4: minor (deferred): 불법 전이 매트릭스에 cancel_sell 케이스가 없다. 5개
- Task 5: minor (deferred): is_active가 True 케이스만 테스트된다. PAUSED·LIQUIDATING
- Task 5: minor (deferred): cycle.py:73 보간 없는 f-string (스타일)
- Task 5: minor (deferred): 구현 코드에 "FINDING B", "FINDING C", "FINDING F4" 같은
- Task 6: minor (deferred): pnl.py:143 _held()의 `s.fill_price is not None` 검사가
- Task 7: minor (deferred): rules.py의 `if qty <= 0: continue` 가 Ladder 불변식 때문에
- Task 7: minor (deferred): decide()가 중복 검출용으로 by_no를 만든 뒤 버리고
- Task 7: minor (deferred): 새 raise 블록의 연속줄 들여쓰기가 여는 괄호에 정렬되지
- Task 8: minor (deferred): rules.py의 `return list(sells)` 가 불필요한 복사다
- Task 9: minor (deferred): _require_aware(now, ...) 가 last_sold_at 이 있는 단계마다
- Task 10: minor (deferred): GuardContext.__post_init__ 이 필드명 6개를 하드코딩한
- Task 11: minor (deferred): test_fake_clock_satisfies_port 가 타입 주석만 하고
- Task 11: minor (deferred): stage.py:134(to_holding 의 fill 양수 검사)과
- Task 11: minor (deferred): task-11-report.md 의 커버리지 서술이 types.py:119 를
