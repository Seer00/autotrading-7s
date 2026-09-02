# AutoTrading 7s

키움증권 REST API 기반 세븐스플릿(7-Split) 자동투자 프로그램.

- 설계서: `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`
- 구현 계획: `docs/superpowers/plans/`

## 현재 상태

**Plan 1 (도메인 코어, G1) 완료.** 사다리 계산·호가 단위·상태기계·트리거 판정·
안전장치가 구현되어 있으며, 네트워크·DB·GUI 없이 전부 테스트로 검증된다.

미구현: 영속성(Plan 2), 키움 어댑터(Plan 3), GUI(Plan 4).

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
