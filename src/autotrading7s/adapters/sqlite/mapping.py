"""행 ↔ 도메인 객체 변환.

Plan 1 이 Plan 2 로 넘긴 제약이 이 모듈에서 착륙한다 — H1(복원 실패를 지목),
H3(완전한 단계 집합), H4(trigger_price 대조). H2(tz-aware)는 codec 이 담당한다.

**감싸는 것과 감싸지 않는 것.** 도메인 객체를 복원하다 `DomainInvariantError` 가
나면 그것은 그 행이 손상된 것이므로 `CorruptRowError` 로 감싸 테이블과 rowid 를
붙인다. `TypeError` 는 호출자 버그이므로 감싸지 않고 그대로 올린다 — 개발 중에
드러나야 한다. 저장된 값을 해석하는 도중 나는 `ValueError`(예: 알 수 없는 enum
값) 도 행 손상이므로 감싼다 — `DomainInvariantError` 는 `ValueError` 의 하위라서
`except ValueError` 가 둘 다 잡는다. Task 1 이 이 구분을 만들었다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CloseReason, CycleStatus, StageStatus
from autotrading7s.ports.repository import (  # noqa: F401 — 재수출
    CorruptRowError,
    SplitConfig,
)




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
    except json.JSONDecodeError as exc:
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
        # D20 (설계서 11.4절). realized_pnl 은 여전히 제외한다 — Cycle 에 그
        # 필드가 없고 set_realized_pnl 이 유일한 쓰기 경로다.
        "forced_close_reason": cycle.forced_close_reason,
        "forced_close_qty": cycle.forced_close_qty,
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
            forced_close_reason=row["forced_close_reason"],
            forced_close_qty=row["forced_close_qty"],
        )
    except ValueError as exc:
        # CycleStatus·CloseReason 의 알 수 없는 값도 ValueError 이며, 그것 역시
        # 행 손상이다. DomainInvariantError 는 ValueError 의 하위이므로 함께 잡힌다.
        raise _corrupt("cycle", rowid, exc) from exc


def stage_to_row(cycle_id: int, stage: StageState) -> dict[str, Any]:
    return {
        "cycle_id": cycle_id,
        "stage_no": stage.stage_no,
        "status": stage.status.value,
        "trigger_price": stage.trigger_price,
        "planned_qty": stage.planned_qty,
        "fill_price": stage.fill_price,
        "fill_qty": stage.fill_qty,
        "bought_at": None if stage.bought_at is None else dt_to_text(stage.bought_at),
        "last_sold_at": (
            None if stage.last_sold_at is None else dt_to_text(stage.last_sold_at)
        ),
        "rebuy_count": stage.rebuy_count,
    }


def row_to_stage(row: Mapping[str, Any]) -> StageState:
    rowid = row.get("id")
    try:
        bought = row["bought_at"]
        sold = row["last_sold_at"]
        return StageState(
            stage_no=row["stage_no"],
            status=StageStatus(row["status"]),
            trigger_price=row["trigger_price"],
            planned_qty=row["planned_qty"],
            fill_price=row["fill_price"],
            fill_qty=row["fill_qty"],
            bought_at=None if bought is None else text_to_dt(bought),
            last_sold_at=None if sold is None else text_to_dt(sold),
            rebuy_count=row["rebuy_count"],
        )
    except ValueError as exc:
        raise _corrupt("stage_state", rowid, exc) from exc


def rows_to_stages(
    rows: Sequence[Mapping[str, Any]],
    *,
    cycle_id: int,
    ladder: Ladder | None,
) -> list[StageState]:
    """사이클의 단계 집합을 복원한다. 항상 완전한 집합만 반환한다.

    **H3 — 완전성.** `decide()` 는 없는 단계를 조용히 건너뛰므로(Plan 1 Task 7 의
    판단), 리포지토리가 완전성을 진다. 도메인은 부분 목록을 계속 허용하고 이
    함수는 완전한 것만 준다. `ladder` 가 있으면 기대 개수는 `ladder.max_stages`
    이고, 없으면(STARTING 사이클) 1부터의 연속성과 중복 부재만 본다.

    **H4 — trigger_price 대조.** 설계서 4.2절이 같은 숫자를 `cycle.ladder_json` 과
    `stage_state.trigger_price` 두 곳에 쓰지만 스키마가 둘을 묶지 않는다. Plan 1 의
    최종 리뷰가 재현한 손상은 `trigger_price=999_999` 인 행이 앵커보다 높은 가격의
    매수를 만드는 것이었다. `decide()` 의 대조는 이미 메모리에 있는 상태를
    보호하고, 이 대조는 손상된 행이 메모리에 들어오는 것을 막는다.

    `ladder` 가 `None` 이면 H4 는 검사할 수 없다 — 대조 기준이 없다. 그때는 H3 만
    적용한다.

    반환 순서는 항상 `stage_no` 오름차순이다. DB 가 `ORDER BY` 없이 주더라도
    호출부가 순서에 의존할 수 있어야 한다.

    **cycle_id 대조.** `UNIQUE(cycle_id, stage_no)` 는 같은 사이클 안의 중복만
    막는다 — 다른 사이클의 행이 이 사이클로 섞여 들어오는 것은 스키마로 막히지
    않는다. 의도한 경로(`WHERE cycle_id = ?`)는 이 오염을 만들 수 없지만, 직접
    생성은 그 경로를 우회할 수 있으므로 여기서도 확인한다.

    빈 목록 처리: `ladder` 가 없고 `rows` 도 비면 `expected` 와 `actual` 이 둘 다
    빈 집합이 되어 통과해버린다. 이 경로를 특별히 막지 않는다 — 사이클이
    있으면 단계도 있으므로 `ladder` 없이 빈 목록을 넘기는 호출부는 없다.
    """
    stages = [row_to_stage(row) for row in rows]

    seen: dict[int, StageState] = {}
    id_by_stage_no: dict[int, object] = {}
    for row, stage in zip(rows, stages):
        row_id = row.get("id")
        row_cycle_id = row["cycle_id"]
        if row_cycle_id != cycle_id:
            raise CorruptRowError(
                f"stage_state (id={row_id}) belongs to cycle {row_cycle_id}, "
                f"not cycle {cycle_id}"
            )
        if stage.stage_no in seen:
            raise CorruptRowError(
                f"duplicate stage_no {stage.stage_no} in stage_state "
                f"(id={row_id}) for cycle {cycle_id}"
            )
        seen[stage.stage_no] = stage
        id_by_stage_no[stage.stage_no] = row_id

    if ladder is not None:
        expected = set(range(1, ladder.max_stages + 1))
    else:
        expected = set(range(1, len(seen) + 1)) if seen else set()

    actual = set(seen)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        extra_ids = [id_by_stage_no[n] for n in extra]
        raise CorruptRowError(
            f"incomplete stage_state set for cycle {cycle_id}: "
            f"missing {missing}, unexpected {extra} (id={extra_ids})"
        )

    if ladder is not None:
        for stage_no in sorted(seen):
            stage = seen[stage_no]
            expected_trigger = ladder.trigger_price(stage_no)
            if stage.trigger_price != expected_trigger:
                raise CorruptRowError(
                    f"trigger_price mismatch in stage_state (id="
                    f"{id_by_stage_no[stage_no]}) stage {stage_no} of cycle "
                    f"{cycle_id}: row has {stage.trigger_price}, ladder "
                    f"computes {expected_trigger}"
                )

    return [seen[n] for n in sorted(seen)]
