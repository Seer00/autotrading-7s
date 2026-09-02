"""잔고 대사 — 설계서 10.2절.

**자동 보정은 하지 않는다 (D13).** 내부 기록이 실계좌보다 많으면 매도 주문이
계속 거부되어 `SELL_PENDING` 무한 재시도에 빠진다. 그래서 멈추는 것이 안전하다.
반대로 프로그램이 내부 상태를 실계좌에 맞춰 고치면 단계별 체결가 정보가 조용히
조작되어 이후 모든 목표가 계산이 근거를 잃는다. 불일치는 **사람이 확인해야 하는
사건**이다.

이 모듈은 `save_stage` 를 부르지 않으며, 그 사실을 테스트가 참조 부재로
고정한다.

강제 종료된 수량은 기준에서 제외한다 (설계서 11.4절). 빼지 않으면 강제 종료
직후 매 5분마다 영구적으로 경고가 나고, 사용자가 그 경고를 무시하는 습관을
들이면 진짜 불일치도 무시된다.

멈추는 것은 **사이클**이다. `split_config.status` 는 `IDLE|ACTIVE` 두 값뿐이며
(설계서 12.1절·스키마 CHECK) "이 설정이 사이클을 돌리고 있는가" 만 말한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autotrading7s.app.events import Event, ReconcileMismatch
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain import pnl
from autotrading7s.domain.types import CycleStatus
from autotrading7s.engine.emergency import broker_qty
from autotrading7s.ports.broker import BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import CorruptRowError, RepositoryPort


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    stock_code: str
    internal_qty: int
    broker_qty: int
    baseline: int
    verdict: str
    action_taken: str | None


class Reconciler:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        emit: Callable[[Event], None],
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._emit = emit

    def reset_baseline(self, stock_code: str) -> None:
        self._repo.reset_forced_close_baseline(stock_code,
                                               at=self._clock.now())

    async def run_once(self) -> list[ReconcileReport]:
        balance = await self._broker.get_balance()
        at = self._clock.now()
        reports: list[ReconcileReport] = []
        for cyc in self._repo.load_active_cycles():
            config = self._repo.load_config(cyc.config_id)
            code = config.stock_code
            try:
                stages = self._repo.load_stages(cyc.cycle_id)
            except CorruptRowError:
                # 손상된 사이클의 격리와 사용자 통지는 복구·틱 루프의 책임이다
                # (2A 핸드오버 7). 대사가 그것을 중복해서 처리하면 같은 사건에
                # 두 개의 경로가 생기고, 어느 쪽이 PAUSED 를 만들었는지
                # 이력에서 구분되지 않는다.
                continue
            internal = pnl.held_qty(stages)
            reported = broker_qty(balance, code)
            baseline = self._repo.forced_close_baseline(code)
            # 응답에 없으면 0 으로 본다 — 대사는 경고를 내는 경로이므로
            # 긴급청산과 달리 여기서 멈출 이유가 없다. 결과가 INTERNAL_MORE 면
            # 그 사이클이 PAUSED 되고, 그것이 안전한 방향이다.
            actual = (0 if reported is None else reported) - baseline
            if actual == internal:
                reports.append(ReconcileReport(code, internal, actual,
                                               baseline, "MATCH", None))
                continue
            verdict = "INTERNAL_LESS" if internal < actual else "INTERNAL_MORE"
            action = None
            if verdict == "INTERNAL_MORE" and cyc.status is CycleStatus.RUNNING:
                self._repo.save_cycle(cycle_mod.pause(cyc))
                action = "PAUSED"
            self._repo.append_reconcile_log(
                checked_at=at, stock_code=code, internal_qty=internal,
                broker_qty=actual, verdict=verdict, action_taken=action,
            )
            self._emit(ReconcileMismatch(
                stock_code=code, internal_qty=internal, broker_qty=actual,
                verdict=verdict, action_taken=action, at=at,
            ))
            reports.append(ReconcileReport(code, internal, actual, baseline,
                                           verdict, action))
        return reports
