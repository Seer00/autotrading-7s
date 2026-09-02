"""엔진 설정 — 설계서 9절·10.2절의 조정 가능한 값들.

`total_limit` 에 기본값을 두지 않는 것이 이 모듈의 유일한 설계 결정이다. 손절매가
없는 전략에서 전체 총한도는 프로그램이 제공하는 유일한 구조적 보호장치이므로
(설계서 6절), 기본값이 조용히 적용되는 것은 무한 물타기를 묵인하는 것이다.

선언상의 기본값 `0` 은 `__post_init__` 이 즉시 거부한다. 즉 "기본값이 없다"를
dataclass 의 필드 순서 제약 안에서 표현한 것이며, 실질적으로 필수 인자다.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EngineSettings:
    total_limit: int = 0
    pending_timeout_sec: int = 3
    reconcile_interval_sec: int = 300
    max_orders_per_minute: int = 10
    rebuy_cooldown_sec: int = 60

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(
                    f"{field.name} must be int, not {type(value).__name__}"
                )
            if value <= 0:
                raise ValueError(f"{field.name} must be positive: {value}")


def load_settings(path: Path) -> EngineSettings:
    """`settings.toml` 의 `[engine]` 절을 읽는다.

    알 수 없는 키를 거부하는 이유: 오타난 설정 키가 조용히 무시되면 사용자는
    한도를 설정했다고 믿은 채로 기본값이 아닌 것으로 돌게 된다.
    """
    with path.open("rb") as fp:
        data = tomllib.load(fp)
    section = data.get("engine", {})
    known = {f.name for f in fields(EngineSettings)}
    unknown = sorted(set(section) - known)
    if unknown:
        raise ValueError(f"unknown settings keys in [engine]: {unknown}")
    if "total_limit" not in section:
        raise ValueError("total_limit is required in [engine] — 설계서 6절")
    return EngineSettings(**section)
