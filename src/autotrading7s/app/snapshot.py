"""엔진 → GUI 상태 스냅샷 — 설계서 14.1절의 표를 그리기 위한 것.

**`holdings()` 뷰로는 그 표를 그릴 수 없다.** 그 뷰는 `stage_state WHERE
status IN ('HOLDING','SELL_PENDING')` 로 조인하므로 보유 0 인 설정은 행을 만들지
못하고(목업의 `NAVER 0/5 IDLE` 이 그런 행이다), `config_id` 도 없어서 명령을
보낼 대상을 알 수 없다. 스냅샷은 `list_configs()`·`load_active_cycles()`·
`load_stages()` 로 만든다.

스냅샷은 **이벤트**다. 큐 계약이 한 방향(명령 in / 이벤트 out)으로 유지되어야
설계서 7.1절이 말한 "향후 프로세스 분리 시 큐를 소켓으로 교체" 가 성립한다.
요청-응답 채널은 상관 ID 와 블로킹이 필요한 두 번째 프로토콜이다.

**단계는 도메인 객체를 그대로 담는다.** 설계서 14.4절이 표시용 계산조차
`domain/pnl.py` 의 순수 함수를 호출하라고 규정하고, 그 함수들이
`Sequence[StageState]` 를 받는다. 별도 DTO 로 옮기면 뷰모델이 계산을 다시
구현하게 되고 그것이 14.4절이 금지한 것이다. `StageState`·`Ladder` 는 frozen
이므로 큐를 건너도 안전하다.

**이벤트로만 알 수 있는 것은 여기 없다.** 대사 판정, 로드 실패, 시세 폴백
여부는 DB 에서 읽을 수 없다 — 프레젠터가 이벤트에서 누적한다. 스냅샷에도
넣으면 같은 사실의 출처가 둘이 되고 어느 쪽이 최신인지 알 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from autotrading7s.app.events import Event, _require_aware
from autotrading7s.domain.ladder import Ladder
from autotrading7s.domain.stage import StageState
from autotrading7s.domain.types import CycleStatus


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """한 설정과 그 활성 사이클의 상태. 사이클이 없으면 cycle_* 이 None 이다."""

    config_id: int
    stock_code: str
    stock_name: str | None
    label: str | None
    config_status: str                 # IDLE | ACTIVE (설계서 12.1절)
    max_stages: int
    drop_pct: Decimal
    target_pct: Decimal
    amount_per_stage: int
    allow_rebuy: bool
    rebuy_cooldown_sec: int
    stock_limit: int                   # split_config.total_limit — 종목 한도
    cycle_id: int | None
    cycle_seq: int | None
    cycle_status: CycleStatus | None
    anchor_price: int | None
    ladder: Ladder | None
    cycle_started_at: datetime | None
    stages: tuple[StageState, ...]
    pending_orders: int


@dataclass(frozen=True, slots=True)
class Snapshot(Event):
    configs: tuple[ConfigSnapshot, ...]
    total_limit: int                   # 전체 총한도 (EngineSettings)
    at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.at)

    @property
    def core(self) -> tuple[object, ...]:
        """`at` 을 제외한 비교용 값 — 상태가 변했는지만 본다.

        `at` 을 포함하면 매 틱마다 스냅샷이 달라져 유휴 구간에도 큐가 자란다.
        시간 주기로 거르는 대안은 `FakeClock` 이 멈춘 테스트에서 첫 스냅샷만
        나가게 만든다.
        """
        return (self.configs, self.total_limit)
