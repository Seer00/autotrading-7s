"""화면 뷰모델 — 설계서 14절.

**이 모듈은 `tkinter` 를 import 하지 않는다.** EC2 에 `tkinter` 가 아예 없으므로
(모듈 자체가 없다), 여기 들어온 로직은 자동 검증이 닿는 곳에 남고 위젯으로
넘어간 로직은 영원히 사각지대가 된다 (설계서 18.1 리스크 7).

**숫자를 담고 서식은 하지 않는다.** `pnl_pct` 는 `Decimal` 이고 `"-1.25%"` 가
아니다. 숫자를 단정하는 테스트는 계산을 검증하고 문자열을 단정하는 테스트는
서식을 검증한다 — 섞으면 소수점 자리를 바꿀 때 계산 테스트가 함께 깨지고 어느
쪽이 틀렸는지 알 수 없다. 서식은 `ui/text_render.py` 와 위젯의 몫이다.

**계산은 `domain/` 의 순수 함수를 부른다** (설계서 14.4절). 평가손익률조차 여기서
직접 계산하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from autotrading7s.app.commands import (
    FORCE_CLOSE_CONFIRMATION,
    LIQUIDATE_ALL_CONFIRMATION,
)
from autotrading7s.app.events import ReconcileMismatch
from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot
from autotrading7s.domain import pnl
from autotrading7s.domain.ladder import Ladder, target_price
from autotrading7s.domain.types import CycleStatus, StageStatus

BROKER_AVG_NOTICE = (
    "증권사 앱의 평균단가는 종목 전체 1개 값이고, 본 프로그램의 단계별 "
    "체결가는 내부 가상 넘버링 기준입니다."
)
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class HoldingRowView:
    config_id: int
    stock_code: str
    stock_name: str | None
    label: str | None
    held_qty: int
    avg_price: int | None
    current_price: int | None
    pnl: int | None
    pnl_pct: Decimal | None
    holding_stages: int
    max_stages: int
    status_label: str


@dataclass(frozen=True, slots=True)
class TotalsView:
    invested: int
    valuation: int
    pnl: int
    pnl_pct: Decimal | None
    missing_prices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HoldingsView:
    rows: tuple[HoldingRowView, ...]
    totals: TotalsView
    broker_avg_notice: str = BROKER_AVG_NOTICE


def status_label(config: ConfigSnapshot, *, mismatched: bool) -> str:
    """설계서 14.1절의 여섯 표기.

    `⚠불일치` 가 나머지를 덮는다 — 사용자가 가장 먼저 알아야 하는 것이고, 그
    상태에서 사이클은 이미 `PAUSED` 이므로 "일시정지" 는 같은 사실의 덜 중요한
    절반이다.
    """
    if mismatched:
        return "⚠불일치"
    if config.cycle_status is None or config.config_status == "IDLE":
        return "IDLE"
    if config.cycle_status is CycleStatus.LIQUIDATING:
        return "청산중"
    if config.cycle_status is CycleStatus.PAUSED:
        return "일시정지"
    # `소진` 은 전 단계 보유다 — 6/7 은 아직 감시 중이다.
    if (config.stages
            and pnl.holding_stage_count(config.stages) == config.max_stages):
        return "소진"
    # STARTING 은 한 틱만 지속되며 사용자가 보기엔 "시작을 눌렀고 감시 중" 이다.
    return "감시"


def build_holdings(
    snapshot: Snapshot, *, prices: Mapping[str, int],
    mismatched_codes: Sequence[str],
) -> HoldingsView:
    """설계서 14.1절 보유현황 표.

    가격이 없는 종목은 **합계에서 제외하고 그 사실을 함께 반환한다.**
    투입금액으로 대체하면 손익 0 으로 보여 사용자가 그 종목이 반영됐다고
    믿는다 — 기동 직후와 장 시작 전에 정확히 그 상태가 된다.

    합계의 백분율은 `domain/pnl.py` 를 쓰지 않는다 — 그 함수들은
    `Sequence[StageState]` 를 받고 여기서는 여러 종목을 합친 값이므로 대응하는
    함수가 없다. 같은 반올림 규칙(소수 2자리, `ROUND_HALF_UP`)을 쓴다.
    """
    mismatched = set(mismatched_codes)
    rows: list[HoldingRowView] = []
    invested = valuation = 0
    missing: list[str] = []

    for config in snapshot.configs:
        stages = config.stages
        held = pnl.held_qty(stages)
        price = prices.get(config.stock_code)
        rows.append(HoldingRowView(
            config_id=config.config_id,
            stock_code=config.stock_code,
            stock_name=config.stock_name,
            label=config.label,
            held_qty=held,
            avg_price=pnl.avg_price(stages),
            current_price=price,
            pnl=(None if price is None or held == 0
                 else pnl.unrealized_pnl(stages, price)),
            pnl_pct=(None if price is None or held == 0
                     else pnl.unrealized_pnl_pct(stages, price)),
            holding_stages=pnl.holding_stage_count(stages),
            max_stages=config.max_stages,
            status_label=status_label(
                config, mismatched=config.stock_code in mismatched),
        ))
        if held == 0:
            continue                      # 보유가 없으면 합계에 영향이 없다
        if price is None:
            missing.append(config.stock_code)
            continue
        invested += pnl.invested_amount(stages)
        valuation += held * price

    total_pnl = valuation - invested
    total_pct = (None if invested == 0
                 else (Decimal(total_pnl) / invested * 100).quantize(
                     _CENT, rounding=ROUND_HALF_UP))
    return HoldingsView(
        rows=tuple(rows),
        totals=TotalsView(invested=invested, valuation=valuation,
                          pnl=total_pnl, pnl_pct=total_pct,
                          missing_prices=tuple(missing)),
    )


# ── 단계별 상세 (설계서 14.1절) ─────────────────────────────────────────
STAGE_STATUS_LABELS: dict[StageStatus, str] = {
    StageStatus.WAITING: "대기",
    StageStatus.BUY_PENDING: "매수대기",
    StageStatus.HOLDING: "보유",
    StageStatus.SELL_PENDING: "매도대기",
    StageStatus.SOLD: "매도완료",
}

_HELD = (StageStatus.HOLDING, StageStatus.SELL_PENDING)
_TENTH = Decimal("0.1")


@dataclass(frozen=True, slots=True)
class StageRowView:
    stage_no: int
    trigger_price: int
    status_label: str
    fill_price: int | None
    fill_qty: int | None
    target_price: int | None
    gap_pct: Decimal | None
    gap_won: int | None
    gap_kind: str | None            # "TARGET" | "TRIGGER" | None
    rebuy_count: int


@dataclass(frozen=True, slots=True)
class StageDetailView:
    config_id: int
    stock_name: str | None
    label: str | None
    cycle_seq: int | None
    anchor_price: int | None
    started_at: datetime | None
    rows: tuple[StageRowView, ...]


def build_stage_detail(
    config: ConfigSnapshot, *, current_price: int | None,
) -> StageDetailView:
    """설계서 14.1절 단계별 상세.

    "목표까지 / 매수까지" 열이 설계서 1.1절 5항의 요구다 — 보유 단계는
    목표까지, 대기 단계는 매수 발동까지를 **같은 열에** 담아 사용자가 한 열만
    훑어도 다음에 무슨 일이 일어날지 알 수 있게 한다.

    두 의미가 하나의 계산이다: `(기준가 − 현재가) / 현재가`. **분모가 현재가인
    것이 핵심이다** — "지금 가격에서 몇 % 움직이면" 이 사용자의 질문이고,
    설계서 목업의 숫자가 그것을 확인한다.

    `SOLD` 와 `BUY_PENDING` 은 기준가가 없다. `SOLD` 는 쿨다운이 끝나기 전이라
    "하락 시 매수" 가 사실이 아니고, `BUY_PENDING` 은 이미 주문이 나갔으므로
    "몇 % 남았는가" 가 답이 아니다.
    """
    rows: list[StageRowView] = []
    for stage in config.stages:
        held = stage.status in _HELD
        target = (target_price(stage.fill_price, config.target_pct)
                  if held and stage.fill_price is not None else None)
        reference: int | None = None
        kind: str | None = None
        if current_price is not None:
            if target is not None:
                reference, kind = target, "TARGET"
            elif stage.status is StageStatus.WAITING:
                reference, kind = stage.trigger_price, "TRIGGER"
        gap_won = None if reference is None else reference - current_price
        gap_pct = (None if gap_won is None or current_price is None
                   else (Decimal(gap_won) / current_price * 100).quantize(
                       _TENTH, rounding=ROUND_HALF_UP))
        rows.append(StageRowView(
            stage_no=stage.stage_no,
            trigger_price=stage.trigger_price,
            status_label=STAGE_STATUS_LABELS[stage.status],
            fill_price=stage.fill_price,
            fill_qty=stage.fill_qty,
            target_price=target,
            gap_pct=gap_pct,
            gap_won=gap_won,
            gap_kind=kind,
            rebuy_count=stage.rebuy_count,
        ))
    return StageDetailView(
        config_id=config.config_id, stock_name=config.stock_name,
        label=config.label, cycle_seq=config.cycle_seq,
        anchor_price=config.anchor_price, started_at=config.cycle_started_at,
        rows=tuple(rows),
    )


# ── 사다리 미리보기 (설계서 14.2절) ─────────────────────────────────────
LADDER_PREVIEW_NOTICE = (
    "실제 앵커는 1단계 체결가로 확정되며, 각 단계 목표가는 발동가가 아니라 "
    "실제 체결가 기준으로 계산됩니다."
)

_REQUIRED_TEXT = ("stock_code",)
_OPTIONAL_TEXT = ("stock_name", "label")
_INT_FIELDS = ("max_stages", "amount_per_stage", "rebuy_cooldown_sec",
               "total_limit")
_PCT_FIELDS = ("drop_pct", "target_pct")
_TRUTHY = ("1", "true", "True", "yes", "on")


class FormError(Exception):
    """입력란 하나의 형식 오류. 메시지에 필드 이름이 들어간다.

    위젯이 그 이름으로 어느 입력란 옆에 표시할지 결정한다.
    """


@dataclass(frozen=True, slots=True)
class LadderPreviewRow:
    stage_no: int
    trigger_price: int
    qty: int
    investment: int
    target_price: int
    cumulative: int


@dataclass(frozen=True, slots=True)
class LadderPreview:
    rows: tuple[LadderPreviewRow, ...]
    total_investment: int
    stock_limit: int
    headroom: int
    over_limit: bool
    last_drop_pct: Decimal
    full_avg_price: int
    full_avg_drop_pct: Decimal
    notice: str = LADDER_PREVIEW_NOTICE


def _pct_vs(value: int, anchor: int) -> Decimal:
    return (Decimal(value - anchor) / anchor * 100).quantize(
        _TENTH, rounding=ROUND_HALF_UP)


def build_ladder_preview(
    *, anchor_price: int, max_stages: int, drop_pct: Decimal,
    target_pct: Decimal, amount_per_stage: int, stock_limit: int,
) -> LadderPreview:
    """설계서 14.2절 사다리 미리보기.

    `Ladder` 를 그대로 쓴다 — 미리보기가 계산을 다시 구현하면 화면의 숫자와
    실제 사다리가 어긋나고, 그 어긋남은 사용자가 저장한 뒤에야 드러난다.
    `Ladder` 의 불변식(1단계에서 1주 이상)도 그대로 통과시킨다: 미리보기가
    도메인보다 관대하면 화면에서 괜찮아 보이는 설정이 저장에서 거부된다.

    **미리보기는 발동가를 체결가로 가정한다.** 설계서 목업의 ⓘ 문구가 그
    사실을 명시하며, `notice` 가 그 문구를 담아 화면이 반드시 보여주게 한다 —
    없으면 사용자가 미리보기의 목표가를 확정된 값으로 읽는다.

    전 단계 보유 시 평단과 앵커 대비 하락률이 중요한 이유: 손절매가 없는
    전략에서 그 숫자가 사용자가 최악의 경우를 가늠하는 유일한 수단이다.
    """
    ladder = Ladder(anchor_price=anchor_price, drop_pct=drop_pct,
                    target_pct=target_pct, max_stages=max_stages,
                    amount_per_stage=amount_per_stage)
    rows: list[LadderPreviewRow] = []
    cumulative = 0
    total_qty = 0
    for n in range(1, max_stages + 1):
        investment = ladder.planned_investment(n)
        cumulative += investment
        total_qty += ladder.planned_qty(n)
        rows.append(LadderPreviewRow(
            stage_no=n, trigger_price=ladder.trigger_price(n),
            qty=ladder.planned_qty(n), investment=investment,
            target_price=target_price(ladder.trigger_price(n), target_pct),
            cumulative=cumulative,
        ))
    full_avg = int((Decimal(cumulative) / total_qty).to_integral_value(
        rounding=ROUND_HALF_UP))
    return LadderPreview(
        rows=tuple(rows), total_investment=cumulative, stock_limit=stock_limit,
        headroom=stock_limit - cumulative, over_limit=cumulative > stock_limit,
        last_drop_pct=_pct_vs(ladder.trigger_price(max_stages), anchor_price),
        full_avg_price=full_avg,
        full_avg_drop_pct=_pct_vs(full_avg, anchor_price),
    )


def parse_config_form(fields: Mapping[str, str]) -> dict[str, object]:
    """설정 등록 폼의 문자열을 `SaveConfig` 가 받는 타입으로 바꾼다.

    반환한 dict 를 그대로 `SaveConfig(config_id=..., **parsed)` 에 넘길 수
    있어야 한다 — 이름이 하나라도 어긋나면 위젯이 그 차이를 손으로 메우게
    되고, 그 코드는 EC2 에서 검증되지 않는 곳에 들어간다.

    `NaN`·`Infinity` 를 명시적으로 거부하는 이유: `Decimal("NaN")` 은
    만들어지고 그 뒤 도메인이 `decimal.InvalidOperation` 을 던지는데, 그것은
    `ArithmeticError` 이지 `ValueError` 가 아니므로 호출자의 넓은
    `except ValueError` 로도 잡히지 않는다 (Plan 1 의 기록).
    """
    out: dict[str, object] = {}
    for name in _REQUIRED_TEXT:
        text = (fields.get(name) or "").strip()
        if not text:
            raise FormError(f"{name}: 값을 입력하세요")
        out[name] = text
    for name in _OPTIONAL_TEXT:
        text = (fields.get(name) or "").strip()
        out[name] = text or None
    for name in _INT_FIELDS:
        text = (fields.get(name) or "").strip().replace(",", "")
        try:
            out[name] = int(text)
        except ValueError:
            raise FormError(f"{name}: 정수를 입력하세요 ({text!r})") from None
    for name in _PCT_FIELDS:
        text = (fields.get(name) or "").strip().replace("%", "")
        try:
            percent = Decimal(text)
        except InvalidOperation:
            raise FormError(f"{name}: 숫자를 입력하세요 ({text!r})") from None
        if not percent.is_finite():
            raise FormError(f"{name}: 유한한 숫자를 입력하세요 ({text!r})")
        out[name] = percent / 100
    out["allow_rebuy"] = (fields.get("allow_rebuy") or "").strip() in _TRUTHY
    return out


# ── 다이얼로그·상태바·배너 (설계서 14.1·14.3·11.4절) ────────────────────
_ENV_LABELS = {"mock": "▣ 모의투자", "real": "▣ 실전투자"}


@dataclass(frozen=True, slots=True)
class EmergencyDialogView:
    config_id: int
    stock_code: str
    stock_name: str | None
    held_qty: int
    holding_stages: int
    current_price: int | None
    estimated_amount: int | None
    avg_price: int | None
    estimated_pnl: int | None
    estimated_pnl_pct: Decimal | None
    pending_orders: int
    required_text: str | None


@dataclass(frozen=True, slots=True)
class ForceCloseDialogView:
    config_id: int
    stock_code: str
    stock_name: str | None
    remaining_qty: int
    holding_stages: int
    attempts: int
    last_attempt_at: datetime | None
    last_failure_detail: str | None
    required_text: str = FORCE_CLOSE_CONFIRMATION


@dataclass(frozen=True, slots=True)
class StatusBarView:
    quote_source_label: str
    last_reconcile_label: str
    total_used: int
    total_limit: int
    used_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class BannerView:
    env_label: str
    is_real: bool
    connection_label: str
    engine_error: str | None


def build_emergency_view(
    config: ConfigSnapshot, *, current_price: int | None, scope: str,
) -> EmergencyDialogView:
    """설계서 14.3절 재확인 다이얼로그.

    팔 것이 없는 종목에 이 다이얼로그를 띄우면 사용자를 오도하므로 거부한다.
    현재가를 모르면 예상금액을 추측하지 않는다 — 사용자가 그 숫자를 근거로
    실행 여부를 판단한다.

    `required_text` 를 여기서 정하는 이유: 다이얼로그가 직접 쓰면 상수와
    어긋날 수 있고, 어긋나면 사용자가 정확히 입력했는데 버튼이 활성화되지
    않는다.
    """
    held = pnl.held_qty(config.stages)
    if held == 0:
        raise ValueError(
            f"{config.stock_code}: 보유 수량이 0 이므로 긴급청산할 것이 없다"
        )
    return EmergencyDialogView(
        config_id=config.config_id, stock_code=config.stock_code,
        stock_name=config.stock_name, held_qty=held,
        holding_stages=pnl.holding_stage_count(config.stages),
        current_price=current_price,
        estimated_amount=None if current_price is None else held * current_price,
        avg_price=pnl.avg_price(config.stages),
        estimated_pnl=(None if current_price is None
                       else pnl.unrealized_pnl(config.stages, current_price)),
        estimated_pnl_pct=(None if current_price is None else
                           pnl.unrealized_pnl_pct(config.stages,
                                                  current_price)),
        pending_orders=config.pending_orders,
        required_text=(LIQUIDATE_ALL_CONFIRMATION if scope == "ALL" else None),
    )


def build_force_close_view(
    config: ConfigSnapshot, *, attempts: int,
    last_attempt_at: datetime | None, last_failure_detail: str | None,
) -> ForceCloseDialogView:
    """설계서 11.4절 강제 종료 확인.

    잔량 0 의 강제 종료는 의미가 없다(절차 ③) — 엔진도 그것을 정상 종료로
    처리하므로 다이얼로그가 애초에 뜨면 안 된다.
    """
    remaining = pnl.held_qty(config.stages)
    if remaining == 0:
        raise ValueError(
            f"{config.stock_code}: 잔량이 0 이므로 강제 종료가 아니라 정상 "
            f"종료로 처리된다 (설계서 11.4절 절차 ③)"
        )
    return ForceCloseDialogView(
        config_id=config.config_id, stock_code=config.stock_code,
        stock_name=config.stock_name, remaining_qty=remaining,
        holding_stages=pnl.holding_stage_count(config.stages),
        attempts=attempts, last_attempt_at=last_attempt_at,
        last_failure_detail=last_failure_detail,
    )


def build_status_bar(
    *, fallback_active: bool, last_reconcile: ReconcileMismatch | None,
    total_used: int, total_limit: int,
) -> StatusBarView:
    """설계서 14.1절 하단 — `시세 WebSocket │ 대사 09:40 일치 │ 총한도 …`."""
    if last_reconcile is None:
        reconcile_label = "대사 일치"
    else:
        reconcile_label = (
            f"대사 {last_reconcile.at:%H:%M} {last_reconcile.stock_code} "
            f"{last_reconcile.verdict}"
        )
    return StatusBarView(
        quote_source_label=("시세 REST 폴백" if fallback_active
                            else "시세 WebSocket"),
        last_reconcile_label=reconcile_label,
        total_used=total_used, total_limit=total_limit,
        # 한도가 0 이면 나누지 않는다 — 설정 전이나 잘못된 설정에서 그 상태가 된다.
        used_pct=(None if total_limit == 0
                  else (Decimal(total_used) / total_limit * 100).quantize(
                      _TENTH, rounding=ROUND_HALF_UP)),
    )


def build_banner(
    *, env: str, fallback_active: bool, engine_error: str | None,
) -> BannerView:
    """설계서 14.1절 상단.

    알 수 없는 환경을 거부하는 이유: 조용히 모의투자로 떨어지면 사용자가
    실전이라고 믿는 채로 돌린다. 색은 위젯이 `is_real` 로 정한다.
    """
    if env not in _ENV_LABELS:
        raise ValueError(f"env must be one of {sorted(_ENV_LABELS)}: {env!r}")
    return BannerView(
        env_label=_ENV_LABELS[env], is_real=(env == "real"),
        connection_label=("● REST 폴백" if fallback_active else "● WS 연결"),
        engine_error=engine_error,
    )
