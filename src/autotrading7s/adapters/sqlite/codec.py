"""원시 타입의 TEXT 왕복.

SQLite 에는 `Decimal` 이 없고, `REAL` 로 저장하면 float 가 되어 전역 제약
("금액·가격은 원 단위 int, 비율만 Decimal, float 금지")을 어긴다. 그래서 비율은
TEXT 로 저장한다(설계서 12.1절).

시각도 TEXT 다. 여기서 지켜야 하는 것이 H2 이며, Plan 1 의 Task 9 가 그 실패
모드를 확인했다 — tzinfo 없이 파싱된 시각이 쿨다운 계산에서 aware 시각과 만나
엔진 틱 루프 안에서 `TypeError` 를 낸다. 이 모듈은 **쓸 때도 읽을 때도** naive 를
거부하므로 그런 값이 도메인에 도달하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from autotrading7s.domain.errors import DomainInvariantError


def ratio_to_text(value: Decimal) -> str:
    """비율을 TEXT 로. 지수 표기를 쓰지 않는다."""
    if not isinstance(value, Decimal):
        raise TypeError(f"ratio must be Decimal, not {type(value).__name__}")
    # format(value, "f") 는 지수 표기를 쓰지 않고 유효자리를 보존한다.
    return format(value, "f")


def text_to_ratio(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise DomainInvariantError(f"not a valid ratio: {text!r}") from exc


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DomainInvariantError(
            f"{label} must be timezone-aware, got naive datetime: {value!r}"
        )


def dt_to_text(value: datetime) -> str:
    """시각을 ISO 8601 TEXT 로. naive 는 거부한다."""
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, not {type(value).__name__}")
    _require_aware(value, "datetime being stored")
    return value.isoformat()


def text_to_dt(text: str) -> datetime:
    """ISO 8601 TEXT 를 시각으로. 오프셋이 없으면 거부한다(H2)."""
    try:
        value = datetime.fromisoformat(text)
    except (ValueError, TypeError) as exc:
        raise DomainInvariantError(f"not a valid timestamp: {text!r}") from exc
    _require_aware(value, f"timestamp {text!r}")
    return value


def bool_to_int(value: bool) -> int:
    """SQLite 에는 BOOLEAN 이 없다. 0/1 변환을 저장소 경계에서 명시적으로 한다.

    Plan 1 은 `allow_rebuy` 가 진리값 해석으로 켜지는 것을 막았다(`"false"` 가
    재매수를 켜면 투입이 늘어나는 방향이다). 그 엄격함을 여기서도 유지한다.
    """
    if not isinstance(value, bool):
        raise TypeError(f"expected bool, not {type(value).__name__}")
    return 1 if value else 0


def int_to_bool(value: int) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected int, not {type(value).__name__}")
    if value not in (0, 1):
        raise DomainInvariantError(f"boolean column must be 0 or 1, got {value}")
    return value == 1
