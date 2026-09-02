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
from decimal import ROUND_HALF_UP, Decimal

from autotrading7s.app.snapshot import ConfigSnapshot, Snapshot
from autotrading7s.domain import pnl
from autotrading7s.domain.ladder import target_price
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
