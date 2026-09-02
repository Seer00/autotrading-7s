"""엔진 조립 — 설계서 7.1절.

명령 소비·시세 수신·트리거 판정·미체결 감시·잔고 대사가 하나의 이벤트 루프에서
협력적으로 돈다. 단일 작성자 구조이므로 리포지토리의 확인-후-갱신이 안전하다
(2A 핸드오버 3).

`priority_q` 를 `command_q` 보다 먼저 비운다. 이것이 설계서 6절 "긴급 기능의
즉시성" 을 구조적으로 보장하는 지점이다 — 일반 명령이 100건 쌓여 있어도
긴급청산이 먼저 처리된다.

큐는 `queue.Queue` 다. GUI 는 Tkinter 메인 스레드에서 `put` 하므로
`asyncio.Queue` 는 스레드 안전하지 않아 조용히 깨진다.

**규칙을 재구현하지 않는다.** 트리거 판정은 `rules.decide()` 가 전부 하고 이
모듈은 그 결과를 집행한다. 여기에 "PENDING 이면 건너뛴다" 같은 코드가 생기면
그것은 규칙 5 의 중복이다.

**주문 빈도 제한의 '지금' 은 틱의 시각이다.** 빈도는 시장 시간 기준으로 세는
것이 맞고, 시계를 쓰면 시세 스크립트만으로는 창이 미끄러지지 않아 11번째
주문부터 전부 막힌다.
"""

from __future__ import annotations

import asyncio
import queue
from collections.abc import Awaitable, Callable
from datetime import timedelta

from autotrading7s.app import commands as cmd
from autotrading7s.app.events import (
    CycleClosed,
    CycleLoadFailed,
    EngineStopped,
    Event,
    GuardBlocked,
    QuoteFallback,
    TickUpdate,
)
from autotrading7s.app.settings import EngineSettings
from autotrading7s.domain import cycle as cycle_mod
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.rules import BuyStage, TriggerParams, decide
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import (
    CloseReason,
    CycleStatus,
    StageStatus,
    Tick,
    TickSource,
)
from autotrading7s.engine.emergency import EmergencyHandler
from autotrading7s.engine.executor import Executor
from autotrading7s.engine.guards import GuardGate
from autotrading7s.engine.reconciler import Reconciler
from autotrading7s.ports.broker import BrokerDisconnected, BrokerPort
from autotrading7s.ports.clock import ClockPort
from autotrading7s.ports.repository import (
    CorruptRowError,
    RepositoryPort,
    SplitConfig,
)


class Orchestrator:
    def __init__(
        self, *, repo: RepositoryPort, broker: BrokerPort, clock: ClockPort,
        settings: EngineSettings, command_q: queue.Queue,
        priority_q: queue.Queue, event_q: queue.Queue,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        fallback_poll_sec: float = 1.0,
        max_fallback_rounds: int | None = None,
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._clock = clock
        self._settings = settings
        self._command_q = command_q
        self._priority_q = priority_q
        self._event_q = event_q
        self._sleep = sleep
        self._fallback_poll_sec = fallback_poll_sec
        # None 은 무한 재시도다 — 상시 가동 프로세스에서 옳은 기본값이다.
        # 테스트는 유한한 값을 넘겨 종료 조건을 얻는다.
        self._max_fallback_rounds = max_fallback_rounds
        self._guards = GuardGate(repo, settings)
        self._executor = Executor(repo=repo, broker=broker, clock=clock,
                                  emit=self._emit)
        self._emergency = EmergencyHandler(repo=repo, broker=broker,
                                           clock=clock, emit=self._emit)
        self._reconciler = Reconciler(repo=repo, broker=broker, clock=clock,
                                      emit=self._emit)
        self._last_reconcile: object = None
        self.stopped = False

    def _emit(self, event: Event) -> None:
        self._event_q.put(event)

    # ── 명령 소비 ───────────────────────────────────────────────────────
    async def drain_commands(self) -> None:
        """`priority_q` 를 먼저 완전히 비우고, 그 다음 `command_q` 를 본다."""
        for q in (self._priority_q, self._command_q):
            while True:
                try:
                    command = q.get_nowait()
                except queue.Empty:
                    break
                await self._handle(command)

    async def _handle(self, command: cmd.Command) -> None:
        if isinstance(command, cmd.EmergencyLiquidate):
            if command.scope == "ALL":
                await self._emergency.liquidate_all(reason=command.reason)
            else:
                await self._emergency.liquidate_single(command.config_id,
                                                       reason=command.reason)
        elif isinstance(command, cmd.ForceClose):
            await self._emergency.force_close(command.config_id,
                                              reason=command.reason)
        elif isinstance(command, cmd.StartCycle):
            self._start_cycle(command.config_id)
        elif isinstance(command, cmd.PauseCycle):
            self._transition(command.config_id, cycle_mod.pause,
                             allowed_from=(CycleStatus.RUNNING,))
        elif isinstance(command, cmd.ResumeCycle):
            self._transition(command.config_id, cycle_mod.resume,
                             allowed_from=(CycleStatus.PAUSED,))
        elif isinstance(command, cmd.StopCycle):
            # D5 — 정지는 자동 트리거를 멈추는 것이고 사이클 종료가 아니다.
            self._transition(command.config_id, cycle_mod.pause,
                             allowed_from=(CycleStatus.RUNNING,))
        elif isinstance(command, cmd.ResetReconcileBaseline):
            self._reconciler.reset_baseline(command.stock_code)
        elif isinstance(command, cmd.Shutdown):
            self.stopped = True

    def _start_cycle(self, config_id: int) -> None:
        """앵커는 첫 틱에서 확정된다 — GUI 가 가격을 정하지 않는다.

        `create_cycle` 이 이미 STARTING 사이클을 삽입하고 반환하므로
        `cycle.start()` 를 다시 부르지 않는다 (그것은 도메인 단독 경로인
        `IDLE → STARTING` 의 것이며, 여기서 부르면 STARTING → STARTING 으로
        `IllegalCycleTransition` 이 난다).
        """
        at = self._clock.now()
        self._repo.create_cycle(config_id, at)
        self._repo.set_config_status(config_id, "ACTIVE", at=at)

    def _transition(
        self, config_id: int, fn,
        *, allowed_from: tuple[CycleStatus, ...],
    ) -> None:
        """사이클 상태만 바꾼다. **멱등해야 한다.**

        `split_config.status` 는 `IDLE|ACTIVE` 두 값뿐이며(설계서 12.1절·스키마
        CHECK) "이 설정이 사이클을 돌리고 있는가" 만 말한다. 일시정지는
        사이클의 상태다.

        `allowed_from` 을 명시하는 이유: 사용자가 [정지]를 두 번 누르면 두
        번째 명령이 `PAUSED → PAUSED` 를 시도하고 도메인 전이표가
        `IllegalCycleTransition` 을 던진다. 그 예외가 명령 소비 태스크를 죽이면
        **뒤에 쌓인 명령이 전부 사라진다 — 그중에 긴급청산이 있을 수 있다.**
        이미 목표 상태인 사이클은 조용히 건너뛴다.
        """
        for cyc in self._repo.load_active_cycles():
            if cyc.config_id == config_id and cyc.status in allowed_from:
                self._repo.save_cycle(fn(cyc))

    def _isolate(self, cyc: Cycle) -> str | None:
        """데이터 문제가 있는 사이클을 격리한다 — 사이클을 PAUSED 로.

        `RUNNING` 일 때만 전이한다: `STARTING` 은 이미 트리거를 받지 않고
        (`accepts_triggers` False), `LIQUIDATING` 을 되돌리면 진행 중인
        긴급청산의 상태를 프로그램이 뒤집는 것이 된다. 반환값은 **실제로 한
        것**이며 그대로 이벤트의 `action_taken` 이 된다.
        """
        if cyc.status is not CycleStatus.RUNNING:
            return None
        self._repo.save_cycle(cycle_mod.pause(cyc))
        return "PAUSED"

    # ── 틱 처리 ─────────────────────────────────────────────────────────
    async def on_tick(self, tick: Tick) -> None:
        self._emit(TickUpdate(stock_code=tick.code, price=tick.price,
                              source=tick.source, at=tick.at))
        for cyc in self._repo.load_active_cycles():
            config = self._repo.load_config(cyc.config_id)
            if config.stock_code != tick.code:
                continue
            try:
                await self._advance(cyc, config, tick)
            except (CorruptRowError, DomainInvariantError) as exc:
                # Plan 1 핸드오버 5 / 2A 핸드오버 7 — 한 사이클의 데이터
                # 문제가 틱 루프를 죽이면 다른 종목의 매도도 함께 멈춘다.
                self._emit(CycleLoadFailed(
                    config_id=cyc.config_id, cycle_id=cyc.cycle_id,
                    detail=str(exc), action_taken=self._isolate(cyc),
                    at=self._clock.now(),
                ))

    async def _advance(self, cyc: Cycle, config: SplitConfig,
                       tick: Tick) -> None:
        if cyc.status is CycleStatus.STARTING:
            self._confirm_anchor(cyc, config, tick)
            return
        # `accepts_triggers` 는 프로퍼티다 — 괄호를 붙이면 bool 을 호출한다.
        if not cyc.accepts_triggers:
            return

        stages = self._repo.load_stages(cyc.cycle_id)
        params = TriggerParams(target_pct=config.target_pct,
                               allow_rebuy=config.allow_rebuy,
                               rebuy_cooldown_sec=config.rebuy_cooldown_sec)
        decisions = decide(
            tick=tick, cycle=cyc, states=stages, params=params,
            now=self._clock.now(),
            market_open=self._clock.is_market_open(self._clock.now()),
            stock_code=config.stock_code,
        )
        for decision in decisions:
            # 주문 빈도 제한의 '지금' 은 틱의 시각이다, 시계가 아니다.
            now = tick.at
            if isinstance(decision, BuyStage):
                verdict = self._guards.check_buy(
                    decision, stock_code=config.stock_code,
                    stock_limit=config.total_limit, now=now)
                side = "BUY"
            else:
                verdict = self._guards.check_sell(decision, now=now)
                side = "SELL"
            if not verdict.allowed:
                self._emit(GuardBlocked(
                    config_id=config.config_id, stage_no=decision.stage_no,
                    side=side, reason=verdict.reason, at=now,
                ))
                continue
            # 한 틱이 여러 매도를 낼 수 있으므로 결정 사이에 증가시킨다
            self._guards.record_order(now)
            stage = next(s for s in self._repo.load_stages(cyc.cycle_id)
                         if s.stage_no == decision.stage_no)
            await self._executor.send(cycle=cyc, config=config, stage=stage,
                                      decision=decision, tick=tick)

    def _confirm_anchor(self, cyc: Cycle, config: SplitConfig,
                        tick: Tick) -> None:
        """첫 틱의 가격으로 앵커와 사다리를 확정하고 **단계 행을 만든다**.

        사다리는 계산될 뿐 저장되지 않는다 — `stage_state` 행 7개를 여기서
        만들어야 `load_stages` 가 완전한 집합(H3)을 얻는다. 이것이 없으면
        RUNNING 사이클이 로드 불가가 되어 첫 틱부터 격리된다.

        **단계를 먼저 쓰고 사이클을 나중에 쓴다.** 그 사이에 프로세스가 죽었을
        때 남는 상태가 다르다:

        - 단계 먼저: STARTING 사이클 + 단계 행 → 다음 틱이 다시 확정하고
          `save_stage` 가 WAITING → WAITING 으로 덮어쓴다(자기 치유).
        - 사이클 먼저: RUNNING 사이클 + 단계 없음 → `load_stages` 가 거부하고
          어느 경로로도 고칠 수 없다.

        설계서 9절의 "잘못 기록된 쪽이 잘못 잊힌 쪽보다 항상 낫다" 와 같은
        논리다 — 복구 가능한 쪽으로 어긋나게 한다.
        """
        ladder = config.to_ladder(anchor_price=tick.price)
        for n in range(1, ladder.max_stages + 1):
            self._repo.save_stage(cyc.cycle_id, StageState(
                stage_no=n, status=StageStatus.WAITING,
                trigger_price=ladder.trigger_price(n),
                planned_qty=ladder.planned_qty(n),
            ))
        self._repo.save_cycle(cycle_mod.confirm_anchor(
            cyc, anchor_price=tick.price, ladder=ladder, at=tick.at))

    # ── 미체결 감시 ─────────────────────────────────────────────────────
    async def poll_pending(self) -> None:
        """DB 를 진실로 삼는다 — 메모리 캐시를 두면 재시작 복구와 두 개의
        진실이 생긴다."""
        for row in self._repo.load_pending_orders():
            if row.stage_no is None or row.broker_order_id is None:
                continue
            config_id: int | None = None
            cyc: Cycle | None = None
            try:
                cyc = self._repo.load_cycle(row.cycle_id)
                config_id = cyc.config_id
                config = self._repo.load_config(config_id)
                stage = next(s for s in self._repo.load_stages(row.cycle_id)
                             if s.stage_no == row.stage_no)
                await self._executor.poll_fill(
                    cycle=cyc, config=config, stage=stage,
                    client_ref=row.client_ref,
                    broker_order_id=row.broker_order_id, sent_at=row.sent_at,
                    timeout_sec=self._settings.pending_timeout_sec,
                )
                self._close_if_complete(cyc, config)
            except (CorruptRowError, DomainInvariantError) as exc:
                # `is_cycle_complete([])` 도 여기로 온다 (Plan 1 핸드오버 5).
                # 한 사이클의 데이터 문제로 미체결 감시 전체가 멈추면 다른
                # 종목의 체결 반영도 함께 멈춘다.
                self._emit(CycleLoadFailed(
                    config_id=config_id, cycle_id=row.cycle_id,
                    detail=str(exc),
                    action_taken=None if cyc is None else self._isolate(cyc),
                    at=self._clock.now(),
                ))

    def _close_if_complete(self, cyc: Cycle, config: SplitConfig) -> None:
        """D5 — 사이클 종료는 보유 0 도달로만 일어난다.

        두 번째 조건이 필요한 이유: 갓 시작한 사이클은 전 단계가 `WAITING`
        이므로 `is_cycle_complete` 가 `True` 다. 그것으로 닫으면 아무것도 사지
        않은 사이클이 즉시 종료된다. `rebuy_count` 나 `last_sold_at` 이 하나라도
        있으면 그 사이클은 최소 한 번 매도를 완료했다는 뜻이다.
        """
        stages = self._repo.load_stages(cyc.cycle_id)
        if not cycle_mod.is_cycle_complete(stages):
            return
        if not any(s.rebuy_count or s.last_sold_at for s in stages):
            return
        at = self._clock.now()
        closed = cycle_mod.close(cyc, reason=CloseReason.NORMAL, at=at,
                                 states=stages)
        self._repo.save_cycle(closed)
        realized = self._repo.realized_pnl_for_cycle(cyc.cycle_id)
        self._repo.set_realized_pnl(cyc.cycle_id, realized)
        self._repo.set_config_status(config.config_id, "IDLE", at=at)
        self._emit(CycleClosed(config_id=config.config_id,
                               cycle_id=cyc.cycle_id,
                               reason=CloseReason.NORMAL,
                               realized_pnl=realized, at=at))

    async def reconcile(self) -> None:
        await self._reconciler.run_once()

    # ── 조립 ────────────────────────────────────────────────────────────
    async def run(self) -> None:
        """시세 스트림을 소비하며 매 틱마다 명령·판정·감시를 돈다.

        태스크를 실제로 5개 띄우지 않고 한 루프에서 순서대로 부르는 이유:
        단일 이벤트 루프에서 협력적으로 도는 것과 관측 가능한 동작이 같고,
        틱 단위로 결정론적이어서 G2 시나리오를 재현할 수 있다. 실전에서
        틱 사이의 유휴 시간이 길어지면 대사와 미체결 감시가 늦어지므로, 그때는
        `asyncio.create_task` 로 분리하는 것이 다음 단계다.
        """
        await self.drain_commands()
        rounds = 0
        while not self.stopped:
            codes = self._subscribed_codes()
            if not codes:
                return
            try:
                async for tick in self._broker.subscribe_quotes(codes):
                    await self._cycle_once(tick)
                    if self.stopped:
                        return
                return                      # 스트림 정상 종료
            except BrokerDisconnected:
                await self._fallback(codes)
                rounds += 1
                if (self._max_fallback_rounds is not None
                        and rounds >= self._max_fallback_rounds):
                    # 재구독이 즉시 다시 끊기면 무한 루프가 된다. 실전에서는
                    # 무한 재시도가 맞지만(상시 가동), 테스트는 종료 조건이
                    # 있어야 이 경로를 돌릴 수 있다.
                    self._emit(EngineStopped(
                        detail=f"시세 재연결 {rounds}회 실패 — 엔진을 멈춥니다",
                        at=self._clock.now(),
                    ))
                    self.stopped = True
                    return

    async def _cycle_once(self, tick: Tick) -> None:
        await self.drain_commands()
        await self.on_tick(tick)
        await self.poll_pending()
        if self._due_for_reconcile(tick):
            await self.reconcile()

    async def _fallback(self, codes: list[str]) -> None:
        """설계서 8.4절 — REST 폴백. **트리거 판정은 계속 수행한다.**

        폴백 중에 판정을 멈추면 급락 구간의 매수 기회를 통째로 놓치고, 더
        나쁘게는 목표가에 도달한 매도를 놓친다.
        """
        at = self._clock.now()
        self._emit(QuoteFallback(stock_codes=tuple(codes), active=True, at=at))
        for _ in range(3):
            if self.stopped:
                return
            for code in codes:
                price = await self._broker.get_price(code)
                await self._cycle_once(Tick(code=code, price=price,
                                            at=self._clock.now(),
                                            source=TickSource.REST_POLL))
            await self._sleep(self._fallback_poll_sec)
        self._emit(QuoteFallback(stock_codes=tuple(codes), active=False,
                                 at=self._clock.now()))

    def _subscribed_codes(self) -> list[str]:
        codes: list[str] = []
        for cyc in self._repo.load_active_cycles():
            code = self._repo.load_config(cyc.config_id).stock_code
            if code not in codes:
                codes.append(code)
        return codes

    def _due_for_reconcile(self, tick: Tick) -> bool:
        if self._last_reconcile is None:
            self._last_reconcile = tick.at
            return False
        if tick.at - self._last_reconcile >= timedelta(
            seconds=self._settings.reconcile_interval_sec
        ):
            self._last_reconcile = tick.at
            return True
        return False
