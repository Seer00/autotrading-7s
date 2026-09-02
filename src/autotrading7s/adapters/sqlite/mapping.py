"""행 ↔ 도메인 객체 변환.

Plan 1 이 Plan 2 로 넘긴 제약이 이 모듈에서 착륙한다 — H1(복원 실패를 지목),
H3(완전한 단계 집합), H4(trigger_price 대조). H2(tz-aware)는 codec 이 담당한다.

**감싸는 것과 감싸지 않는 것.** 도메인 객체를 복원하다 `DomainInvariantError` 가
나면 그것은 그 행이 손상된 것이므로 `CorruptRowError` 로 감싸 테이블과 rowid 를
붙인다. `ValueError`·`TypeError` 는 호출자 버그이므로 감싸지 않고 그대로 올린다 —
개발 중에 드러나야 한다. Task 1 이 두 범주를 나눈 목적이 이 구분이다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from autotrading7s.adapters.sqlite.codec import (
    bool_to_int,
    dt_to_text,
    int_to_bool,
    ratio_to_text,
    text_to_dt,
    text_to_ratio,
)
from autotrading7s.domain.cycle import Cycle
from autotrading7s.domain.errors import DomainInvariantError
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.types import CloseReason, CycleStatus
from autotrading7s.ports.repository import SplitConfig


class CorruptRowError(DomainInvariantError):
    """복원된 행이 도메인 불변식을 만족하지 않을 때. 어느 행인지 지목한다."""


def _corrupt(table: str, rowid: object, cause: Exception) -> CorruptRowError:
    return CorruptRowError(f"corrupt row in {table} (id={rowid}): {cause}")


def config_to_row(config: SplitConfig) -> dict[str, Any]:
    return {
        "stock_code": config.stock_code,
        "stock_name": config.stock_name,
        "label": config.label,
        "max_stages": config.max_stages,
        "drop_pct": ratio_to_text(config.drop_pct),
        "target_pct": ratio_to_text(config.target_pct),
        "amount_per_stage": config.amount_per_stage,
        "allow_rebuy": bool_to_int(config.allow_rebuy),
        "rebuy_cooldown_sec": config.rebuy_cooldown_sec,
        "total_limit": config.total_limit,
        "status": config.status,
        "created_at": dt_to_text(config.created_at),
        "updated_at": dt_to_text(config.updated_at),
    }


def row_to_config(row: Mapping[str, Any]) -> SplitConfig:
    rowid = row.get("id")
    try:
        config = SplitConfig(
            config_id=rowid,
            stock_code=row["stock_code"],
            stock_name=row["stock_name"],
            label=row["label"],
            max_stages=row["max_stages"],
            drop_pct=text_to_ratio(row["drop_pct"]),
            target_pct=text_to_ratio(row["target_pct"]),
            amount_per_stage=row["amount_per_stage"],
            allow_rebuy=int_to_bool(row["allow_rebuy"]),
            rebuy_cooldown_sec=row["rebuy_cooldown_sec"],
            total_limit=row["total_limit"],
            status=row["status"],
            created_at=text_to_dt(row["created_at"]),
            updated_at=text_to_dt(row["updated_at"]),
        )
    except DomainInvariantError as exc:
        raise _corrupt("split_config", rowid, exc) from exc
    # SplitConfig 자체에는 불변식이 없다(저장 형태다). 실행 가능성은 Ladder 가
    # 판단하므로, 복원 시점에 사다리를 만들어 검증한다 — 앵커는 임의값을 쓴다.
    # max_stages 범위·비율 범위·1주 미달을 여기서 잡는다.
    #
    # 이 검증에는 한계가 있다: `to_ladder` 는 실제 앵커가 아니라 임의의 앵커
    # (10,000원)로 사다리를 만든다. "1단계에서 1주를 살 수 있는가" 같은 검사는
    # 앵커에 따라 결과가 달라지므로, 이 임의 앵커에서만 통과하거나 실패하는
    # 설정이 있을 수 있다. max_stages 범위와 비율 범위는 앵커와 무관하게
    # 잡히며, 그것이 복원 시점에 잡고 싶은 손상이다. 앵커 의존적인 검증은
    # 사이클 시작 시 실제 앵커로 다시 이루어진다(YAGNI — 더 나은 검증을
    # 여기서 발명하지 않는다).
    try:
        config.to_ladder(anchor_price=10_000)
    except DomainInvariantError as exc:
        raise _corrupt("split_config", rowid, exc) from exc
    return config


def ladder_to_json(ladder: Ladder) -> str:
    """사다리 스냅샷. 설계서 12.2절 — 설정이 변해도 과거 사이클을 재현할 수 있다."""
    return json.dumps(
        {
            "anchor_price": ladder.anchor_price,
            "drop_pct": ratio_to_text(ladder.drop_pct),
            "target_pct": ratio_to_text(ladder.target_pct),
            "max_stages": ladder.max_stages,
            "amount_per_stage": ladder.amount_per_stage,
        },
        ensure_ascii=False,
    )


def json_to_ladder(text: str) -> Ladder:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CorruptRowError(f"corrupt ladder_json: {exc}") from exc
    try:
        return Ladder(
            anchor_price=payload["anchor_price"],
            drop_pct=text_to_ratio(payload["drop_pct"]),
            target_pct=text_to_ratio(payload["target_pct"]),
            max_stages=payload["max_stages"],
            amount_per_stage=payload["amount_per_stage"],
        )
    except KeyError as exc:
        raise CorruptRowError(f"corrupt ladder_json: missing key {exc}") from exc
    except DomainInvariantError as exc:
        raise CorruptRowError(f"corrupt ladder_json: {exc}") from exc


def cycle_to_row(cycle: Cycle) -> dict[str, Any]:
    return {
        "config_id": cycle.config_id,
        "seq": cycle.seq,
        "status": cycle.status.value,
        "anchor_price": cycle.anchor_price,
        "ladder_json": None if cycle.ladder is None else ladder_to_json(cycle.ladder),
        "close_reason": None if cycle.close_reason is None else cycle.close_reason.value,
        "started_at": None if cycle.started_at is None else dt_to_text(cycle.started_at),
        "closed_at": None if cycle.closed_at is None else dt_to_text(cycle.closed_at),
    }


def row_to_cycle(row: Mapping[str, Any]) -> Cycle:
    rowid = row.get("id")
    try:
        status = CycleStatus(row["status"])
        reason_text = row["close_reason"]
        close_reason = None if reason_text is None else CloseReason(reason_text)
        ladder_text = row["ladder_json"]
        ladder = None if ladder_text is None else json_to_ladder(ladder_text)
        started = row["started_at"]
        closed = row["closed_at"]
        return Cycle(
            cycle_id=rowid,
            config_id=row["config_id"],
            seq=row["seq"],
            status=status,
            anchor_price=row["anchor_price"],
            ladder=ladder,
            close_reason=close_reason,
            started_at=None if started is None else text_to_dt(started),
            closed_at=None if closed is None else text_to_dt(closed),
        )
    except ValueError as exc:
        # CycleStatus·CloseReason 의 알 수 없는 값도 ValueError 이며, 그것 역시
        # 행 손상이다. DomainInvariantError 는 ValueError 의 하위이므로 함께 잡힌다.
        raise _corrupt("cycle", rowid, exc) from exc
