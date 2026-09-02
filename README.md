# AutoTrading 7s

키움증권 REST API 기반 세븐스플릿(7-Split) 자동투자 프로그램.

- 설계서: `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`
- 구현 계획: `docs/superpowers/plans/`

## 현재 상태

**Plan 1 (도메인 코어, G1) 완료.** 사다리 계산·호가 단위·상태기계·트리거 판정·
안전장치가 구현되어 있으며, 네트워크·DB·GUI 없이 전부 테스트로 검증된다.

**Plan 2A (영속성 + 브로커 포트, G2a) 완료.** SQLite 리포지토리가 도메인 객체를
저장·복원하며, Plan 1 이 넘긴 제약 다섯 건을 리포지토리 경계에서 강제한다 —
복원 실패의 지목(`CorruptRowError`), tz-aware 시각, 완전한 단계 집합,
`trigger_price` 대조, `order_log` 기반 실현손익. 시뮬레이션 브로커가 체결·실패
모드를 재생해 모의투자로는 만들 수 없는 실패 경로를 검증한다.

**Plan 2B (엔진 + G2) 완료.** 시뮬레이션 브로커로 7단계 전 사이클과 설계서
15.2절의 실패 경로가 검증된다 — 갭하락 순차 매수, 매도 우선, 재매수 쿨다운,
미체결 3초 타임아웃, 부분체결 매수·매도 비대칭, 응답 타임아웃 후 중복 발주 없음,
발주 거부 시 상태 복구, WebSocket 끊김 시 REST 폴백, 대사 불일치 자동 정지,
재시작 복구, 긴급청산과 D20 강제 종료, 총한도 도달 시 매수 중단.

`python -m autotrading7s.cli --env mock --settings settings.toml --simulate ...`
로 GUI 없이 엔진만 돌릴 수 있다.

**Plan 4 (GUI) 완료 — 단, 화면 렌더링은 Windows 에서 확인해야 한다.**
`ui/` 가 순수 뷰모델(EC2 에서 전수 테스트)과 얇은 Tkinter 셸로 나뉘어 있다.
EC2 에는 `tkinter` 가 설치되어 있지 않아 위젯 파일은 import 조차 되지 않으므로,
그 경계를 `tests/test_g4_prep_gate.py` 가 강제한다 — 순수 층은 `tkinter`·DB 를
import 하지 않고, 위젯 층은 `domain`·`engine`·`ports`·`adapters` 를 import
하지 않는다. 위젯의 **배선**은 `tkinter` 스텁으로 검증된다(모든 이름 오류가
잡힌다); Tk 가 실제로 그리는 것은 검증되지 않는다.

설계서 14.1·14.2·14.3절 목업의 숫자가 그대로 재현된다 — 보유 316주 / 평균단가
9,458원 / 평가손익 -37,410원(-1.25%), 목표까지 열의 일곱 줄, 사다리 미리보기의
총투입 6,978,200원 / 여유 21,800원 / 전단계 평단 7,823원(-16.2%).

```bash
python -m autotrading7s --env mock --settings settings.toml --simulate 10000,9500   # GUI (Windows)
python -m autotrading7s.cli --env mock --settings settings.toml --simulate 10000,9500 --status  # headless
```

**Windows 검증 절차:**
`docs/superpowers/records/2026-09-02-plan4-windows-checklist.md`

미구현: 키움 어댑터(Plan 3).

## 개발

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest tests/ -v
python -m pytest tests/ --cov=autotrading7s.domain --cov-report=term-missing
```

## 설계 원칙

- `domain/` 은 표준 라이브러리 외 어떤 것도 import 하지 않는다. 테스트로 강제한다.
- 금액·가격은 원 단위 `int`, 비율만 `Decimal`. `float` 는 금지한다.
- 자동 트리거 경로는 시장가를 표현할 수 없다. 시장가는 긴급청산 전용이다.
- 주문 요청 타입에 신용·미수 필드가 존재하지 않는다.
- `decide()` 에 하락 조건 매도 분기가 없다. 자동 손절매는 전략 원칙상 배제한다.
