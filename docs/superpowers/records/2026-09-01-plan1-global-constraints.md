# Global Constraints — AutoTrading 7s Plan 1 (도메인 코어)

계획서의 Global Constraints 절에서 그대로 옮긴 구속 요구사항.
모든 태스크의 요구사항에 암묵적으로 포함된다.

- **Python 3.12** 이상. `from __future__ import annotations` 를 모든 모듈 첫 줄에 둔다.
- **`domain/` 패키지는 표준 라이브러리 외 어떤 것도 import 하지 않는다.** `httpx`·`sqlite3`·`tkinter` 모두 금지. (설계서 7.2절 의존 규칙)
- **금액·가격은 원 단위 `int`, 비율만 `Decimal`.** `float` 를 금액 계산에 쓰는 것을 금지하며, `float` 를 받는 함수는 `TypeError` 를 던진다. (설계서 3.1절 — float 오차가 주문 수량을 바꿀 수 있음)
- **주문 요청 타입에 신용·미수 관련 필드를 정의하지 않는다.** (설계서 6절 — 원칙을 타입으로 강제)
- **`decide()` 에 하락 조건 매도 분기를 두지 않는다.** 자동 손절매 배제 원칙. (설계서 6절)
- **자동 트리거 경로는 시장가를 표현할 수 없다.** `LimitOrderRequest` 는 `price` 가 필수이며 `None` 을 허용하지 않는다. (설계서 8.2절)
- 분할 단계 수는 **2~7**. (설계서 3.1절)
- 매수 트리거 기준점은 **1단계 체결가 대비 누적** (`anchor × (1 - drop×(n-1))`). (설계서 D3)
- 호가 단위 정규화 방향: **매수 발동가는 내림, 목표 매도가는 올림.** (설계서 3.2절)
- 개발·테스트는 Linux EC2에서 수행한다. 모든 코드는 GUI 없이 동작하며 `pytest` 만으로 검증된다.
- 커밋 메시지는 한국어 본문 + Conventional Commits 접두어(`feat:` / `test:` / `chore:` / `docs:`).

## 트리거 판정 규칙 번호 (설계서 5절)

| 규칙 | 내용 |
|---|---|
| 규칙 1 | 한 틱에서 매도를 매수보다 먼저 평가. 매도가 하나라도 있으면 그 틱은 매도만 집행 |
| 규칙 2 | 한 틱에 매수는 1단계씩만, 낮은 번호부터 |
| 규칙 3 | 재매수 쿨다운 (기본 60초) |
| 규칙 4 | 장 운영시간 밖에서는 어떤 결정도 내리지 않음 |
| 규칙 5 | PENDING 상태 단계는 판정 대상에서 제외 |

## 컨트롤러 상시 판단 (모든 태스크에 적용)

1. "`from __future__ import annotations` 를 모든 모듈 첫 줄에 둔다"는 **"모듈 docstring 직후 첫 import"** 로 해석한다. 문자 그대로의 1행은 docstring이 있으면 Python 문법상 불가능하다.
2. 각 태스크의 "Expected: PASS (N tests)" 개수는 **참고값**이며 게이트가 아니다. 게이트는 "전체 PASS + 출력 청결(경고 없음)".
3. **브리프의 `Files:` 절에 나열된 파일만 만든다.** README·conftest·CI 설정은 뒤 태스크의 것이다. 단 브리프 본문의 실행 명령(`mkdir`/`touch`)이 명시적으로 지시하는 패키지 골격 파일은 예외로 허용한다(Task 1 판단).
4. 커밋 시 `git add`는 브리프가 지정한 경로만. `git add -A` 금지.
5. 외부 경계(주문 요청 타입)는 `isinstance(x, bool) or not isinstance(x, int)` 로 방어하고, 내부 계산 함수(`normalize_tick` 등)는 브리프가 쓴 검사를 그대로 유지한다. 두 패턴이 다른 것은 경계의 성격 차이이며 불일치가 아니다.

## 개발 환경

- 작업 디렉터리 `/home/ec2-user/capstone/trading-7s`, 브랜치 `feat/domain-core`
- Python은 반드시 `.venv/bin/python` (3.12.13). 시스템 `python3`는 3.9이며 `slots=True`에서 실패한다.
- pytest 9.1.1. `pip install -e .` 불필요 — pyproject의 `pythonpath = ["src"]`가 import를 해결한다.
