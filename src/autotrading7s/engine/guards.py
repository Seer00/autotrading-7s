"""안전장치 조립 — 설계서 6절·9절 ②.

`domain/guards.py` 의 판정은 상태 없는 술어다. 이 모듈은 그 술어에 컨텍스트를
공급하는 책임만 진다: 노출금액을 리포지토리에서 집계하고, 분당 주문 수를 센다.

**긴급청산은 이 모듈을 거치지 않는다.** `max_orders_per_minute=0` 이 매도를
막게 되고, 그것은 손절 없는 전략의 유일한 탈출구에 레이트 리미터를 거는 것이다
(Plan 1 핸드오버 1). `engine/emergency.py` 는 이 모듈을 import 하지 않으며,
그 사실을 테스트가 고정한다.

`compute_exposure` 는 `load_stages` 를 부르므로 `CorruptRowError` 가 올라올 수
있다. 여기서 잡지 않는다 — 손상된 사이클을 어떻게 처리할지는 `recovery` 와
`orchestrator` 의 정책이며, 노출 집계가 0 을 반환하며 조용히 넘어가면 한도가
사라진다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain import pnl
from autotrading7s.domain.guards import (
    GuardContext,
    GuardVerdict,
    check_buy,
    check_sell,
)
from autotrading7s.domain.rules import BuyStage, SellStage
from autotrading7s.ports.repository import RepositoryPort


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.tzinfo.utcoffset(at) is None:
        raise ValueError(f"timestamp must be tz-aware: {at!r}")


class OrderRateWindow:
    """분당 주문 수를 세는 슬라이딩 윈도우.

    경계는 `now - at < window` 다 — 정확히 60초 전의 주문은 '지난 1분'에 들지
    않는다. 포함하면 허용 건수가 실질적으로 1건 좁아진다.
    """

    def __init__(self, window_sec: int = 60) -> None:
        self._window = timedelta(seconds=window_sec)
        self._stamps: deque[datetime] = deque()

    def record(self, at: datetime) -> None:
        _require_aware(at)
        self._stamps.append(at)

    def count(self, now: datetime) -> int:
        _require_aware(now)
        cutoff = now - self._window
        while self._stamps and self._stamps[0] <= cutoff:
            self._stamps.popleft()
        return len(self._stamps)


@dataclass(frozen=True, slots=True)
class Exposure:
    """종목별·전체 보유 원가. 한도 판정의 '누적' 쪽 값이다."""

    per_stock: dict[str, int] = field(default_factory=dict)
    total: int = 0


def compute_exposure(repo: RepositoryPort) -> Exposure:
    """활성 사이클 전부의 보유 원가를 집계한다.

    `pnl.invested_amount` 를 쓰므로 매도 완료된 단계는 빠진다 — 한도가
    제한하는 것은 동시 노출이며, 누적 지출을 제한하는 것이라면 재매수가
    허용된 설정에서 한도가 영구적으로 소진된다.
    """
    per_stock: dict[str, int] = {}
    for cyc in repo.load_active_cycles():
        stages = repo.load_stages(cyc.cycle_id)
        amount = pnl.invested_amount(stages)
        if amount == 0:
            continue
        code = repo.load_config(cyc.config_id).stock_code
        per_stock[code] = per_stock.get(code, 0) + amount
    return Exposure(per_stock=per_stock, total=sum(per_stock.values()))


class GuardGate:
    """가드 판정의 단일 진입점. 상태를 갖는 이유는 분당 주문 수뿐이다."""

    def __init__(self, repo: RepositoryPort, settings: EngineSettings) -> None:
        self._repo = repo
        self._settings = settings
        self._window = OrderRateWindow()

    def record_order(self, at: datetime) -> None:
        """발주를 시도한 시점에 부른다.

        한 틱이 여러 매도를 낼 수 있으므로 결정과 결정 사이에 불러야 한다
        (Plan 1 핸드오버 2).
        """
        self._window.record(at)

    def check_buy(
        self, decision: BuyStage, *, stock_code: str, stock_limit: int,
        now: datetime,
    ) -> GuardVerdict:
        exposure = compute_exposure(self._repo)
        ctx = GuardContext(
            stock_invested=exposure.per_stock.get(stock_code, 0),
            stock_limit=stock_limit,
            total_invested=exposure.total,
            total_limit=self._settings.total_limit,
            orders_last_minute=self._window.count(now),
            max_orders_per_minute=self._settings.max_orders_per_minute,
        )
        return check_buy(decision, ctx)

    def check_sell(self, decision: SellStage, *, now: datetime) -> GuardVerdict:
        """매도는 포지션을 줄이는 방향이므로 한도와 무관하다.

        그래도 노출을 집계하는 것은 낭비이므로 0 을 넣는다 — 도메인
        `check_sell` 이 빈도만 보기 때문에 결과가 같고, DB 왕복이 사라진다.
        """
        ctx = GuardContext(
            stock_invested=0, stock_limit=self._settings.total_limit,
            total_invested=0, total_limit=self._settings.total_limit,
            orders_last_minute=self._window.count(now),
            max_orders_per_minute=self._settings.max_orders_per_minute,
        )
        return check_sell(decision, ctx)
