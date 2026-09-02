"""테스트용 시계 — 시간과 장 운영 여부를 명시적으로 조작한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class FakeClock:
    current: datetime
    market_open: bool = field(default=True)

    def now(self) -> datetime:
        return self.current

    def is_market_open(self, at: datetime | None = None) -> bool:
        return self.market_open

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)

    def set_market_open(self, value: bool) -> None:
        self.market_open = value
