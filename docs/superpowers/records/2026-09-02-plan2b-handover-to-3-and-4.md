# Plan 2B → Plan 3·4 핸드오버 제약

Plan 2B 실행에서 확정된 것. 각각 실증했고 원장에 근거가 있다.
(원장: `docs/superpowers/records/2026-09-02-plan2b-execution-ledger.md`)

**Plan 3(키움 어댑터)과 Plan 4(GUI)는 파일이 겹치지 않으므로 병행할 수 있다.**
Plan 3 은 `adapters/kiwoom/` + `ports/repository.py`(항목 5·6)를 건드리고,
Plan 4 는 `ui/` 만 건드린다. 유일한 접점은 `app/engine_thread.py` 이며 Plan 4 는
그것을 **읽기만** 한다.

---

## Plan 3 (키움 어댑터 + 인증)

### 1. 브로커 예외는 이제 포트 계약이다

`ports/broker.py` 가 `BrokerError`와 그 하위 셋을 선언한다. **키움 어댑터는
반드시 이것들을 던져야 한다** — 엔진은 `adapters/` 를 모르며 이 세 타입으로만
분기한다.

| 예외 | 언제 | 엔진이 하는 일 |
|---|---|---|
| `BrokerTimeout` | 응답이 오지 않음 (접수 여부 불명) | **재발주하지 않고** `list_orders_today` 로 확인 (D12) |
| `BrokerRejected(code, message)` | 거래소가 명시적으로 거부 | `order_log` REJECTED, 단계를 WAITING 으로 복구 |
| `BrokerDisconnected` | 시세 스트림 단절 | REST 폴백 진입 (설계서 8.4절) |

`BrokerTimeout` 은 `TimeoutError` 를 **상속하지 않는다.** 어댑터가
`asyncio.wait_for` 를 쓰다가 `TimeoutError` 를 그대로 새어나가게 하면 엔진의
`except BrokerTimeout` 이 그것을 잡지 못하고 틱 루프가 죽는다 — **반드시
`BrokerTimeout` 으로 변환해서 던져라.**

### 2. `OrderStatus.filled_qty` 는 누적, `filled_price` 는 수량가중평균

엔진이 그 값을 그대로 `update_order_log` 에 넘기고, `realized_pnl_for_cycle` 이
`fill_price * fill_qty` 를 취득/처분 금액으로 직접 쓴다. **키움 응답이 증분으로
온다면 어댑터가 누적해야 한다.** 증분을 그대로 흘리면 취득원가가 과소 계상되어
사용자에게 보고되는 이익이 부풀려진다 — 이 프로젝트 최악의 결함과 같은 방향이다
(보고 +399,200 / 진짜 +19,200).

### 3. `get_balance` 는 "보유 0"과 "응답에 없음"을 구분해서 만들어야 한다

`engine/emergency.broker_qty` 가 `Balance.holdings` 에 종목이 **없으면 `None`**
을 반환하고, 엔진은 그것을 **긴급청산 중단 사유**로 쓴다. 근거: 긴급청산이 불리는
상황은 시스템 오작동이 의심되는 상황이고, "응답에 없음"은 "보유 0"의 증거가
아니다. 그 상태에서 사이클을 닫으면 실계좌에 주식이 남은 채 프로그램이 손을 뗀다.

**따라서 어댑터는 계좌가 아는 종목을 `qty=0` 항목으로 남기고, 모르는 종목은
빼야 한다.** 키움 응답이 0 수량 종목을 아예 생략한다면, 전량 매도된 종목의
긴급청산이 항상 `FAILED` 로 끝난다 — 그때는 이 계약을 다시 설계해야 한다.

### 4. `list_orders_today` 는 `client_ref` 를 되돌려줘야 한다

설계서 9절 ⑤의 UNKNOWN 분기가 **`client_ref` 대조로만** 접수 여부를 확인한다.
`OrderStatus.client_ref` 가 우리가 보낸 UUID 와 같아야 한다.

**키움 API 가 클라이언트 참조값을 에코하지 못하면 그 분기 전체를 다시 설계해야
한다.** 대안(주문 시각·종목·수량으로 추정)은 같은 단계를 두 번 사는 위험을
되살리므로, 그 경우 D12 를 어떻게 지킬지가 Plan 3 의 첫 설계 문제다. **이것을
가장 먼저 확인하라** — 나머지 배선보다 앞선다.

### 5. `token_session` 접근자가 아직 없다 (2A 핸드오버 8 그대로)

테이블은 있지만 어떤 포트 메서드도 쓰지 않는다. 추가할 때 포트 메서드가 앱키를
받아 **내부에서 해시**하도록 해서, 호출자가 원본 키를 `_hash` 컬럼에 넣을 수
없게 해야 한다. 앱키·시크릿·접근토큰은 `keyring` 에만 두고 DB 에 평문으로
저장하지 않는다.

### 6. 스키마는 버전 1 을 넘는 마이그레이션 경로가 없다 (2A 핸드오버 5 그대로)

`CREATE TABLE IF NOT EXISTS` 는 기존 테이블을 바꾸지 못하고, `apply_schema` 는
`0 < current < SCHEMA_VERSION` 에서 `RuntimeError` 를 던진다. 컬럼을 추가하려면
명시적 `ALTER TABLE` 단계를 먼저 만들어야 한다.

Plan 2B 는 이 제약을 우회했다 — 강제 종료 대사 기준선의 초기화 시점을 새 컬럼이
아니라 `reconcile_log` 의 `action_taken='BASELINE_RESET'` 행으로 표현했다.
Plan 3 이 컬럼을 추가할 필요가 생기면 그때 마이그레이션 프레임워크를 만들어라.

### 7. 시장가 주문의 즉시 체결은 `FakeBroker` 의 가정이다

`engine/emergency` 는 `place_market_sell` 직후 `get_order` 를 **한 번만** 보고,
`filled_qty < 요청수량` 이면 `PARTIAL` 로 보고하고 사이클을 `LIQUIDATING` 에
남긴다. `FakeBroker` 는 시장가를 즉시 전량 체결하므로 그 경로가 항상 성공한다.

**실제 키움에서는 발주 직후 조회가 `OPEN`(체결 전)으로 올 가능성이 높다.** 그러면
정상 청산이 매번 `PARTIAL` 로 보고되고 사용자가 버튼을 다시 눌러야 한다. Plan 3
은 이 지점에서 폴링(몇 회, 몇 초 간격)을 결정해야 하며, 그것은 계약 변경이므로
**설계로 다뤄라** — 수정 wave 에서 즉흥적으로 얹는 것이 이 프로젝트가 반복해서
배운 실패 방식이다.

### 8. `ClockPort` 구현이 필요하다

`is_market_open` 이 D16(장외 긴급청산 거부)과 규칙 4(장외 무동작)를 모두 결정한다.
설계서 18.2절은 이것을 "키움 문서 확인 후 결정(잠정: 설정 파일 + 시세 무갱신
감지)" 으로 미뤄뒀다. `FakeClock` 이 `market_open` 플래그 하나로 구현되어 있으니
같은 포트를 만족시키면 된다.

### 9. 설계서 18.2절의 미확정 값 8건

엔드포인트 경로, TR(api-id) 코드, 토큰 만료 시간, TR별 호출 제한, 연속조회
헤더(`cont-yn`/`next-key`), WebSocket 구독 프로토콜·체결통보 형식, 정규장 시간·
휴장일 판단, 미체결 사유(거래정지 판별). **외부 선행조건: 키움 API 사용승인.**

---

## Plan 4 (GUI)

### 1. 접점은 `app/engine_thread.EngineThread` 하나뿐이다

```python
thread.send(cmd)            # 일반 명령 → command_q
thread.send_priority(cmd)   # 긴급청산·강제 종료만 (타입이 강제한다)
thread.drain_events()       # root.after(200ms) 마다
thread.raise_if_failed()    # 아래 항목 2
```

**GUI 는 DB 를 건드리지 않는다** (설계서 14.4절). 그 규칙이 리포지토리의 단일
작성자 전제를 성립시킨다 — 두 번째 쓰기 연결이 생기면 `update_order_log` 의
확인-후-갱신이 더 이상 원자적이지 않다.

### 2. `raise_if_failed()` 를 주기적으로 확인해야 한다

엔진 스레드가 예외로 죽으면 아무도 보지 못한다. **조용히 죽은 엔진은 "프로그램이
켜져 있는데 트리거를 놓치는" 최악의 상태다** (설계서 18.1 리스크 6) — 사용자는
화면이 멈춘 것을 알아차리기까지 그 사실을 모른다. `drain_events()` 를 부르는
`root.after` 루프에서 함께 확인하고, 실패 시 화면에 눈에 띄게 표시하라.

### 3. 이벤트가 화면에 필요한 것을 전부 싣고 있다

| 이벤트 | 화면 |
|---|---|
| `TickUpdate` | 현재가. `holdings()` 의 행과 결합해 평가손익을 만든다 |
| `StageFilled` | 단계 상세 갱신 |
| `CycleClosed` | 종료 알림 + 실현손익 |
| `GuardBlocked` | 한도·빈도 거부 사유 |
| `OrderRejected` / `OrderUnknown` | **반드시 시각적으로 구분하라** (아래 4) |
| `ReconcileMismatch` | 배너 (설계서 10.2절) |
| `QuoteFallback` | 폴백 구간 표시 (`active` 로 진입·복귀 구분) |
| `EmergencyResult` | 긴급청산·강제 종료 결과 |
| `CycleLoadFailed` | **사용자에게 나갈 길을 줘야 한다** (아래 5) |
| `EngineStopped` | 엔진 정지 |

`holdings()` 뷰는 리포지토리가 제공하지만 **GUI 가 직접 부르지 않는다** — 필요한
경로는 엔진이 이벤트로 실어 보내거나, `engine_thread` 에 조회 명령을 추가해야
한다. 그 API 를 Plan 4 가 정하고, 그때도 GUI 는 DB 연결을 갖지 않는다.

### 4. `OrderUnknown` 과 `OrderRejected` 를 같은 색으로 그리면 안 된다

`OrderUnknown` 은 **재발주 금지 상태에서 조회로 확인 중**이고 `OrderRejected` 는
**복구가 끝난 상태**다. 합치면 사용자가 개입할 시점을 알 수 없다.

`UNKNOWN_UNRESOLVED`(확인 조회 자체가 실패)는 단계가 PENDING 으로 남고 재시작
복구까지 그대로 있다 — 그 단계는 화면에서 "확인 중" 으로 계속 보이며, 그것이
의도다. 사용자가 프로그램을 재시작하면 해소된다.

### 5. `CycleLoadFailed` 에 대한 사용자 탈출구가 필요하다

`load_stages` 는 fail-closed 이고 복구 API 가 없다 (2A 핸드오버 7). 엔진은 그
사이클을 `PAUSED` 로 격리하고 이벤트를 내지만, **그 다음에 사용자가 할 수 있는
일이 아직 없다.** 자동 손절매가 없는 프로그램이므로 Plan 4 는 최소한 그 상태를
분명히 보여주고 무엇이 손상됐는지(`detail` 에 테이블과 rowid 가 있다) 알려야 한다.

### 6. 확인 문자열은 정확히 이 값들이다

```python
FORCE_CLOSE_CONFIRMATION = "강제종료"       # ForceClose.confirmed_text
LIQUIDATE_ALL_CONFIRMATION = "전체청산"     # EmergencyLiquidate(scope="ALL")
```

`app/commands.py` 에 상수로 있으니 다이얼로그가 그것을 import 해서 쓰면 어긋날
수 없다. `ForceClose.reason` 은 비어 있으면 생성 자체가 실패한다 — 증언 기록을
타입이 강제한다 (설계서 11.4절).

### 7. `GuardBlocked.reason` 은 도메인이 만든 문자열이다

그대로 표시하라. 다시 서식하면 한도 숫자의 표현이 두 곳에 생기고, 도메인
테스트가 고정한 문구와 화면의 문구가 어긋난다.

### 8. `ResetReconcileBaseline` 의 UI 입구가 필요하다

설계서 11.4절이 "사용자가 그 주식을 처리한 뒤 기준선을 초기화하는 수단" 을
요구한다. 명령은 있고 엔진이 처리하지만 **누르는 곳이 없다.**

### 9. 사다리 미리보기는 엔진 없이 만들 수 있다

`SplitConfig.to_ladder(anchor_price)` 는 순수 함수다 (설계서 14.2절). 다만 입력
검증은 Plan 4 의 몫이다 — Plan 1 이 기록했듯, 사용자가 비율에 `NaN` 을 넣으면
`decimal` 내부 예외가 그대로 올라와 오류 메시지가 불친절하다.

### 10. 사이클 상태와 설정 상태를 혼동하지 말 것

`split_config.status` 는 `IDLE | ACTIVE` 두 값뿐이고 **"이 설정이 사이클을
돌리고 있는가" 만** 말한다. 일시정지·청산 중 같은 것은 `cycle.status`
(`STARTING|RUNNING|PAUSED|LIQUIDATING|CLOSED`)에 있다. Plan 2B 계획서가 이것을
잘못 읽어 `split_config.status` 에 `PAUSED` 를 쓰려 했고, 스키마 CHECK 가
막았다 (원장 Ruling 1).
