# 최종 수정 보고서 — Plan 1 도메인 코어 (병합 전 단일 수정 웨이브)

- 브랜치: `feat/domain-core`
- 커밋: `d87d70f` (F1~F4), `16e217c` (이연 Minor 13건 + 최종 리뷰 Minor 4건)
- 테스트: **270 → 429 passing, 출력 청결(경고 0)**
- 커버리지: **98% (8 uncovered) → 99.81% (1 uncovered)**

---

## 1. 실행한 명령과 출력

### 기준선 (수정 전)

```
$ .venv/bin/python -m pytest tests/ -q
270 passed

$ .venv/bin/python -m pytest tests/ --cov=autotrading7s.domain --cov-report=term-missing
Name                                    Stmts   Miss  Cover   Missing
src/autotrading7s/domain/cycle.py          79      2    97%   76, 113
src/autotrading7s/domain/guards.py         35      0   100%
src/autotrading7s/domain/ladder.py         53      1    98%   47
src/autotrading7s/domain/pnl.py            26      0   100%
src/autotrading7s/domain/rules.py          98      2    98%   146, 175
src/autotrading7s/domain/stage.py          72      2    97%   92, 134
src/autotrading7s/domain/tick_size.py      22      0   100%
src/autotrading7s/domain/types.py         102      1    99%   119
TOTAL                                     487      8    98%
```

### 최종 (수정 후)

```
$ .venv/bin/python -m pytest tests/ -q
........................................................................ [ 16%]
........................................................................ [ 33%]
........................................................................ [ 50%]
........................................................................ [ 67%]
........................................................................ [ 83%]
.....................................................................    [100%]
429 passed in 0.50s

$ .venv/bin/python -m pytest tests/ --cov=autotrading7s.domain --cov-report=term-missing
Name                                    Stmts   Miss  Cover   Missing
src/autotrading7s/domain/__init__.py        0      0   100%
src/autotrading7s/domain/cycle.py          81      0   100%
src/autotrading7s/domain/guards.py         35      0   100%
src/autotrading7s/domain/ladder.py         66      0   100%
src/autotrading7s/domain/pnl.py            26      0   100%
src/autotrading7s/domain/rules.py         110      0   100%
src/autotrading7s/domain/stage.py          82      1    99%   117
src/autotrading7s/domain/tick_size.py      22      0   100%
src/autotrading7s/domain/types.py         109      0   100%
TOTAL                                     531      1    99%
Required test coverage of 95.0% reached. Total coverage: 99.81%
429 passed
```

**남은 미커버 1행**: `stage.py:117` — `_guard()` 안의
`raise IllegalStageTransition`. 여섯 전이 도우미 전부가 `_guard` 보다 먼저
`_require_source` 로 출발 상태를 확인하고, 각 도우미의 (출발, 목표) 쌍은
`_ALLOWED` 에 모두 존재한다. 따라서 공개 API 로는 도달할 수 없다. 삭제하지
않았다 — 이 표는 설계서 4.1절 전이도를 코드로 표현한 것이고 `_require_source`
와 다른 질문을 하는 의도된 이중 방어이며, `stage.py` 주석이 "중복이니
지운다고 지우면 안 된다"고 명시하고 있다. 사설 함수를 직접 호출하는 테스트로
행을 채우는 것은 커버리지 수치를 위한 테스트가 되므로 하지 않았다.

기준선에서 미커버였던 8행 중:
`cycle.py:76`(중복 raise) → F4 로 소멸,
`cycle.py:113`·`ladder.py:47`·`stage.py:134`·`types.py:119` → 이연 Minor 13번의
테스트로 커버, `rules.py:146,175` → 이연 Minor 11번으로 삭제.

---

## 2. Important 5건

### F1 — `Ladder` 의 float 금액·비율 (`domain/ladder.py`)

모듈 수준 헬퍼 `_require_int` / `_require_ratio` 를 추가하고
`Ladder.__post_init__` 맨 앞에서 호출한다. 기존 범위·값 검사보다 **먼저**
실행되므로 float 이 크기 비교에 닿지 않는다.

- `anchor_price`, `amount_per_stage`, `max_stages` → `int` 필수,
  `bool` 거절, 메시지 `"<name> must be int, not <T>"` (`LimitOrderRequest` 와 동일)
- `drop_pct`, `target_pct` → `Decimal` 필수, `float`·`int`·`bool` 모두 거절,
  메시지 `"<name> must be Decimal, not <T>"`
- 타입 오류는 `TypeError`, 값 오류는 기존 `LadderConfigError` 유지
- `target_price(fill_price, target_pct)` 에 같은 두 검사 추가 (+ `fill_price <= 0`
  는 기존대로 `ValueError`)

커버 테스트 (`tests/domain/test_ladder.py`): `test_rejects_non_int_money_fields`
(8건), `test_rejects_non_int_max_stages`(3), `test_rejects_non_decimal_ratio_fields`
(8), `test_type_check_runs_before_range_check`(5),
`test_target_price_rejects_non_int_fill_price`(3),
`test_target_price_rejects_non_decimal_target_pct`(3),
`test_target_price_rejects_nonpositive`.

**부수 효과 — 정정됨 (2026-09-02, 아래 8절 참조)**: 최초 보고서는 "리뷰어가
지적한 'TriggerParams 가 같은 float 을 들고 있으면 매 틱 TypeError' 경로는
`decide()` 의 목표율 대조가 float 을 불일치로 잡아내므로 닫힌다
(`Decimal("0.05") != 0.05` 는 참)"고 적었다. **이 추론은 틀렸다.** 그 부등식은
0.05 에서만 성립한다 — `Decimal` 은 `float` 과 **정확한 값**으로 비교되므로
2진수로 정확히 표현되는 비율은 서로 같다고 나온다(`Decimal("0.25") == 0.25`,
`0.5`, `0.125`, `0.0625` 모두 참). 그 비율에서는 대조 검사가 통과하고 float 이
`target_price` 까지 흘러가 매 틱 `TypeError` 를 낸다. 하나의 예시에서 일반
명제를 끌어낸 잘못된 추론이었고, 따라서 `TriggerParams` 자체의 타입 가드는
**필수였다.** 8절에서 추가했다.

RED:
```
$ .venv/bin/python -m pytest tests/domain/test_ladder.py --tb=no -q
..............FFFFFFFFFFFFFFFFFFFFFFFF...........FFFFFF.......   (30 FAILED)
FAILED ...::test_rejects_non_int_money_fields[10000.0-anchor_price]
FAILED ...::test_rejects_non_decimal_ratio_fields[0.05_0-drop_pct]
FAILED ...::test_target_price_rejects_non_decimal_target_pct[True]   (등)
```
GREEN: `62 passed` (파일 단위), 전체 스위트 `302 passed`.

### F2 — `decide()` 가 `state.trigger_price` 를 사다리와 대조하지 않았다

`domain/rules.py::_eval_buy` 에 검사 7 추가. 목록에 존재하는 각 단계에 대해
`state.trigger_price == ladder.trigger_price(stage_no)` 를 확인하고, 다르면
단계 번호·저장된 값·사다리가 계산한 값을 담아 `ValueError` 를 던진다.

- **위치**: `state is None → continue` 직후, 규칙 5(WAITING 필터) **앞**.
  목록에 없는 단계는 검사하지 않으므로 부분 목록은 계속 유효하고(Task 8·9
  테스트 유지), WAITING 이 아닌 단계도 발동가는 사다리에 고정되어 있어야
  하므로 함께 검사한다. 브리프의 "함수가 실제로 살펴보는 단계만" 보다 한
  칸 넓다 — 쿨다운으로 건너뛰는 단계까지 검사하려면 필터 앞이어야 하고,
  기존 테스트 churn 은 0이었다. 이 판단이 과하다고 보면 되돌리기는 한 줄
  이동이다.
- 건너뛰지 않고 던진다 — 손상 데이터를 조용히 숨기지 않는다.

`StageState.__post_init__`(`domain/stage.py`)에 정체성 필드 불변식 추가:
`stage_no >= 1`, `trigger_price > 0`, `planned_qty >= 0`, `rebuy_count >= 0`,
각각 `int`·`bool` 거절. 공통 헬퍼 `_check_int_field(name, value, minimum, phrase)`
가 타입 → 하한 순으로 검사한다(기존 `_check_fill_field` 와 같은 순서·이유).

커버 테스트: `tests/domain/test_rules_buy.py::test_stored_trigger_price_must_match_the_ladder`
(리뷰어의 재현 그대로 — 발동가 999,999, 틱 10,200), `..._mismatch_raises_instead_of_skipping`,
`test_partial_states_list_stays_legal`;
`tests/domain/test_stage.py::test_rejects_out_of_range_identity_fields`(6),
`test_rejects_non_int_identity_fields`(12), `test_accepts_zero_planned_qty_and_first_stage`.

RED: `20 FAILED` (정체성 필드 18 + 발동가 대조 2).
GREEN: 전체 `328 passed`. **기존 테스트 수정 0건** — 브리프의 예상대로 모든
생산자가 발동가를 사다리에서 가져오고 있었다.

### F3 — `BuyStage`·`SellStage` 의 불변식 부재

`domain/rules.py` 에 `_check_decision_fields()` 를 두고 두 타입의
`__post_init__` 에서 호출한다. `stage_no`·`limit_price`·`qty` 각각 양의 `int`,
`bool` 거절, 타입은 `TypeError` / 비양수는 `ValueError` — `LimitOrderRequest`
와 동일한 패턴·메시지.

커버 테스트 (`tests/domain/test_guards.py`): `test_decision_rejects_nonpositive_fields`
(18건), `test_decision_rejects_non_int_fields`(18),
`test_rejects_bypass_via_negative_limit_price`(리뷰어의 한도 우회 재현),
`test_rejects_zero_limit_price_market_order_encoding`.

RED: `38 FAILED`. GREEN: 전체 `366 passed`.

### F4 — `Cycle.__post_init__` 의 중복·약한 형태

계획서의 형태로 되돌렸다. 최종 구조(3검사, raise 중복 없음):

1. `RUNNING`·`PAUSED` 는 `anchor_price` 필수
2. `ladder is None and (RUNNING·PAUSED 이거나 anchor_price 가 있으면)` → 거부
3. **상태와 무관하게** 둘 다 있으면 `anchor_price == ladder.anchor_price`

`cycle_id`·`config_id`·`seq` 에 양의 `int` 검사 추가 (`TypeError`/`ValueError`).

**계획서 형태와의 한 가지 차이 — 보고 대상**: 계획서가 지정한 형태는 일치
검사만 무조건으로 만들고, 기존 `elif LIQUIDATING` 분기의 "앵커가 있으면
사다리도 필수" 규칙은 사라진다. 그런데 그 규칙에는 기존 테스트가 있다
(`test_cycle_liquidating_with_anchor_requires_matching_ladder`, `match="requires
ladder"`). 그 테스트의 **기대를 바꾸지 않기 위해** 규칙을 삭제하는 대신
LIQUIDATING 전용에서 **모든 상태 공통**으로 일반화했다(위 2번). 근거: 앵커는
`confirm_anchor` 에서 사다리와 같은 순간에 생기므로 "앵커만 있는 행"은 어느
상태에서든 손상이다. 결과적으로 중복 raise와 미커버 행은 계획서 의도대로
사라지고, LIQUIDATING 이 앵커·사다리를 **요구하지 않는다**는 성질도 그대로다
(앵커가 없으면 아무 검사도 걸리지 않는다). 지시를 문자 그대로 따르면 기존
테스트 하나를 삭제해야 했으므로, 그쪽이 옳다면 되돌려 주면 된다.

커버 테스트 (`tests/domain/test_cycle.py`):
`test_anchor_ladder_mismatch_is_rejected_in_every_status`(6개 상태 전부 —
리뷰어의 CLOSED·STARTING·IDLE 재현), `test_anchor_without_ladder_is_rejected_in_every_status`(6),
`test_matching_anchor_and_ladder_constructs_in_every_status`(6),
`test_rejects_non_int_identity_fields`(15), `test_rejects_nonpositive_identity_fields`(6),
`test_confirm_anchor_rejects_anchor_not_matching_ladder`.

RED: `27 FAILED` — 그 중 IDLE/STARTING/CLOSED 의 앵커 불일치 3건이 정확히
리뷰어의 재현. GREEN: 전체 `412 passed`.

---

## 3. 이연 Minor 13건 (지시 목록 그대로)

| # | 내용 | 커버 테스트 |
|---|---|---|
| 1 | `Holding.qty`·`avg_price` 를 음이 아닌 `int` 로 강제 (`types.py`). `Balance`·`OrderAck`·`OrderStatus` 는 손대지 않았다 | `test_types.py::test_holding_rejects_non_int_fields`(6), `..._rejects_negative_fields`(2), `test_holding_allows_zero_qty` |
| 2 | `MarketSellRequest` 신용 필드 부재 회귀 테스트 | `test_market_sell_request_has_no_credit_fields` |
| 3 | 매도 호가 구간 경계 6개 전부. 값 고정 + `result % tick_unit(result) == 0` 로 docstring 의 일반 성질까지 확인 | `test_tick_size.py::test_normalize_sell_crossing_unit_boundary_stays_valid`(6) |
| 4 | `max_stages=2` 구성 성공 | `test_ladder.py::test_accepts_minimum_stage_count` |
| 5 | 틀린 주석 `0.004` → `0.4` 수정 | (주석) |
| 6 | `cancel_sell` 의 잘못된 출발 3개(WAITING·HOLDING·SOLD)를 불법 전이 표에 추가 | `test_stage.py::test_illegal_transitions_are_rejected` |
| 7 | `is_active` 를 6개 상태 전부로 파라미터화 | `test_cycle.py::test_is_active_only_for_starting_and_running` |
| 8 | `cycle.py` 의 잉여 `f` 접두어 제거 (F4 재작성으로 해당 줄 소멸) | — |
| 9 | `FINDING`/`Finding` 라벨 **55개** 제거 (cycle.py 4, test_cycle.py 28, test_rules_buy.py 21, test_rules_rebuy.py 2). 각 docstring 을 검증하는 규칙 서술로 다시 씀. `grep -ri finding src tests` → 0 | (전면) |
| 10 | `pnl._held` 의 중복 술어 삭제 | 기존 pnl 테스트 전부 (동작 불변) |
| 11 | `rules` 의 조용한 skip 2개 삭제 | 아래 별항 |
| 12 | `assert isinstance(clock, ClockPort)` (`ClockPort` 는 `runtime_checkable`) | `test_fake_clock.py::test_fake_clock_satisfies_port` |
| 13 | 미커버 가드 4개 테스트 | `test_stage.py::test_to_holding_rejects_nonpositive_fill_in_transition_context`, `test_cycle.py::test_confirm_anchor_rejects_anchor_not_matching_ladder`, `test_ladder.py::test_rejects_nonpositive_target_pct`, `test_types.py::test_market_sell_request_rejects_nonpositive_qty` |

**11번 — 두 skip 의 도달 가능성 판단**: 둘 다 F1~F4 이후 **도달 불가**이며
삭제했다.
- `_eval_buy` 의 `qty <= 0`: `Ladder.__post_init__` 이 1단계에서 1주 이상 살 수
  있음을 확인하고, 발동가는 단계가 올라갈수록 낮아지므로 `planned_qty(n) >= 1`
  이 모든 단계에서 성립한다. 그 불변식이 깨지면 F3 가 추가한
  `BuyStage`("qty must be positive")가 터진다.
- `_eval_sells` 의 `fill_price is None or not fill_qty`: `status is HOLDING` 인
  분기 안이므로 `StageState.__post_init__` 이 둘 다 양의 `int` 임을 보장한다.
  깨지면 `target_price` 가 `TypeError` 로 터진다. (`state.fill_price` 의 정적
  타입이 `int | None` 이므로 `pnl.py` 와 같은 양식으로 `# type: ignore[arg-type]`
  만 남겼다.)

---

## 4. 최종 리뷰 Minor 4건

14. **`test_g1_gate.py`**: `held_qty(states) == 433` 으로 문자 그대로 고정,
    유도식은 주석으로 남김 (`100+105+111+117`).
15. **`pyproject.toml`**: `[tool.coverage.report] fail_under = 95` 추가.
    커버리지 실행 마지막 줄에 `Required test coverage of 95.0% reached` 가
    출력되는 것으로 게이트 작동을 확인했다.
16. **하락 매도 없음 행동 테스트**: `test_rules_sell.py::test_no_sell_on_decline_however_deep`
    — 7단계 전부 보유 상태에서 모든 체결가를 크게 밑도는 틱(3,500 / 100)에
    대해 `decide()` 가 `[]` 를 돌려준다.
17. **약한 단정 6개**: `assert "positive" in msg or "0" in msg` 형태를 전부
    정확한 메시지 단정으로 교체했다. 실제 메시지는 추측하지 않고 스크립트로
    출력해 확인했다:
    `"price must be positive: 0"`, `"price must be positive: -5000"`,
    `"Duplicate stage_no in states: 2"`, `"target_pct must be positive: 0"`,
    `"rebuy_cooldown_sec must be non-negative: -1"`,
    `"target_pct mismatch: ladder has 0.05, params has 0.03"`.
    같은 줄에 있던 다른 약한 단정(`"int" in msg.lower()`, 종목 불일치)도 함께
    정확한 메시지로 바꿨다.

---

## 5. 범위 밖으로 지킨 것

- `DomainInvariantError` 도입하지 않음 (Plan 2 로 이연)
- 중복 테스트 삭제·영/한 docstring 혼용 정규화 하지 않음
- `Balance`·`OrderAck`·`OrderStatus` 에 불변식 추가하지 않음
- git 히스토리 재작성 없음
- 기존 테스트의 **기대를 바꾼 곳은 없다.** 기존 테스트 수정은 라벨 제거와
  단정 강화뿐이며, 새 불변식을 통과시키기 위해 값을 조정한 테스트도 없었다
  (F1~F4 가 기존 테스트를 하나도 깨지 않았다).

## 6. 자체 검토에서 찾은 것

- `### ` 로 붙였던 테스트 섹션 주석을 기존 코드베이스 양식(`# `)으로 통일.
- `_eval_sells` 의 `state.fill_price` 정적 타입 불일치에 `# type: ignore[arg-type]`
  추가 (`pnl.py:invested_amount` 와 같은 양식).
- F2 검사 위치를 규칙 5 필터 앞으로 결정한 이유를 코드 주석과 위 3절에 명시.

## 7. 우려 / 남은 관찰

1. **`stage.py:117` (`_guard` 의 raise) 는 여전히 도달 불가**. 위 1절에 근거를
   적었다. 커버리지 게이트(95%)는 통과한다.
2. **`Decimal("NaN")` 은 비율 검사를 통과한다** — 범위 밖이므로 손대지 않았다.
   `target_pct=Decimal("NaN")` 은 `<= 0` 비교가 False 라서 `Ladder` 를 통과하고,
   `drop_pct=Decimal("NaN")` 은 `(0,1)` 범위 검사에서 거부된다. NaN 목표율은
   이후 `normalize_tick` 의 `int(Decimal("NaN"))` 에서 예외로 **크게** 터지므로
   조용한 실패는 아니지만(모든 틱에서 판정 자체가 멈춘다), 값 검사를
   `not (value > 0)` 형태로 바꾸면 등록 시점에 잡을 수 있다. `TriggerParams`
   도 같다. Plan 2 후보로 기록한다.
3. **F4 의 계획서 형태 대비 일반화** — 3절 F4 항목에 적은 대로, 기존 테스트를
   삭제하지 않기 위해 "앵커가 있으면 사다리 필수" 를 모든 상태로 넓혔다.
   판단이 다르면 되돌리기 쉽다.

---

# 8. 스코프 재리뷰 후속 수정 (2026-09-02)

- 커밋: `1eb8230` fix: TriggerParams 에 타입 가드 추가, Cycle 의 거울상 불변식 보완
- 테스트: **453 passing** (429 → +24), 출력 청결
- 커버리지: **99.81%** (미커버 1행 `stage.py:117`, 변동 없음 — 요구선 99% 이상 충족)

## 8.1 FINDING — `TriggerParams.target_pct` 가 `float` 을 받고, 대조 검사로는 못 잡는다

**재현 (수정 전 HEAD)**:

```
$ PYTHONPATH=src .venv/bin/python  (요약)
Decimal('0.05')   == 0.05    -> False   (안전)
Decimal('0.25')   == 0.25    -> True    <- 대조 검사 통과
Decimal('0.5')    == 0.5     -> True
Decimal('0.125')  == 0.125   -> True
Decimal('0.0625') == 0.0625  -> True
Decimal('0.03')   == 0.03    -> False
Decimal('0.1')    == 0.1     -> False

TriggerParams constructed with float: 0.25 float
decide() -> TypeError: target_pct must be Decimal, not float     (매 틱)
```

컨트롤러의 지적이 정확하다. 그리고 **F1 이 이 경로를 만들었다**: 수정 전에는
`target_price` 가 float 을 받아 부정확하게라도 계산했고, `_require_ratio` 추가가
그것을 예외로 바꿨다. 예외의 효과는 "그 종목은 아무것도 팔지 못한다" 이며,
손절매가 없는 전략에서 이는 나쁜 방향이다. F1 자체는 옳고, 그래서 가드가
`TriggerParams` 에도 있어야 한다.

**수정** (`src/autotrading7s/domain/rules.py`):

- `TriggerParams.__post_init__` 맨 앞에 타입 검사를 넣어 기존 값 검사보다 먼저
  실행한다.
  - `target_pct` → `_require_ratio` (`Decimal` 필수, `float`·`int`·`bool` 거절)
  - `rebuy_cooldown_sec` → `_require_int` (`bool` 거절)
  - `allow_rebuy` → **가드했다.** `bool` 이 아니면 `TypeError`. 근거:
    `_eval_buy` 가 `if not params.allow_rebuy` 로 진리값으로 읽으므로 문자열
    `"false"` 는 참이 되어 사용자가 끈 재매수를 켠다 — 투입을 늘리는 방향이다.
    `int` 도 거절한다: Plan 2 의 SQLite 는 boolean 을 0/1 로 돌려주므로, 그
    변환을 저장소 경계에서 명시적으로 하게 만드는 것이 "금액은 int, 비율은
    Decimal" 과 같은 규율이다. 메시지 `"allow_rebuy must be bool, not <T>"`.
- `_require_int`·`_require_ratio` 는 `ladder.py` 에서 **import** 했다. `rules` 는
  이미 `ladder` 에 의존하므로 새 의존 방향이 생기지 않으며, 관용구를 다시 쓰면
  두 곳의 메시지가 갈라진다. import 문에 그 근거를 주석으로 남겼다.

이로써 값 검사만 있고 타입 검사가 없던 11번째 타입의 비대칭이 사라졌다.

**커버 테스트**

| 요구 | 테스트 |
|---|---|
| 1 | `test_rules_buy.py::test_trigger_params_rejects_non_decimal_target_pct` — `0.05`·`0.25`·`0.5`·`0.125`·`0.0625`·`1`·`True` 파라미터화(7건). 2진수로 정확한 값들을 명시적으로 포함 |
| 2 | `test_rules_buy.py::test_trigger_params_accepts_decimal_target_pct` |
| 3 | `test_rules_rebuy.py::test_rejects_non_int_cooldown` (`60.0`·`True`·`Decimal(60)`), `test_rejects_non_bool_allow_rebuy` (`1`·`0`·`"false"`·`None`) |
| 4 | `test_rules_buy.py::test_float_target_pct_fails_at_construction_not_at_every_tick` — `Ladder(target_pct=Decimal("0.25"))` + `TriggerParams(target_pct=0.25)` 가 `TriggerParams` 구성에서 실패하며, `decide()` 는 호출되지 않는다. 대조 검사만으로 막히지 않았음(`Decimal("0.25") == 0.25`)도 같은 테스트에서 못박았다 |

## 8.2 MINOR — 사다리만 있고 앵커가 없는 `Cycle`

`src/autotrading7s/domain/cycle.py::Cycle.__post_init__` 에 거울상 검사를
추가했다: `anchor_price is None and ladder is not None` → `ValueError`
(`"Cycle status <S> with ladder requires anchor_price, got None"`).
`RUNNING`·`PAUSED` 는 첫 검사가 이미 앵커를 요구하므로 이 검사에 닿지 않는다.

**커버 테스트** (`tests/domain/test_cycle.py`):
`test_ladder_without_anchor_is_rejected` (IDLE·STARTING·LIQUIDATING·CLOSED 4건),
`test_neither_anchor_nor_ladder_still_constructs` (같은 4개 상태에서 둘 다 없는
구성은 여전히 성공).

## 8.3 실행한 명령과 출력

RED (구현 전, 커버 파일 3개):

```
$ .venv/bin/python -m pytest tests/domain/test_rules_buy.py \
      tests/domain/test_rules_rebuy.py tests/domain/test_cycle.py --tb=no -q
19 FAILED
FAILED ...test_rules_buy.py::test_trigger_params_rejects_non_decimal_target_pct[0.25]
FAILED ...test_rules_buy.py::test_float_target_pct_fails_at_construction_not_at_every_tick
FAILED ...test_rules_rebuy.py::test_rejects_non_int_cooldown[60.0]
FAILED ...test_rules_rebuy.py::test_rejects_non_bool_allow_rebuy[false]
FAILED ...test_cycle.py::test_ladder_without_anchor_is_rejected[CycleStatus.IDLE]
   (등 19건)
```

GREEN (구현 후):

```
$ .venv/bin/python -m pytest tests/domain/test_rules_buy.py \
      tests/domain/test_rules_rebuy.py tests/domain/test_cycle.py -q
........................................................................ [ 44%]
........................................................................ [ 88%]
...................                                                      [100%]
(163 passed)

$ .venv/bin/python -m pytest tests/
453 passed in 0.44s

$ .venv/bin/python -m pytest tests/ --cov=autotrading7s.domain --cov-report=term-missing
src/autotrading7s/domain/cycle.py          83      0   100%
src/autotrading7s/domain/guards.py         35      0   100%
src/autotrading7s/domain/ladder.py         66      0   100%
src/autotrading7s/domain/pnl.py            26      0   100%
src/autotrading7s/domain/rules.py         114      0   100%
src/autotrading7s/domain/stage.py          82      1    99%   117
src/autotrading7s/domain/tick_size.py      22      0   100%
src/autotrading7s/domain/types.py         109      0   100%
TOTAL                                     537      1    99%
Required test coverage of 95.0% reached. Total coverage: 99.81%
453 passed
```

## 8.4 보고서 정정

3절 F1 항목의 "부수 효과" 문장을 정정했다. 원문은 `decide()` 의 목표율 대조가
float 매개변수를 잡아내므로 `TriggerParams` 가드가 불필요하다고 주장하며
`Decimal("0.05") != 0.05` 하나를 근거로 들었다. 정정문은 (1) 그 부등식이 0.05
에서만 성립하고 2진수로 정확한 비율(`0.25`·`0.5`·`0.125`·`0.0625`)에서는
`==` 가 참이라는 사실, (2) 따라서 그 비율에서는 대조가 통과하고 float 이
`target_price` 까지 도달한다는 사실, (3) 그러므로 `TriggerParams` 가드가
필수였다는 결론을 적는다. 하나의 예시에서 일반 명제를 끌어낸 잘못된 추론이었다.

## 8.5 컨트롤러가 파킹한 3건 (확인)

`stage_no > ladder.max_stages` 의 매도 가능성, `RUNNING` 사이클의 빈 `states`
목록, `stage.py:117` 의 도달 불가 `_guard` raise — 세 건 모두 이 웨이브에서
손대지 않았다.
