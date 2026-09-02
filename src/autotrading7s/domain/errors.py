"""도메인 예외.

`DomainInvariantError` 는 "이 도메인 객체의 상태가 무효하다" 를 뜻한다. 호출 인자가
무효한 것(맨 `ValueError`)과 구분하는 이유는 Plan 2 의 매핑 계층이 둘을 다르게
다뤄야 하기 때문이다 — 복원된 행의 정합성 실패는 그 행을 지목하는 `CorruptRowError`
로 감싸 사용자에게 보이고, 호출자 버그는 그대로 올려 개발 중에 드러나게 한다.

`ValueError` 를 상속하는 이유는 하위 호환이다. Plan 1 의 테스트와 호출부가
`ValueError` 를 잡고 있으며, 그 기대를 깨지 않는다.
"""

from __future__ import annotations


class DomainInvariantError(ValueError):
    """도메인 객체의 상태가 무효할 때. 주로 `__post_init__` 이 던진다."""
