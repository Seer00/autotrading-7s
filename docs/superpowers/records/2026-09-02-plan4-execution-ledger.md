# SDD 원장 — Plan 4 (GUI)

계획서: `docs/superpowers/plans/2026-09-02-autotrading-7s-gui.md`
브랜치: `feat/gui`  ·  실행 방식: 인라인 (사용자의 서브에이전트 금지 지시에 따름)

---

## Task 1 — 스냅샷 계약 · 완료 (`9a598a4`)

계획서대로. 픽스처 헬퍼 하나가 `ladder=None` 오버라이드에서 터졌다(기본 단계를
사다리에서 만들기 때문) — 테스트 헬퍼의 버그이고 설계 문제가 아니다.

## Task 2 — 스냅샷 생성·발행 · 완료 (`d9b8ce8`)

계획서대로. 866 테스트.

---

## Task 3 — 설정 등록·수정 명령

### Ruling 1 (계획서의 사전 검사가 절반만 맞았다)

계획서는 `config.to_ladder(anchor_price=command.amount_per_stage)` 로 저장 전에
사용자에게 친절한 메시지를 주라고 했다. 그 판단은 맞다 — **`SplitConfig` 는
검증하지 않는 순수 DTO** 이고 모든 불변식이 `Ladder.__post_init__` 에 있으므로,
`to_ladder` 없이는 스키마 CHECK 의 `IntegrityError` 가 유일한 방어선이 되고 그
예외는 사용자에게 이유를 전달하지 못한다.

**그러나 계획서가 그 검사로 잡힌다고 적은 것 하나는 결코 잡히지 않는다.**
"1단계에서 1주도 못 산다" 규칙은 `amount_per_stage // trigger_price(1)` 인데
앵커를 `amount_per_stage` 로 두면 `amount // amount == 1` 이므로 **어떤 설정이든
통과한다.** 그 규칙은 실제 앵커에 달렸고 저장 시점에는 알 수 없다 — 미리보기가
사용자가 입력한 현재가로 그것을 보여주는 것이 실제 방어선이다. 계획서에 있던
그 테스트(`amount_per_stage=1` 로 거부를 기대)를 지웠다.

**추가로 두 값이 `Ladder` 의 검사 밖에 있다**: `rebuy_cooldown_sec` 과
`total_limit` 은 스키마에 CHECK 가 있지만 `Ladder` 는 보지 않는다. 직접 검사해
`ConfigRejected` 로 되돌린다 — 그러지 않으면 `sqlite3.IntegrityError` 가 포트
계약 밖으로 새고 사용자에게 이유가 전달되지 않는다.

**틀렸을 경우 비용.** 없다 — 검사가 늘어난 방향이며, 각각 테스트가 있다.

### Ruling 2: `update_config` 는 `created_at` 도 건드리지 않는다

계획서는 `status` 만 언급했다. 최초 등록 시각은 이력이며 수정이 그것을 덮으면
언제 만든 설정인지 알 수 없다. `config_to_row` 가 세 키를 모두 담으므로 셋을
전부 `pop` 한다. 그 사실을 테스트로 고정했다.
