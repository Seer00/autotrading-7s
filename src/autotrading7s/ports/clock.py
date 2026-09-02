"""시계 포트 — 설계서 5절 규칙 4, 7.2절.

시간을 주입 가능하게 만드는 이유는 "15:29에 갭하락이 오면?" 같은 시나리오를
테스트에서 재현하기 위해서다. 실제 장 운영시간·휴장일 판단 방법은 설계서
18.2절에 따라 구현 2단계에서 확정하며, 그때 KiwoomClock 이 이 포트를 구현한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...

    def is_market_open(self, at: datetime | None = None) -> bool:
        """``at`` (기본값: 현재) 이 정규장 운영시간 안인가."""
        ...
