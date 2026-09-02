"""프레젠터 — 이벤트 소비 상태기계.

**GUI 로직의 전부가 여기 있다.** 위젯은 프레젠터가 만든 뷰를 그리고 사용자
입력을 명령으로 되돌리는 일만 한다 — `tkinter` 가 EC2 에 없으므로 그 경계가
검증 가능한 것과 그렇지 않은 것을 가른다. **이 모듈은 `tkinter` 를 import
하지 않는다.**

**대사 불일치 경고는 사용자나 사이클 종료가 지울 때까지 남는다.** 대사는
일치할 때 **이벤트를 내지 않는다**(설계서 10.2절: "일치 — 로그 없음") — 그래서
해소를 알 방법이 없다. 새 스냅샷이 온다고 지우면 다음 대사(5분)까지 사용자가
아무 문제도 없다고 믿는다.

**로그 줄은 이벤트 종류를 그대로 담는다.** `OrderUnknown` 과 `OrderRejected` 를
같은 색으로 그리면 안 된다는 것이 2B 핸드오버 4 이고, 위젯이 그 구분을 하려면
종류 이름이 필요하다 — 문구만 담으면 위젯이 문자열을 검사하게 되고 그것은
사각지대의 로직이다.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from autotrading7s.app.events import (
    CommandFailed,
    ConfigRejected,
    ConfigSaved,
    CycleClosed,
    CycleLoadFailed,
    EmergencyResult,
    EngineStopped,
    Event,
    GuardBlocked,
    OrderRejected,
    OrderUnknown,
    QuoteFallback,
    ReconcileMismatch,
    StageFilled,
    TickUpdate,
)
from autotrading7s.app.snapshot import Snapshot
from autotrading7s.domain import pnl
from autotrading7s.ui.view_model import (
    BannerView,
    EmergencyDialogView,
    ForceCloseDialogView,
    HoldingsView,
    StageDetailView,
    StatusBarView,
    build_banner,
    build_emergency_view,
    build_force_close_view,
    build_holdings,
    build_stage_detail,
    build_status_bar,
)

# 종류 → 심각도. 위젯은 이것으로 색을 정한다.
_SEVERITY: dict[str, str] = {
    "StageFilled": "INFO",
    "CycleClosed": "INFO",
    "GuardBlocked": "INFO",
    "ConfigSaved": "INFO",
    "OrderUnknown": "WARN",
    "OrderRejected": "WARN",
    "ConfigRejected": "WARN",
    "CommandFailed": "WARN",
    "QuoteFallback": "WARN",
    "ReconcileMismatch": "ERROR",
    "CycleLoadFailed": "ERROR",
    "EngineStopped": "ERROR",
}
# 청산 시도로 세지 않는 결말 — 성공한 청산을 횟수에 넣으면 강제 종료
# 다이얼로그의 근거가 흐려진다.
_SETTLED_RESULTS = ("SUCCESS", "FORCED_CLOSE")


@dataclass(frozen=True, slots=True)
class LogLine:
    kind: str
    severity: str
    text: str
    at: datetime


@dataclass(frozen=True, slots=True)
class ConfigFeedback:
    ok: bool
    config_id: int | None
    detail: str


class Presenter:
    def __init__(self, env: str, *, log_capacity: int = 500) -> None:
        self._env = env
        self._snapshot: Snapshot | None = None
        self._prices: dict[str, int] = {}
        self._mismatched: dict[str, ReconcileMismatch] = {}
        self._last_reconcile: ReconcileMismatch | None = None
        self._fallback_active = False
        self._engine_error: str | None = None
        self._log: deque[LogLine] = deque(maxlen=log_capacity)
        self._config_feedback: ConfigFeedback | None = None
        self._failed_attempts: dict[str, list[EmergencyResult]] = {}

    # ── 이벤트 소비 ─────────────────────────────────────────────────────
    def consume_all(self, events: Iterable[Event]) -> None:
        for event in events:
            self.consume(event)

    def consume(self, event: Event) -> None:
        if isinstance(event, Snapshot):
            self._snapshot = event
            return                    # 초당 여러 번 온다 — 로그에 넣지 않는다
        if isinstance(event, TickUpdate):
            self._prices[event.stock_code] = event.price
            return                    # 같은 이유
        if isinstance(event, ReconcileMismatch):
            self._mismatched[event.stock_code] = event
            self._last_reconcile = event
        elif isinstance(event, QuoteFallback):
            self._fallback_active = event.active
        elif isinstance(event, EngineStopped):
            self._engine_error = event.detail or "엔진이 멈췄습니다"
        elif isinstance(event, CycleClosed):
            # 사이클이 끝나면 그 불일치는 더 이상 이 사이클의 문제가 아니다.
            code = self._code_of(event.config_id)
            if code is not None:
                self._mismatched.pop(code, None)
                self._failed_attempts.pop(code, None)
        elif isinstance(event, ConfigSaved):
            self._config_feedback = ConfigFeedback(
                ok=True, config_id=event.config_id, detail="저장되었습니다")
        elif isinstance(event, ConfigRejected):
            self._config_feedback = ConfigFeedback(
                ok=False, config_id=event.config_id, detail=event.detail)
        elif isinstance(event, EmergencyResult):
            if event.stock_code is not None:
                if event.result in _SETTLED_RESULTS:
                    self._failed_attempts.pop(event.stock_code, None)
                else:
                    self._failed_attempts.setdefault(
                        event.stock_code, []).append(event)
        self._log.append(self._to_log_line(event))

    def note_engine_error(self, text: str) -> None:
        """`EngineThread.raise_if_failed()` 가 던진 것을 화면에 올린다."""
        self._engine_error = text

    def clear_mismatch(self, stock_code: str) -> None:
        """대사는 일치할 때 이벤트를 내지 않으므로 해소를 알 방법이 없다.

        위젯이 기준선 초기화·재개를 보낼 때 함께 부른다.
        """
        self._mismatched.pop(stock_code, None)

    def take_config_feedback(self) -> ConfigFeedback | None:
        """한 번 읽고 지운다 — 남아 있으면 다음에 열 때 옛 오류가 뜬다."""
        feedback, self._config_feedback = self._config_feedback, None
        return feedback

    # ── 뷰 ─────────────────────────────────────────────────────────────
    def holdings(self) -> HoldingsView:
        if self._snapshot is None:
            return build_holdings(_EMPTY_SNAPSHOT, prices={},
                                  mismatched_codes=())
        return build_holdings(self._snapshot, prices=self._prices,
                              mismatched_codes=tuple(self._mismatched))

    def stage_detail(self, config_id: int) -> StageDetailView | None:
        config = self._config(config_id)
        if config is None:
            return None
        return build_stage_detail(
            config, current_price=self._prices.get(config.stock_code))

    def status_bar(self) -> StatusBarView:
        used = 0
        limit = 0
        if self._snapshot is not None:
            used = sum(pnl.invested_amount(c.stages)
                       for c in self._snapshot.configs)
            limit = self._snapshot.total_limit
        return build_status_bar(fallback_active=self._fallback_active,
                                last_reconcile=self._last_reconcile,
                                total_used=used, total_limit=limit)

    def banner(self) -> BannerView:
        return build_banner(env=self._env,
                            fallback_active=self._fallback_active,
                            engine_error=self._engine_error)

    def emergency(self, config_id: int, *, scope: str
                  ) -> EmergencyDialogView | None:
        """뷰모델의 `ValueError` 를 `None` 으로 바꾼다 — 위젯이 예외를 처리하게
        하면 그 처리 코드가 EC2 에서 검증되지 않는 곳에 들어간다."""
        config = self._config(config_id)
        if config is None:
            return None
        try:
            return build_emergency_view(
                config, current_price=self._prices.get(config.stock_code),
                scope=scope)
        except ValueError:
            return None

    def force_close(self, config_id: int) -> ForceCloseDialogView | None:
        config = self._config(config_id)
        if config is None:
            return None
        attempts = self._failed_attempts.get(config.stock_code, [])
        try:
            return build_force_close_view(
                config, attempts=len(attempts),
                last_attempt_at=attempts[-1].at if attempts else None,
                last_failure_detail=attempts[-1].detail if attempts else None)
        except ValueError:
            return None

    def log_lines(self) -> tuple[LogLine, ...]:
        return tuple(self._log)

    # ── 내부 ────────────────────────────────────────────────────────────
    def _config(self, config_id: int):
        if self._snapshot is None:
            return None
        for config in self._snapshot.configs:
            if config.config_id == config_id:
                return config
        return None

    def _code_of(self, config_id: int) -> str | None:
        config = self._config(config_id)
        return None if config is None else config.stock_code

    def _to_log_line(self, event: Event) -> LogLine:
        kind = type(event).__name__
        return LogLine(
            kind=kind, severity=_SEVERITY.get(kind, "INFO"),
            text=_describe(event), at=getattr(event, "at"),
        )


def _describe(event: Event) -> str:
    """로그 한 줄의 문구.

    `GuardBlocked.reason` 은 **도메인이 만든 문장을 그대로** 쓴다 (2B 핸드오버
    7) — 다시 쓰면 한도 숫자의 서식이 두 곳에 생기고 도메인 테스트가 고정한
    문구와 화면의 문구가 어긋난다.
    """
    if isinstance(event, GuardBlocked):
        return event.reason
    if isinstance(event, StageFilled):
        return (f"{event.side} 체결 단계 {event.stage_no}: "
                f"{event.fill_qty}주 @ {event.fill_price:,}원")
    if isinstance(event, CycleClosed):
        return (f"사이클 {event.cycle_id} 종료({event.reason.value}) "
                f"실현손익 {event.realized_pnl:,}원")
    if isinstance(event, OrderUnknown):
        return (f"응답 유실 단계 {event.stage_no} — 조회로 확인 중 "
                f"(재발주하지 않는다)")
    if isinstance(event, OrderRejected):
        return (f"주문 거부 단계 {event.stage_no}: "
                f"[{event.api_code}] {event.api_message}")
    if isinstance(event, ReconcileMismatch):
        return (f"대사 불일치 {event.stock_code}: 내부 {event.internal_qty} / "
                f"실계좌 {event.broker_qty} ({event.verdict})")
    if isinstance(event, CycleLoadFailed):
        return f"사이클 {event.cycle_id} 로드 실패: {event.detail}"
    if isinstance(event, QuoteFallback):
        return ("시세 REST 폴백 진입" if event.active else "시세 WebSocket 복귀")
    if isinstance(event, EmergencyResult):
        return (f"긴급청산({event.scope}) {event.stock_code}: {event.result}"
                + (f" — {event.detail}" if event.detail else ""))
    if isinstance(event, ConfigSaved):
        return f"설정 {event.config_id} 저장"
    if isinstance(event, ConfigRejected):
        return f"설정 저장 거부: {event.detail}"
    if isinstance(event, CommandFailed):
        return f"명령 {event.command} 실패: {event.detail}"
    if isinstance(event, EngineStopped):
        return event.detail or "엔진이 멈췄습니다"
    return type(event).__name__


# 첫 스냅샷이 오기 전에도 화면을 그릴 수 있어야 한다 — 기동 직후가 그 상태다.
_EMPTY_SNAPSHOT = Snapshot(configs=(), total_limit=0,
                           at=datetime(1970, 1, 1, tzinfo=UTC))
