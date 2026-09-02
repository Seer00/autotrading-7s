# SDD 원장 — Plan 2B (엔진 + G2)

계획서: `docs/superpowers/plans/2026-09-02-autotrading-7s-engine.md`
브랜치: `feat/engine`  ·  실행 방식: 인라인 (사용자의 서브에이전트 금지 지시에 따름)

각 룰링은 `무엇을 결정했는가 — 왜 — 틀렸을 경우의 비용` 형식이다.

---

## Task 1 — 큐 메시지 계약과 엔진 설정 · 완료 (`2a2d03d`)

697 테스트 통과 (기존 672 + 25). **이 커밋 이후 Plan 4(GUI)를 병행 착수할 수 있다.**

계획서대로 구현했고 벗어난 것은 없다.

---

## Task 2 — 안전장치 조립기 · 완료

### Ruling 1: `split_config.status` 는 `IDLE | ACTIVE` 두 값뿐이다

**무엇을 결정했는가.** 계획서가 `split_config.status` 에 `RUNNING`·`PAUSED` 를
쓴 것은 잘못이다. 그 컬럼의 스키마 CHECK 는 `('IDLE', 'ACTIVE')` 이고 설계서
12.1절(725행)도 `IDLE | ACTIVE` 라고 적었다. **일시정지는 `cycle.status` 에만
존재한다.** 설정 상태는 "이 설정이 사이클을 돌리고 있는가" 만 말한다.

귀결:
- `StartCycle` → 설정 `ACTIVE`
- `PauseCycle`·`ResumeCycle`·`StopCycle` → **사이클 상태만** 바꾼다 (설정은 ACTIVE)
- 사이클 종료(정상·긴급·강제) → 설정 `IDLE`
- 대사 불일치 `INTERNAL_MORE` → **사이클** `PAUSED`, 설정은 `ACTIVE` 유지

**왜.** 설계서와 스키마가 일치하고 계획서만 어긋난다. Plan 2A 에서 19개 룰링 중
15개가 이 모양이었고, 매번 설계서가 이겼다.

**틀렸을 경우 비용.** 없다 — 스키마 CHECK 가 다른 값을 애초에 거부하며, 그
거부가 이 결함을 즉시 드러냈다(Task 2 의 첫 테스트 실행에서 10건 실패).

### Ruling 2: `create_cycle` 뒤에 `cycle.start()` 를 부르지 않는다

**무엇을 결정했는가.** `SqliteRepository.create_cycle` 은 이미 `STARTING` 상태의
사이클을 삽입하고 반환한다. 계획서의 픽스처와 Task 11 `_start_cycle` 이 그 뒤에
`cycle_mod.start()` 를 부르는데, 그것은 `STARTING → STARTING` 이므로
`IllegalCycleTransition` 이다. `start()` 는 도메인 단독 경로(`IDLE → STARTING`)의
것이다.

**왜.** 리포지토리가 이미 그 전이를 수행했다. 두 번 부르는 것은 계획서가 도메인
API 와 리포지토리 API 의 경계를 잘못 읽은 것이다.

**틀렸을 경우 비용.** 없다 — 도메인 전이표가 즉시 예외를 던진다.

### Ruling 3: 픽스처를 처음부터 `tests/conftest.py` 에 둔다

계획서는 Task 2 가 `tests/engine/conftest.py` 를 만들고 Task 4 가 그것을
`tests/conftest.py` 로 올리게 했다. 이동이 처음부터 예정돼 있었으므로 바로
최종 위치에 둔다 — 이동 커밋 하나가 사라지고, 같은 시드가 두 곳에 복제될 창이
닫힌다. 틀렸을 경우 비용: 없다.

### Ruling 4: 픽스처의 매도 완료는 홉마다 저장한다

`after_sell(to_sell_pending(s))` 를 합성해 한 번만 저장하면 `save_stage` 가
`HOLDING → SOLD` 를 거부한다(없는 전이). 두 홉을 각각 저장한다 — 2A 핸드오버 9
가 예고한 그대로이며, 설계서 9절 ④의 홉별 커밋 요구와 같은 것이다.
틀렸을 경우 비용: 없다 — 가드가 즉시 드러낸다.
