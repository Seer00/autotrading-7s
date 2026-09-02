# 키움 어댑터 + 인증 구현 계획 (Plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 엔진이 구분하지 못하는 실제 키움증권 REST/WebSocket 어댑터를 만들어, 모의투자 계좌로 전 사이클을 돌릴 수 있는 상태(G3 직전)까지 도달한다.

**Architecture:** `adapters/kiwoom/` 안에 인증·REST·WebSocket·필드매핑을 각각 분리하고, 그 위에 `KiwoomBroker(BrokerPort)` 를 얹는다. 엔진은 `adapters/` 를 모르므로 어댑터가 포트 계약을 **전부** 짊어진다 — 특히 키움이 클라이언트 참조값을 지원하지 않으므로 `OrderStatus.client_ref` 를 어댑터가 대조해서 채운다. 자격증명 접근은 새 포트(`CredentialStorePort`)로 뒤집어 `keyring` 을 어댑터 한 파일에 가둔다. EC2 에는 사용 가능한 keyring 백엔드가 없고(`NoKeyringError`) 실계좌도 없으므로, 그 두 가지를 제외한 전부가 EC2 에서 자동 검증된다.

**Tech Stack:** Python 3.12, `httpx`(비동기 HTTP + `MockTransport`), `websockets`, `keyring`, `tomllib`(표준), pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-09-01-autotrading-7s-design.md`
**확정 사실:** `docs/superpowers/records/2026-09-02-kiwoom-api-confirmed.md` — **이 문서의 값이 유일한 근거다. 여기 없는 값을 지어내지 마라.**
**선행 제약:** `docs/superpowers/records/2026-09-02-plan2b-handover-to-3-and-4.md` (Plan 3 항목 1~7)

---

## Global Constraints

설계서와 확정 기록에서 그대로 옮긴 것. 모든 태스크의 요구사항에 이 절이 암묵적으로 포함된다.

- **`domain/` 은 표준 라이브러리 외 어떤 것도 import 하지 않는다.** `ports/` 도 같다 — `httpx`·`websockets`·`keyring` 은 `adapters/` 안에만 있다. `engine/`·`app/` 은 `adapters/` 를 import 하지 않는다. (`tests/test_g1_gate.py:180`, `tests/test_g2a_gate.py:380` 이 이미 강제한다.)
- **앱키·시크릿·접근토큰을 DB 에 평문 저장하지 않는다.** `token_session` 테이블은 `env`, `app_key_hash`, 발급·만료시각만 담고 토큰 원문을 담지 않는다.
- **신용·미수 관련 필드를 주문 DTO 에 정의하지 않는다.** 신용 TR(`kt10006`~`kt10009`)을 호출하지 않는다.
- **자동 트리거 경로는 시장가를 표현할 수 없다.** `LimitOrderRequest.price` 는 필수이며 `None` 불허. 시장가는 `MarketSellRequest`(긴급청산 전용, `reason` 필수)뿐이다.
- **금액·가격은 원 단위 `int`, 비율만 `Decimal`.** 금액 계산에 `float` 를 쓰지 않는다.
- **도메인의 모든 `datetime` 은 tz-aware.** 키움의 시각 문자열은 KST 이므로 어댑터 경계에서 KST 를 붙여 UTC 로 변환한다.
- **`BrokerTimeout` 은 `TimeoutError` 를 상속하지 않는다.** 어댑터가 `httpx.TimeoutException` 이나 `asyncio.TimeoutError` 를 그대로 새어나가게 하면 엔진의 `except BrokerTimeout` 이 못 잡고 틱 루프가 죽는다. **반드시 `BrokerTimeout` 으로 변환해서 던진다.**
- **`OrderStatus.filled_qty` 는 누적, `filled_price` 는 수량가중평균.** 키움의 체결 필드는 건별이므로 어댑터가 계산한다: `누적 = ord_qty − oso_qty`, `평균 = cntr_tot_amt ÷ 누적`.
- **넓은 `except` 를 두지 않는다.** `CorruptRowError` 가 `ValueError` 의 하위이므로 `except ValueError` 는 DB 손상을 삼킨다. `except Exception` 은 오케스트레이터의 명령 루프(`_safe_handle`) 한 곳에만 정당하다.
- **`BrokerRejected(code, message)` 의 재료는 응답 봉투의 `return_code`·`return_msg` 다.**
- **응답이 유실되면 재발주하지 않는다 (D12).** 조회로 사실을 확인하고, 확인할 수 없으면 멈춘다.
- **자동 손절매를 만들지 않는다.** `decide()` 에 하락 조건 매도 분기가 없다는 사실이 이 계획으로 바뀌지 않는다.
- 로깅 필터로 토큰·시크릿 패턴을 마스킹한다.
- 툴 호출 파라미터의 한글은 리터럴 UTF-8 로 쓰고 `\uXXXX` 이스케이프를 쓰지 않는다.

### 확정된 키움 값 (지어내지 말고 이 표를 쓴다)

| 이름 | 값 |
|---|---|
| REST 실전 / 모의 | `https://api.kiwoom.com` / `https://mockapi.kiwoom.com` |
| WS 실전 / 모의 | `wss://api.kiwoom.com` / `wss://mockapi.kiwoom.com` |
| WS 경로 | `/api/dostk/websocket` |
| 공통 헤더 | `api-id`, `authorization`(=`Bearer <token>`), `cont-yn`, `next-key` |
| Content-Type | `application/json;charset=UTF-8` |
| 토큰 발급 / 폐기 | `au10001` `POST /oauth2/token` / `au10002` `POST /oauth2/revoke` |
| 토큰 요청 body | `grant_type`(=`client_credentials`), `appkey`, `secretkey` |
| 토큰 응답 body | `token`, `token_type`, `expires_dt`(**KST `YYYYMMDDHHMMSS`**) |
| 매수 / 매도 / 취소 | `kt10000` / `kt10001` / `kt10003` — 전부 `POST /api/dostk/ordr` |
| 주문 요청 body | `dmst_stex_tp`, `stk_cd`, `ord_qty`, `ord_uv`, `trde_tp`, `cond_uv` |
| 취소 요청 body | `dmst_stex_tp`, `orig_ord_no`, `stk_cd`, `cncl_qty` |
| 주문 응답 body | `ord_no`, `dmst_stex_tp` |
| `trde_tp` | `0`=보통(지정가), `3`=시장가 |
| `dmst_stex_tp` | `KRX` (이 계획은 KRX 고정. `NXT`·`SOR` 은 범위 밖) |
| 미체결 / 체결 조회 | `ka10075` / `ka10076` — `POST /api/dostk/acnt` |
| 계좌평가잔고 / 예수금 | `kt00018` / `kt00001` — `POST /api/dostk/acnt` |
| 주식기본정보(현재가) | `ka10001` `POST /api/dostk/stkinfo` |
| 실시간 시세 / 주문체결 | `0B` / `00` |
| 선제 갱신 여유 | **600초** (설계서 8.3절의 60초를 정정 — 60초는 갱신 실패 시 재시도 여유가 없다) |

**미확정이라 지어내지 않는 것**: TR 별 호출 제한 수치, `kt00018` 의 0수량 종목 포함 여부, WebSocket 하트비트 규격, 휴장일 판단. 전부 `endpoints.toml` 의 값으로 빼거나 관측 후 확정한다.

---

## 이 계획이 바꾸는 두 가지 설계 결정

실행자가 "설계서와 다르다" 고 판단해 되돌리지 않도록 여기에 근거를 남긴다. **둘 다 사용자 승인을 받았다.**

### 결정 A — `client_ref` 대조를 어댑터의 책임으로 옮긴다

설계서 9절 ⑤는 응답 유실 시 `list_orders_today` 로 **`client_ref` 를 대조**해 접수 여부를 확인하라고 규정한다. **키움 API 에는 클라이언트 참조값을 넣을 자리가 없다** (337개 API 전 필드 확인, 확정 기록 "결정적 사실 1").

포트 계약은 바꾸지 않는다. `client_ref` 는 우리 DB 의 내부 식별자로 그대로 남고, 키움에 보내지 않을 뿐이다. 어댑터가 **발주 대장**(in-flight ledger)을 들고 있다가 WebSocket `00`(주문체결) 푸시 또는 `ka10075`/`ka10076` 응답과 대조해 `OrderStatus.client_ref` 를 채워서 돌려준다. **엔진의 D12 로직은 한 줄도 바뀌지 않는다.**

대조 키는 `(stk_cd, 매도수, ord_qty, ord_uv)` 이고 발주시각 이후로 한정한다. 모호성은 어댑터의 불변식으로 막는다: **동일 `(종목, 매도수, 단가)` 의 미결 주문은 최대 하나.** 사다리가 단계별로 서로 다른 발동가를 보장하므로 정상 동작을 제약하지 않는다. 그래도 후보가 둘 이상이면 **`client_ref` 를 채우지 않는다** — 엔진은 Plan 2B 가 만든 `UNKNOWN_UNRESOLVED` 경로로 가고 재발주하지 않는다.

WebSocket 푸시를 1순위로 쓰는 이유는 폴링보다 강하기 때문이다. `913='접수'` 가 접수 사실을, `9203` 이 주문번호를 알려주므로 REST 응답이 유실돼도 사실을 안다. REST 조회는 WS 가 끊긴 구간의 폴백이다.

### 결정 B — `Balance` 에 완전성 필드를 추가한다 (포트 변경)

`engine/emergency.broker_qty` 는 `Balance.holdings` 에 종목이 없으면 `None` 을 반환하고, 엔진은 그것을 **긴급청산 중단 사유**로 쓴다. 근거: "응답에 없음" 은 "보유 0" 의 증거가 아니다.

`kt00018` 이 보유수량 0 인 종목을 생략하는지는 명세에 없다. 생략한다면 전량 매도된 종목의 긴급청산이 **항상 실패**한다.

그래서 `Balance` 에 `listing_complete: bool` 을 추가한다. 어댑터가 **연속조회를 끝까지 페이징해 완전한 계좌 스냅샷을 얻었을 때만** `True` 로 채운다. `broker_qty` 는 완전한 스냅샷에서 종목이 없으면 `0` 을, 불완전하면 `None` 을 반환한다.

이것이 원래 계약보다 나은 이유: 진짜 위험은 "조회가 부실했는데 0 으로 오인하는 것" 이고, 그 위험이 이제 타입으로 표현된다. 페이징을 중간에 끊으면 `listing_complete=False` 가 되어 긴급청산이 멈춘다 — 조용히 0 으로 보고하는 것보다 항상 낫다.

---

## File Structure

```
src/autotrading7s/
├── ports/
│   ├── credentials.py     [신규] CredentialStorePort — 앱키·시크릿·토큰의 저장 창구
│   ├── broker.py          [수정] Balance.listing_complete 추가
│   └── repository.py      [수정] token_session 접근자 2개
├── adapters/
│   ├── keyring_store.py   [신규] KeyringCredentialStore — keyring 을 이 파일에만 둔다
│   ├── logging_filter.py  [신규] 토큰·시크릿 마스킹 필터
│   ├── sqlite/
│   │   └── repository.py  [수정] token_session 구현 (앱키를 받아 내부에서 해시)
│   └── kiwoom/
│       ├── __init__.py    [신규]
│       ├── endpoints.toml [신규] 확정값 + 미확정 파라미터
│       ├── config.py      [신규] endpoints.toml 로더. 플레이스홀더를 거부한다
│       ├── codes.py       [신규] trde_tp·거래소·주문상태·FID 상수
│       ├── numbers.py     [신규] 부호 0-padding 정수, 종목코드 접두어
│       ├── mapping.py     [신규] 요청 body 조립 + 응답 → 도메인 DTO
│       ├── auth.py        [신규] TokenManager
│       ├── rest.py        [신규] RestClient — 헤더·봉투·예외변환·재시도·레이트리밋·연속조회
│       ├── ws.py          [신규] QuoteStream(0B) + OrderStream(00)
│       ├── ledger.py      [신규] InFlightLedger — 결정 A 의 발주 대장
│       └── broker.py      [신규] KiwoomBroker(BrokerPort)
├── engine/
│   └── emergency.py       [수정] broker_qty 가 listing_complete 를 본다
├── app/
│   └── events.py          [수정] AuthFailed 이벤트
└── cli.py                 [수정] --env real 경로 활성화 + --kiwoom 스위치

tests/
├── adapters/kiwoom/
│   ├── test_config.py, test_numbers.py, test_codes.py
│   ├── test_mapping_orders.py, test_mapping_status.py, test_mapping_balance.py
│   ├── test_rest.py, test_rest_retry.py, test_rest_paging.py
│   ├── test_auth.py, test_ws_quotes.py, test_ws_orders.py
│   ├── test_ledger.py, test_broker.py
│   └── test_broker_d12.py     결정 A 의 핵심 — 응답 유실 후 재발주하지 않는다
├── adapters/test_keyring_store.py, test_logging_filter.py
├── ports/test_credentials.py
└── test_g3_prep_gate.py       Plan 3 게이트
```

**파일을 이렇게 쪼개는 이유**: `mapping.py` 하나에 몰면 "주문 요청 조립" 과 "잔고 응답 해석" 이 같은 파일에서 섞이고, 둘의 실패 결과가 전혀 다르다(잘못된 주문 vs 잘못된 대사). `numbers.py` 를 따로 두는 것은 부호 0-padding 파싱과 종목코드 접두어가 **모든** 매핑의 공통 전제이기 때문이며, 그 두 개가 틀리면 나머지 전부가 조용히 틀린다.

---

## Task 1: 자격증명 포트와 keyring 어댑터, 그리고 로깅 마스킹

**Files:**
- Create: `src/autotrading7s/ports/credentials.py`
- Create: `src/autotrading7s/adapters/keyring_store.py`
- Create: `src/autotrading7s/adapters/logging_filter.py`
- Test: `tests/ports/test_credentials.py`, `tests/adapters/test_keyring_store.py`, `tests/adapters/test_logging_filter.py`

**Interfaces:**
- Produces: `CredentialStorePort` (Protocol, `runtime_checkable`) — `app_key(env)`, `app_secret(env)`, `save_token(env, token, expires_at)`, `token(env)`, `clear_token(env)`. `KeyringCredentialStore(service="autotrading7s")`. `MaskingFilter()` (logging.Filter 하위). `CredentialsMissing(Exception)`.
- Consumes: 없음.

**왜 포트로 뒤집는가**: `keyring` 은 EC2 에서 사용 가능한 백엔드가 없어 `NoKeyringError` 를 던진다(실측). `TokenManager` 가 `keyring` 을 직접 import 하면 인증 로직 전체가 EC2 에서 검증 불가가 된다. Plan 4 가 `tkinter` 에 대해 한 것과 같은 대응이다.

- [ ] **Step 1: 포트 계약 테스트를 먼저 쓴다**

`tests/ports/test_credentials.py`:

```python
from __future__ import annotations

import inspect

from autotrading7s.ports.credentials import CredentialStorePort


def test_the_port_declares_the_expected_methods():
    """집합으로 단정하므로 추가·삭제가 눈에 띈다."""
    expected = {"app_key", "app_secret", "save_token", "token", "clear_token"}
    declared = {
        name for name, _ in inspect.getmembers(CredentialStorePort,
                                               inspect.isfunction)
        if not name.startswith("_")
    }
    assert declared == expected


def test_the_port_is_runtime_checkable():
    assert getattr(CredentialStorePort, "_is_runtime_protocol", False) is True


def test_the_port_does_not_expose_a_way_to_read_the_raw_secret_by_name():
    """`get(key_name)` 같은 범용 조회를 두지 않는 이유: 호출자가 임의의
    키 이름으로 시크릿을 꺼내갈 수 있으면 어떤 값이 어디로 흐르는지
    타입으로 추적할 수 없다. 메서드가 용도별로 나뉘어 있어야 로깅·감사에서
    '시크릿을 읽은 지점' 을 셀 수 있다."""
    assert not hasattr(CredentialStorePort, "get")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/ports/test_credentials.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autotrading7s.ports.credentials'`

- [ ] **Step 3: 포트를 만든다**

`src/autotrading7s/ports/credentials.py`:

```python
"""자격증명 저장 포트 — 설계서 13.1절.

앱키·시크릿·접근토큰은 DB 에 넣지 않는다. Windows 에서는 `keyring` 이 자격 증명
관리자에 넣고, 그 구현은 `adapters/keyring_store.py` 에만 있다.

**포트로 뒤집는 이유**: EC2 에는 사용 가능한 keyring 백엔드가 없어 `keyring`
호출이 `NoKeyringError` 를 던진다. `TokenManager` 가 `keyring` 을 직접 import
하면 인증 로직 전체가 EC2 에서 검증 불가가 된다. Plan 4 가 `tkinter` 에 대해
한 것과 같은 대응이다.

메서드를 용도별로 나눈 것도 설계다. `get(key_name)` 같은 범용 조회를 두면
호출자가 임의의 이름으로 시크릿을 꺼내갈 수 있고, 그러면 어떤 값이 어디로
흐르는지 타입으로 추적할 수 없다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


class CredentialsMissing(Exception):
    """저장소에 그 환경의 자격증명이 없다.

    조용히 `None` 을 반환하지 않는 이유: 키가 없는 채로 인증을 시도하면
    키움이 4xx 로 거부하고, 그 메시지는 "키가 등록되지 않았다" 가 아니라
    "인증 실패" 로 보인다. 사용자가 봐야 하는 것은 앞쪽이다.
    """


@runtime_checkable
class CredentialStorePort(Protocol):
    def app_key(self, env: str) -> str:
        """그 환경의 앱키. 없으면 `CredentialsMissing`."""
        ...

    def app_secret(self, env: str) -> str:
        """그 환경의 시크릿키. 없으면 `CredentialsMissing`."""
        ...

    def save_token(self, env: str, token: str, expires_at: datetime) -> None:
        """접근토큰과 만료시각을 저장한다. `expires_at` 은 tz-aware 여야 한다."""
        ...

    def token(self, env: str) -> tuple[str, datetime] | None:
        """저장된 접근토큰과 만료시각. 없으면 `None`.

        여기서 `None` 이 정당한 이유: 토큰의 부재는 정상 상태다(첫 기동,
        폐기 직후). 앱키의 부재와 달리 사용자가 조치할 일이 없다.
        """
        ...

    def clear_token(self, env: str) -> None:
        """저장된 토큰을 지운다. 없어도 조용히 성공한다."""
        ...
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/ports/test_credentials.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: keyring 어댑터의 테스트를 쓴다**

`keyring` 을 직접 부르지 않고 모듈의 함수를 주입한다 — EC2 에서 백엔드가 없어도 어댑터의 **로직**을 검증할 수 있어야 한다.

`tests/adapters/test_keyring_store.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autotrading7s.adapters.keyring_store import KeyringCredentialStore
from autotrading7s.ports.credentials import CredentialsMissing


class FakeKeyring:
    """keyring 모듈의 세 함수만 흉내낸다.

    실제 `keyring` 을 쓰지 않는 이유: EC2 에는 사용 가능한 백엔드가 없어
    `NoKeyringError` 가 난다. 그렇다고 이 어댑터를 검증 불가로 두면 키 이름
    규칙(설계서 13.1절)과 환경 분리가 아무도 확인하지 않는 채 남는다.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.deleted: list[tuple[str, str]] = []

    def get_password(self, service: str, name: str) -> str | None:
        return self.store.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        self.store[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        if (service, name) not in self.store:
            raise KeyError(name)
        del self.store[(service, name)]
        self.deleted.append((service, name))


def a_store(fake: FakeKeyring | None = None) -> KeyringCredentialStore:
    fake = fake or FakeKeyring()
    return KeyringCredentialStore(
        get_password=fake.get_password, set_password=fake.set_password,
        delete_password=fake.delete_password,
    )


def test_the_key_names_follow_the_spec():
    """설계서 13.1절의 키 이름 규칙 — `autotrading7s:{env}:app_key` 등.

    이름을 고정하는 이유: 사용자가 Windows 자격 증명 관리자에서 직접 등록할
    수도 있고, 이름이 다르면 프로그램이 그것을 찾지 못한다.
    """
    fake = FakeKeyring()
    store = a_store(fake)
    fake.set_password("autotrading7s", "mock:app_key", "K")
    fake.set_password("autotrading7s", "mock:app_secret", "S")
    assert store.app_key("mock") == "K"
    assert store.app_secret("mock") == "S"


def test_the_environments_do_not_share_credentials():
    """D15 — 모의와 실전이 절대 섞이지 않는다.

    섞이면 모의투자 키로 실전에 붙거나 그 반대가 되고, 후자는 실제 주문이
    나간다.
    """
    fake = FakeKeyring()
    store = a_store(fake)
    fake.set_password("autotrading7s", "mock:app_key", "MOCK")
    with pytest.raises(CredentialsMissing, match="real"):
        store.app_key("real")


def test_a_missing_app_key_raises_instead_of_returning_none():
    with pytest.raises(CredentialsMissing):
        a_store().app_key("mock")


def test_the_token_round_trips_with_an_aware_expiry():
    store = a_store()
    at = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    store.save_token("mock", "T", at)
    got = store.token("mock")
    assert got is not None
    token, expires_at = got
    assert token == "T"
    assert expires_at == at
    assert expires_at.tzinfo is not None


def test_saving_a_naive_expiry_is_rejected():
    """도메인의 모든 datetime 은 tz-aware 다. naive 를 저장하면 다음 기동에서
    9시간 어긋난 만료시각으로 되살아나고, 만료된 토큰을 유효하다고 믿는다."""
    with pytest.raises(ValueError, match="aware"):
        a_store().save_token("mock", "T", datetime(2026, 9, 2, 9, 0))


def test_an_absent_token_is_none_not_an_error():
    assert a_store().token("mock") is None


def test_clearing_an_absent_token_is_quiet():
    """폐기는 멱등이어야 한다 — 토큰이 없는 상태에서 폐기를 부르는 것은
    정상 흐름이다(기동 실패 후 재시도)."""
    a_store().clear_token("mock")          # 예외가 나면 실패


def test_a_corrupt_stored_token_is_treated_as_absent():
    """저장된 값이 우리 형식이 아니면 없는 것으로 본다.

    사용자가 자격 증명 관리자에서 손으로 고칠 수 있는 자리이므로, 깨진 값에
    크래시하면 기동조차 못 한다. 없는 것으로 보면 재발급으로 복구된다.
    """
    fake = FakeKeyring()
    fake.set_password("autotrading7s", "mock:access_token", "쓰레기")
    assert a_store(fake).token("mock") is None


def test_the_store_satisfies_the_port():
    from autotrading7s.ports.credentials import CredentialStorePort
    assert isinstance(a_store(), CredentialStorePort)
```

- [ ] **Step 6: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/test_keyring_store.py -q`
Expected: FAIL — `ModuleNotFoundError: ...keyring_store`

- [ ] **Step 7: keyring 어댑터를 만든다**

`src/autotrading7s/adapters/keyring_store.py`:

```python
"""keyring 자격증명 저장소 — 설계서 13.1절.

**`keyring` 을 import 하는 유일한 파일이다.** 다른 어디에서도 import 하지
않으며, `tests/test_g3_prep_gate.py` 가 그것을 강제한다. EC2 에는 사용 가능한
백엔드가 없어 `keyring` 호출이 `NoKeyringError` 를 던지므로, 이 파일 밖에서
부르면 그 지점 전체가 EC2 에서 검증 불가가 된다.

세 함수를 주입받는 이유도 같다. 기본값은 실제 `keyring` 이지만 테스트는
가짜를 넣어 키 이름 규칙과 환경 분리를 검증한다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from autotrading7s.ports.credentials import CredentialsMissing

SERVICE = "autotrading7s"


def _default_get(service: str, name: str) -> str | None:
    import keyring
    return keyring.get_password(service, name)


def _default_set(service: str, name: str, value: str) -> None:
    import keyring
    keyring.set_password(service, name, value)


def _default_delete(service: str, name: str) -> None:
    import keyring
    keyring.delete_password(service, name)


class KeyringCredentialStore:
    def __init__(
        self,
        *,
        service: str = SERVICE,
        get_password: Callable[[str, str], str | None] = _default_get,
        set_password: Callable[[str, str, str], None] = _default_set,
        delete_password: Callable[[str, str], None] = _default_delete,
    ) -> None:
        self._service = service
        self._get = get_password
        self._set = set_password
        self._delete = delete_password

    # ── 이름 규칙 (설계서 13.1절) ────────────────────────────────────────
    @staticmethod
    def _name(env: str, kind: str) -> str:
        return f"{env}:{kind}"

    def _require(self, env: str, kind: str) -> str:
        value = self._get(self._service, self._name(env, kind))
        if not value:
            raise CredentialsMissing(
                f"{env} 환경의 {kind} 가 자격 증명 관리자에 없습니다 "
                f"({self._service}/{self._name(env, kind)})"
            )
        return value

    def app_key(self, env: str) -> str:
        return self._require(env, "app_key")

    def app_secret(self, env: str) -> str:
        return self._require(env, "app_secret")

    def save_token(self, env: str, token: str, expires_at: datetime) -> None:
        if expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        payload = json.dumps(
            {"token": token, "expires_at": expires_at.isoformat()},
            ensure_ascii=False,
        )
        self._set(self._service, self._name(env, "access_token"), payload)

    def token(self, env: str) -> tuple[str, datetime] | None:
        raw = self._get(self._service, self._name(env, "access_token"))
        if not raw:
            return None
        # 사용자가 자격 증명 관리자에서 손으로 고칠 수 있는 자리다. 깨진 값에
        # 크래시하면 기동조차 못 하므로, 없는 것으로 보고 재발급에 맡긴다.
        try:
            data = json.loads(raw)
            token = str(data["token"])
            expires_at = datetime.fromisoformat(str(data["expires_at"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if expires_at.tzinfo is None:
            return None
        return token, expires_at

    def clear_token(self, env: str) -> None:
        # 폐기는 멱등이어야 한다 — 토큰이 없는 상태에서 부르는 것은 정상
        # 흐름이다(기동 실패 후 재시도).
        try:
            self._delete(self._service, self._name(env, "access_token"))
        except KeyError:
            pass
```

- [ ] **Step 8: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/test_keyring_store.py -q`
Expected: PASS (9 passed)

`keyring` 의 실제 예외 타입(`keyring.errors.PasswordDeleteError`)도 `clear_token` 에서 삼켜야 한다. `except KeyError` 만으로는 부족하다 — 다음 스텝에서 다룬다.

- [ ] **Step 9: 실제 keyring 의 삭제 예외도 삼키는지 테스트한다**

`tests/adapters/test_keyring_store.py` 에 추가:

```python
def test_clearing_swallows_the_real_keyring_delete_error():
    """`keyring` 은 없는 항목을 지울 때 `PasswordDeleteError` 를 던진다.

    `KeyError` 만 삼키면 Windows 에서 기동 실패 후 재시도가 크래시한다 —
    EC2 에서는 백엔드가 없어 이 경로가 드러나지 않으므로 테스트로 고정한다.
    """
    class Boom(Exception):
        pass

    def delete(service: str, name: str) -> None:
        raise Boom("not found")

    store = KeyringCredentialStore(
        get_password=lambda s, n: None,
        set_password=lambda s, n, v: None,
        delete_password=delete,
    )
    with pytest.raises(Boom):
        store.clear_token("mock")       # 지금은 새어나간다
```

- [ ] **Step 10: 실패를 확인하고 정정한다**

Run: `.venv/bin/python -m pytest tests/adapters/test_keyring_store.py::test_clearing_swallows_the_real_keyring_delete_error -q`
Expected: PASS (`Boom` 이 새어나오는 것이 현재 동작)

이제 계약을 바꾼다. 주입된 삭제 함수가 무엇을 던질지 어댑터는 알 수 없으므로, **"없으면 조용히"** 를 확인 후 삭제로 표현한다.

`clear_token` 을 다음으로 교체:

```python
    def clear_token(self, env: str) -> None:
        # 폐기는 멱등이어야 한다. 주입된 삭제 함수가 무엇을 던질지 알 수 없고
        # (`keyring` 은 `PasswordDeleteError`), 넓은 `except` 는 이 프로젝트가
        # 금지하므로, 존재를 먼저 확인해서 던질 상황 자체를 만들지 않는다.
        name = self._name(env, "access_token")
        if self._get(self._service, name) is None:
            return
        self._delete(self._service, name)
```

그리고 위 테스트를 뒤집는다:

```python
def test_clearing_an_absent_token_never_calls_delete():
    """주입된 삭제 함수가 무엇을 던질지 어댑터는 알 수 없다(`keyring` 은
    `PasswordDeleteError`). 넓은 `except` 는 이 프로젝트가 금지하므로,
    존재를 먼저 확인해 던질 상황을 만들지 않는다."""
    calls: list[str] = []

    store = KeyringCredentialStore(
        get_password=lambda s, n: None,
        set_password=lambda s, n, v: None,
        delete_password=lambda s, n: calls.append(n),
    )
    store.clear_token("mock")
    assert calls == []
```

- [ ] **Step 11: 두 테스트 모두 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/test_keyring_store.py -q`
Expected: PASS (`test_clearing_swallows_the_real_keyring_delete_error` 는 삭제하고 위 테스트로 대체)

- [ ] **Step 12: 로깅 마스킹 필터의 테스트를 쓴다**

`tests/adapters/test_logging_filter.py`:

```python
from __future__ import annotations

import logging

from autotrading7s.adapters.logging_filter import MaskingFilter


def a_record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


def masked(msg: str, *args: object) -> str:
    record = a_record(msg, *args)
    assert MaskingFilter().filter(record) is True     # 항상 통과시킨다
    return record.getMessage()


def test_a_bearer_token_is_masked():
    out = masked("authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abcdefghij")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "Bearer" in out and "***" in out


def test_the_secretkey_field_is_masked():
    out = masked('{"appkey": "AK123456789", "secretkey": "SK987654321"}')
    assert "SK987654321" not in out
    assert "AK123456789" not in out


def test_the_token_field_in_a_json_body_is_masked():
    out = masked('{"token": "abcdefghijklmnopqrstuvwxyz", "token_type": "Bearer"}')
    assert "abcdefghijklmnopqrstuvwxyz" not in out


def test_masking_applies_to_the_formatted_message_not_just_the_template():
    """`logger.info("token=%s", tok)` 처럼 값이 인자로 오는 형태.

    템플릿만 검사하면 이 형태가 통째로 새어나간다 — 그리고 실전 코드는
    거의 항상 이 형태로 쓴다.
    """
    out = masked("authorization: %s", "Bearer eyJsecrettokenvalue123456")
    assert "eyJsecrettokenvalue123456" not in out


def test_an_ordinary_message_is_untouched():
    """과잉 마스킹은 로그를 쓸모없게 만든다 — 주문 실패 원인을 못 읽는다."""
    out = masked("주문 접수: 005930 100주 @9,500 (ord_no=00024)")
    assert out == "주문 접수: 005930 100주 @9,500 (ord_no=00024)"


def test_a_short_value_that_looks_like_a_price_is_not_masked():
    out = masked('{"ord_uv": "9500"}')
    assert "9500" in out
```

- [ ] **Step 13: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/test_logging_filter.py -q`
Expected: FAIL — `ModuleNotFoundError: ...logging_filter`

- [ ] **Step 14: 마스킹 필터를 만든다**

`src/autotrading7s/adapters/logging_filter.py`:

```python
"""로깅 마스킹 필터 — 설계서 13.1절.

토큰·시크릿이 예외 트레이스백이나 요청 덤프로 새는 것을 막는다.

**포맷된 메시지를 검사하는 것이 이 모듈의 핵심이다.** 템플릿만 보면
`logger.info("authorization: %s", header)` 형태가 통째로 새어나가고, 실전
코드는 거의 항상 그 형태로 쓴다. 그래서 `record.getMessage()` 로 인자를 펼친
뒤 마스킹하고 `record.msg` 를 그 결과로 바꾸고 `record.args` 를 비운다.

과잉 마스킹도 결함이다. 주문 실패의 원인을 로그에서 읽을 수 없으면 사고를
사후에 분석할 수 없다. 그래서 값이 충분히 긴 경우만, 알려진 이름 옆에서만
가린다.
"""

from __future__ import annotations

import logging
import re

_MIN = 12       # 이보다 짧으면 가리지 않는다 — 가격·수량을 가리면 안 된다

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer <토큰>
    re.compile(rf"(Bearer\s+)([A-Za-z0-9._~+/=-]{{{_MIN},}})"),
    # JSON 의 token / appkey / secretkey / app_secret / access_token
    re.compile(
        r"(\"(?:token|appkey|secretkey|app_key|app_secret|access_token)\"\s*:\s*\")"
        rf"([^\"]{{{_MIN},}})(\")"
    ),
    # key=value 형태
    re.compile(
        r"((?:token|appkey|secretkey|app_key|app_secret|access_token)=)"
        rf"([A-Za-z0-9._~+/=-]{{{_MIN},}})"
    ),
)


def mask(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(
            lambda m: m.group(1) + "***" + (m.group(3) if m.lastindex == 3 else ""),
            text,
        )
    return text


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # 인자를 펼친 뒤 마스킹한다 — 템플릿만 검사하면 `%s` 로 실린 값이
        # 그대로 새어나간다.
        message = record.getMessage()
        masked = mask(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        # 항상 통과시킨다. 이 필터의 일은 거르는 것이 아니라 가리는 것이다 —
        # 레코드를 떨어뜨리면 사고 분석에 필요한 줄이 사라진다.
        return True
```

`appkey` 는 위 테스트가 `AK123456789`(11자)를 가리라고 요구하는데 `_MIN=12` 라 걸리지 않는다. **테스트가 요구하는 쪽이 맞다** — 앱키는 짧아도 시크릿이다. 앱키·시크릿 이름 옆의 값은 길이 조건 없이 가린다.

- [ ] **Step 15: 이름별로 길이 조건을 나눈다**

`_PATTERNS` 를 다음으로 교체:

```python
# 이름이 확실한 것(앱키·시크릿·토큰 필드)은 길이 조건 없이 가린다 — 앱키는
# 짧아도 시크릿이다. 길이 조건은 `Bearer` 뒤처럼 이름이 없는 자리에만 쓴다.
_NAMED = r"token|appkey|secretkey|app_key|app_secret|access_token"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"(Bearer\s+)([A-Za-z0-9._~+/=-]{{{_MIN},}})"),
    re.compile(rf"(\"(?:{_NAMED})\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(rf"((?:{_NAMED})=)([A-Za-z0-9._~+/=-]+)"),
)
```

`token_type` 이 `token` 패턴에 걸려 `"Bearer"` 까지 가려지는 문제가 남는다. `"token"` 뒤에 곧바로 `"` 가 오는 것을 요구하므로 `"token_type"` 은 걸리지 않는다 — 정규식이 `\"(?:token)\"` 로 닫혀 있기 때문이다. 위 테스트 `test_the_token_field_in_a_json_body_is_masked` 가 그것을 확인한다.

- [ ] **Step 16: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/test_logging_filter.py -q`
Expected: PASS (6 passed)

- [ ] **Step 17: 커밋**

```bash
git add src/autotrading7s/ports/credentials.py \
        src/autotrading7s/adapters/keyring_store.py \
        src/autotrading7s/adapters/logging_filter.py \
        tests/ports/test_credentials.py \
        tests/adapters/test_keyring_store.py \
        tests/adapters/test_logging_filter.py
git commit -m "feat: 자격증명 포트와 keyring 어댑터, 로깅 마스킹 필터"
```

> **경로를 명시해서 스테이징하는 이유**: 이 저장소의 `.gitignore` 가 현재
> `.venv/` 를 추적 대상으로 두고 있다. `git add -A` 를 쓰면 30MB 가 커밋에
> 휩쓸려 들어간다. **모든 태스크의 커밋에서 경로를 명시하라.**

---

## Task 2: endpoints.toml 과 그 로더

**Files:**
- Create: `src/autotrading7s/adapters/kiwoom/__init__.py`
- Create: `src/autotrading7s/adapters/kiwoom/endpoints.toml`
- Create: `src/autotrading7s/adapters/kiwoom/config.py`
- Test: `tests/adapters/kiwoom/test_config.py`

**Interfaces:**
- Produces: `KiwoomConfig` (frozen dataclass) — `rest_base`, `ws_base`, `ws_path`, `tr: Mapping[str, str]`, `exchange`, `token_refresh_buffer_sec`, `rate_limit_per_sec`, `request_timeout_sec`, `max_pages`. `load_config(env: str, path: Path | None = None) -> KiwoomConfig`. `ConfigError(Exception)`.
- Consumes: 없음.

**왜 파일로 빼는가**: 설계서 7절 1항 — 사양 변경이 "코드 수정 + 재테스트" 가 아니라 "설정값 교체" 로 끝나야 한다. 키움은 엔드포인트가 아니라 `api-id` 헤더로 TR 을 가르므로 바뀌는 것은 거의 항상 TR 코드다.

- [ ] **Step 1: 테스트를 먼저 쓴다**

`tests/adapters/kiwoom/test_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from autotrading7s.adapters.kiwoom.config import (
    ConfigError,
    KiwoomConfig,
    load_config,
)


def test_the_packaged_config_loads_for_both_environments():
    """패키지에 들어 있는 endpoints.toml 이 실제로 로드된다.

    이 파일은 썩는다 — 키를 바꾸거나 오타를 내면 조용히 무효가 되고, 그것을
    발견하는 사람은 실계좌에 붙이려는 사람이다.
    """
    for env in ("mock", "real"):
        cfg = load_config(env)
        assert isinstance(cfg, KiwoomConfig)


def test_the_environments_point_at_different_hosts():
    """D15 — 모의와 실전이 절대 섞이지 않는다. 같은 호스트를 가리키면
    모의투자라고 믿는 채로 실제 주문이 나간다."""
    mock, real = load_config("mock"), load_config("real")
    assert mock.rest_base == "https://mockapi.kiwoom.com"
    assert real.rest_base == "https://api.kiwoom.com"
    assert mock.ws_base == "wss://mockapi.kiwoom.com"
    assert real.ws_base == "wss://api.kiwoom.com"
    assert mock.rest_base != real.rest_base


def test_the_confirmed_tr_codes_are_present_and_exact():
    """확정 기록의 값과 한 글자도 다르면 안 된다 — 틀린 api-id 는 "인증 실패"
    나 "권한 없음" 처럼 원인과 무관한 메시지로 나타난다."""
    tr = load_config("mock").tr
    assert tr["token_issue"] == "au10001"
    assert tr["token_revoke"] == "au10002"
    assert tr["order_buy"] == "kt10000"
    assert tr["order_sell"] == "kt10001"
    assert tr["order_cancel"] == "kt10003"
    assert tr["order_unfilled"] == "ka10075"
    assert tr["order_filled"] == "ka10076"
    assert tr["balance"] == "kt00018"
    assert tr["deposit"] == "kt00001"
    assert tr["price_current"] == "ka10001"
    assert tr["realtime_quote"] == "0B"
    assert tr["realtime_order"] == "00"


def test_the_paths_are_present_and_exact():
    cfg = load_config("mock")
    assert cfg.path["token_issue"] == "/oauth2/token"
    assert cfg.path["token_revoke"] == "/oauth2/revoke"
    assert cfg.path["order"] == "/api/dostk/ordr"
    assert cfg.path["account"] == "/api/dostk/acnt"
    assert cfg.path["stkinfo"] == "/api/dostk/stkinfo"
    assert cfg.ws_path == "/api/dostk/websocket"


def test_credit_order_codes_are_absent():
    """설계서 6절 — 신용·미수는 타입 차원에서 배제했다. 설정에 코드가 있으면
    누군가 그것을 쓸 수 있고, 원칙이 문서상의 약속으로 격하된다."""
    tr = load_config("mock").tr
    assert not any(code in tr.values()
                   for code in ("kt10006", "kt10007", "kt10008", "kt10009"))


def test_the_exchange_is_krx():
    """이 계획은 KRX 고정이다. SOR 은 NXT 까지 라우팅해 체결이 유리할 수
    있지만 호가 단위와 대사의 거래소 구분이 흔들린다 — 우리 호가 로직이
    KRX 전제다."""
    assert load_config("mock").exchange == "KRX"


def test_an_unknown_environment_is_rejected():
    with pytest.raises(ConfigError, match="env"):
        load_config("prod")


def test_a_placeholder_value_is_rejected(tmp_path):
    """설계서 8.3절이 남긴 `<공식 문서로 확정>` 같은 값이 남아 있으면
    기동 자체가 실패해야 한다. 플레이스홀더로 요청을 보내면 키움은 4xx 를
    주고, 그 메시지는 "설정이 비어 있다" 가 아니라 "잘못된 요청" 이다."""
    bad = tmp_path / "endpoints.toml"
    bad.write_text(
        '[env.mock]\nrest_base = "<공식 문서로 확정>"\nws_base = "wss://x"\n'
        'ws_path = "/w"\n[path]\ntoken_issue = "/t"\n[tr]\ntoken_issue = "au10001"\n'
        '[limits]\ntoken_refresh_buffer_sec = 600\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="확정"):
        load_config("mock", bad)


def test_an_empty_value_is_rejected(tmp_path):
    bad = tmp_path / "endpoints.toml"
    bad.write_text(
        '[env.mock]\nrest_base = ""\nws_base = "wss://x"\nws_path = "/w"\n'
        '[path]\ntoken_issue = "/t"\n[tr]\ntoken_issue = "au10001"\n'
        '[limits]\ntoken_refresh_buffer_sec = 600\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config("mock", bad)


def test_the_refresh_buffer_is_600_not_60():
    """설계서 8.3절은 60초로 적었고 이 계획이 600초로 정정했다.

    60초는 갱신 실패 시 지수 백오프 재시도의 여유가 없다 — 한 번 실패하면
    토큰이 만료되고 자동매매가 정지한다. 공식 클라이언트의 기본값도 600초다.
    """
    assert load_config("mock").token_refresh_buffer_sec == 600


def test_the_rate_limit_is_marked_provisional(tmp_path):
    """TR 별 호출 제한 수치는 확정되지 않았다(명세·공식 클라이언트 어디에도
    없다). 값을 두되 그것이 잠정임을 파일이 말해야 하고, 보수적이어야 한다."""
    cfg = load_config("mock")
    assert 0 < cfg.rate_limit_per_sec <= 5


def test_the_config_is_frozen():
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        load_config("mock").rest_base = "https://evil.example"   # type: ignore[misc]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/kiwoom/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: ...kiwoom.config`

- [ ] **Step 3: endpoints.toml 을 만든다**

`src/autotrading7s/adapters/kiwoom/endpoints.toml`:

```toml
# 키움 REST API 엔드포인트 — 설계서 8.3절.
#
# 값의 출처는 docs/superpowers/records/2026-09-02-kiwoom-api-confirmed.md 이며
# 그 근거는 키움증권 공식 저장소의 kiwoom/_data/kiwoom_api_spec.json 이다.
# **이 파일에 없는 값을 코드에 박지 마라.** 사양 변경이 "코드 수정 + 재테스트"
# 가 아니라 "설정값 교체" 로 끝나야 한다(설계서 7절 1항).
#
# 키움은 엔드포인트가 아니라 `api-id` 헤더로 TR 을 가른다 — 매수·매도·취소가
# 모두 /api/dostk/ordr 이다. 그래서 [path] 와 [tr] 이 분리되어 있다.

[env.mock]
rest_base = "https://mockapi.kiwoom.com"
ws_base   = "wss://mockapi.kiwoom.com"
ws_path   = "/api/dostk/websocket"

[env.real]
rest_base = "https://api.kiwoom.com"
ws_base   = "wss://api.kiwoom.com"
ws_path   = "/api/dostk/websocket"

[path]
token_issue  = "/oauth2/token"
token_revoke = "/oauth2/revoke"
order        = "/api/dostk/ordr"
account      = "/api/dostk/acnt"
stkinfo      = "/api/dostk/stkinfo"

[tr]
token_issue     = "au10001"
token_revoke    = "au10002"
order_buy       = "kt10000"
order_sell      = "kt10001"
order_cancel    = "kt10003"
order_unfilled  = "ka10075"
order_filled    = "ka10076"
balance         = "kt00018"
deposit         = "kt00001"
price_current   = "ka10001"
realtime_quote  = "0B"
realtime_order  = "00"

# 신용주문(kt10006~kt10009)과 정정주문(kt10002)은 **의도적으로 없다.**
# 신용·미수는 설계서 6절이 타입 차원에서 배제했고, 정정은 이 프로그램의 주문
# 수명주기에 없다 — 미체결은 취소하고 다음 틱에 재판정한다(9절 ⑥).

[market]
# KRX 고정. SOR 은 NXT 까지 라우팅해 체결이 유리할 수 있으나 호가 단위와
# 대사의 거래소 구분이 흔들린다. 우리 호가 로직이 KRX 전제다.
exchange = "KRX"

[limits]
# 만료 600초 전 선제 갱신. 설계서 8.3절은 60초로 적었으나 그것은 갱신 실패
# 시 재시도 여유가 없다. 공식 클라이언트 기본값도 600초다.
token_refresh_buffer_sec = 600

# **잠정값.** TR 별 호출 제한 수치는 키움 명세와 공식 클라이언트 어디에도
# 없다(공식 클라이언트에는 레이트리미터 자체가 없다). 확정 전까지 보수적으로
# 둔다 — 넉넉하게 잡아 제한에 걸리면 주문이 거부되고, 그 거부는 트리거를
# 놓치는 것과 같다.
rate_limit_per_sec = 3

request_timeout_sec = 5

# 연속조회 페이지 상한. 0 은 무제한이 아니라 오류다 — 무제한은 응답이
# 이상할 때 무한 루프가 된다.
max_pages = 50
```

- [ ] **Step 4: 로더를 만든다**

`src/autotrading7s/adapters/kiwoom/config.py`:

```python
"""endpoints.toml 로더 — 설계서 8.3절.

**플레이스홀더와 빈 값을 거부하는 것이 이 모듈의 존재 이유다.** 설계서가 남긴
`<공식 문서로 확정>` 같은 값이 하나라도 살아 있으면 기동 자체가 실패해야 한다.
그 값으로 요청을 보내면 키움은 4xx 를 주고, 그 메시지는 "설정이 비어 있다" 가
아니라 "잘못된 요청" 이다 — 사용자가 원인을 찾을 수 없다.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

DEFAULT_PATH = Path(__file__).with_name("endpoints.toml")

# 설계서가 남긴 미확정 표기. 이 중 하나라도 값에 들어 있으면 거부한다.
_PLACEHOLDERS = ("확정", "TODO", "TBD", "<", ">")


class ConfigError(Exception):
    """설정 파일이 쓸 수 없는 상태다."""


@dataclass(frozen=True, slots=True)
class KiwoomConfig:
    env: str
    rest_base: str
    ws_base: str
    ws_path: str
    path: Mapping[str, str]
    tr: Mapping[str, str]
    exchange: str
    token_refresh_buffer_sec: int
    rate_limit_per_sec: int
    request_timeout_sec: int
    max_pages: int

    def url(self, path_key: str) -> str:
        """REST 전체 URL. 경로 키가 없으면 조용히 넘어가지 않는다."""
        try:
            return f"{self.rest_base}{self.path[path_key]}"
        except KeyError:
            raise ConfigError(f"unknown path key: {path_key!r}") from None

    def api_id(self, tr_key: str) -> str:
        try:
            return self.tr[tr_key]
        except KeyError:
            raise ConfigError(f"unknown tr key: {tr_key!r}") from None

    @property
    def ws_url(self) -> str:
        return f"{self.ws_base}{self.ws_path}"


def _checked(value: object, where: str) -> str:
    text = str(value)
    if not text.strip():
        raise ConfigError(f"{where} 가 비어 있습니다")
    if any(token in text for token in _PLACEHOLDERS):
        raise ConfigError(
            f"{where} 에 미확정 플레이스홀더가 남아 있습니다: {text!r} — "
            "docs/superpowers/records/2026-09-02-kiwoom-api-confirmed.md 참조"
        )
    return text


def _positive(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} 는 정수여야 합니다: {value!r}")
    if value <= 0:
        raise ConfigError(f"{where} 는 양수여야 합니다: {value}")
    return value


def load_config(env: str, path: Path | None = None) -> KiwoomConfig:
    resolved = path or DEFAULT_PATH
    with resolved.open("rb") as fp:
        data = tomllib.load(fp)

    envs = data.get("env", {})
    if env not in envs:
        raise ConfigError(f"unknown env {env!r}; known: {sorted(envs)}")
    section = envs[env]

    limits = data.get("limits", {})
    return KiwoomConfig(
        env=env,
        rest_base=_checked(section.get("rest_base"), f"env.{env}.rest_base"),
        ws_base=_checked(section.get("ws_base"), f"env.{env}.ws_base"),
        ws_path=_checked(section.get("ws_path"), f"env.{env}.ws_path"),
        path=MappingProxyType({
            k: _checked(v, f"path.{k}") for k, v in data.get("path", {}).items()
        }),
        tr=MappingProxyType({
            k: _checked(v, f"tr.{k}") for k, v in data.get("tr", {}).items()
        }),
        exchange=_checked(data.get("market", {}).get("exchange"),
                          "market.exchange"),
        token_refresh_buffer_sec=_positive(
            limits.get("token_refresh_buffer_sec"),
            "limits.token_refresh_buffer_sec"),
        rate_limit_per_sec=_positive(limits.get("rate_limit_per_sec"),
                                     "limits.rate_limit_per_sec"),
        request_timeout_sec=_positive(limits.get("request_timeout_sec"),
                                      "limits.request_timeout_sec"),
        max_pages=_positive(limits.get("max_pages"), "limits.max_pages"),
    )
```

`_PLACEHOLDERS` 에 `<` 와 `>` 가 있는데 `ws_base` 는 `wss://...` 여서 걸리지 않는다. URL 에 `<` 가 들어갈 일은 없다.

- [ ] **Step 5: `__init__.py` 를 만든다**

`src/autotrading7s/adapters/kiwoom/__init__.py`:

```python
"""키움증권 REST/WebSocket 어댑터 — 설계서 8.3절.

이 패키지 밖에서 `httpx`·`websockets` 를 import 하지 않는다. 사양 변경의
영향이 이 디렉터리 안에서 멈추는 것이 설계서 18.1 리스크 1 의 대응이다.
"""
```

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/kiwoom/test_config.py -q`
Expected: PASS (11 passed)

`tests/adapters/kiwoom/__init__.py` 는 만들지 않는다 — 이 저장소의 다른 테스트 디렉터리도 없다.

- [ ] **Step 7: 커밋**

```bash
git add src/autotrading7s/adapters/kiwoom/__init__.py \
        src/autotrading7s/adapters/kiwoom/endpoints.toml \
        src/autotrading7s/adapters/kiwoom/config.py \
        tests/adapters/kiwoom/test_config.py
git commit -m "feat: endpoints.toml 확정값과 플레이스홀더를 거부하는 로더"
```

`endpoints.toml` 이 패키지 데이터로 함께 설치되도록 `pyproject.toml` 에 다음을 더한다(같은 커밋).

```toml
[tool.setuptools.package-data]
"autotrading7s.adapters.kiwoom" = ["*.toml"]
```

---

## Task 3: 숫자·종목코드 정규화와 코드 상수

**Files:**
- Create: `src/autotrading7s/adapters/kiwoom/numbers.py`
- Create: `src/autotrading7s/adapters/kiwoom/codes.py`
- Test: `tests/adapters/kiwoom/test_numbers.py`, `tests/adapters/kiwoom/test_codes.py`

**Interfaces:**
- Produces: `numbers.won(value) -> int`, `numbers.qty(value) -> int`, `numbers.percent(value) -> Decimal`, `numbers.stock_code(value) -> str`, `numbers.kst_time(hhmmss, *, on: date) -> datetime`, `MappingError(Exception)`. `codes.TRDE_TP_LIMIT="0"`, `TRDE_TP_MARKET="3"`, `SIDE_SELL="1"`, `SIDE_BUY="2"`, `ORD_STT_*`, `FID_*`.
- Consumes: `KiwoomConfig` 없음 (순수 변환).

**이 태스크가 모든 매핑의 전제다.** 부호 0-padding 파싱과 종목코드 접두어가 틀리면 나머지 전부가 조용히 틀린다. 확정 기록의 "결정적 사실 2" 가 근거다.

- [ ] **Step 1: 테스트를 먼저 쓴다**

`tests/adapters/kiwoom/test_numbers.py`:

```python
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from autotrading7s.adapters.kiwoom.numbers import (
    MappingError,
    kst_time,
    percent,
    qty,
    stock_code,
    won,
)

KST = timezone(timedelta(hours=9))


@pytest.mark.parametrize("raw,expected", [
    ("+000000000012345", 12345),
    ("-000000000012345", -12345),
    ("000000000012345", 12345),
    ("12345", 12345),
    ("+60700", 60700),
    ("0", 0),
    ("000000000000000", 0),
    ("-0", 0),
])
def test_won_parses_signed_zero_padded_strings(raw, expected):
    """kt00018 의 금액은 "좌측 0-padding 처리된 부호 포함 15자리" 다.

    부호와 0-padding 을 함께 처리하지 못하면 평가손익의 음수가 양수로
    읽히고, 손실이 이익으로 보고된다.
    """
    assert won(raw) == expected


def test_won_returns_an_int_not_a_float():
    """금액 계산에 float 를 쓰지 않는다 — 원 단위 int 다."""
    assert isinstance(won("+000000000012345"), int)


@pytest.mark.parametrize("raw", ["", "   ", "abc", "1.5", "+", "-", None, "1,234"])
def test_won_rejects_anything_it_cannot_parse(raw):
    """조용히 0 을 반환하지 않는 이유: 0 은 "돈이 없다" 라는 뜻이고, 그것이
    파싱 실패의 결과로 나오면 총한도 계산과 대사가 근거를 잃는다."""
    with pytest.raises(MappingError):
        won(raw)


def test_won_accepts_an_int_unchanged():
    assert won(12345) == 12345


@pytest.mark.parametrize("raw,expected", [
    ("+000000000000205", 205),
    ("205", 205),
    ("0", 0),
])
def test_qty_parses_the_same_way(raw, expected):
    assert qty(raw) == expected


def test_qty_rejects_a_negative_quantity():
    """수량이 음수로 오는 것은 우리가 필드를 잘못 짚었다는 뜻이다.
    그대로 흘리면 매도 수량이 음수인 주문을 만든다."""
    with pytest.raises(MappingError, match="negative"):
        qty("-000000000000205")


@pytest.mark.parametrize("raw,expected", [
    ("+1.69", Decimal("0.0169")),
    ("-1.25", Decimal("-0.0125")),
    ("0.00", Decimal("0")),
    ("+12.40", Decimal("0.124")),
    ("100.00", Decimal("1")),
])
def test_percent_becomes_a_ratio_decimal(raw, expected):
    """비율만 Decimal 이고, 도메인은 0.05 형태의 비율을 쓴다.

    백분율을 그대로 흘리면 5% 가 500% 로 해석되어 목표가가 6배가 된다.
    """
    assert percent(raw) == expected


def test_percent_is_exact_not_binary_floating():
    """Decimal 로 만드는 이유 — float 경유는 0.0169 를 정확히 담지 못한다."""
    assert percent("+1.69") == Decimal("0.0169")
    assert str(percent("+1.69")) == "0.0169"


@pytest.mark.parametrize("raw,expected", [
    ("A005930", "005930"),
    ("J580011", "580011"),
    ("Q500001", "500001"),
    ("005930", "005930"),
    ("  A005930  ", "005930"),
])
def test_stock_code_strips_the_kt00018_prefix(raw, expected):
    """확정 기록 "결정적 사실 2" — kt00018 의 stk_cd 는 "접두어 1자리 +
    종목코드 6자리, 접두어(A: 주식 / J: ELW / Q: ETN)" 다.

    벗기지 않으면 broker_qty 가 항상 None 을 반환하고, 대사가 영구히
    INTERNAL_MORE 를 보고해 **모든 사이클이 자동 정지한다**(D13).
    """
    assert stock_code(raw) == expected


@pytest.mark.parametrize("raw", ["", "A", "12345", "1234567", "A00593", "X005930"])
def test_stock_code_rejects_what_it_cannot_normalize(raw):
    """알 수 없는 접두어를 조용히 벗기면 안 된다 — 그 종목이 무엇인지
    모르는 채로 수량을 대사에 넣게 된다."""
    with pytest.raises(MappingError):
        stock_code(raw)


def test_stock_code_keeps_a_six_digit_code_that_starts_with_a_letter_only_when_prefixed():
    """`A005930` 은 7자리이므로 접두어가 있는 것이 확실하다. 6자리인데
    글자로 시작하는 코드는 우리 시장에 없으므로 거부한다."""
    with pytest.raises(MappingError):
        stock_code("A00593")


def test_kst_time_attaches_kst_and_returns_utc():
    """0B 의 체결시간(FID 20)과 00 의 주문시간(FID 908)은 HHmmss 뿐이다.
    날짜를 붙이고 KST 로 해석해 UTC 로 바꿔야 한다 — naive 로 두면 9시간
    어긋난 시각이 도메인에 들어가고 재매수 쿨다운이 무너진다."""
    got = kst_time("094022", on=date(2026, 9, 2))
    assert got == datetime(2026, 9, 2, 9, 40, 22, tzinfo=KST)
    assert got.tzinfo is not None
    assert got.astimezone(UTC).hour == 0        # 09:40 KST == 00:40 UTC


@pytest.mark.parametrize("raw", ["", "9402", "abcdef", "254022", "096022"])
def test_kst_time_rejects_a_malformed_time(raw):
    with pytest.raises(MappingError):
        kst_time(raw, on=date(2026, 9, 2))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/kiwoom/test_numbers.py -q`
Expected: FAIL — `ModuleNotFoundError: ...kiwoom.numbers`

- [ ] **Step 3: numbers.py 를 만든다**

```python
"""키움 응답의 숫자·코드 정규화.

**모든 매핑의 전제다.** 여기가 틀리면 나머지 전부가 조용히 틀린다.

키움의 금액·수량은 "좌측 0-padding 처리된 부호 포함 15자리" 문자열이고
(`-000000000012345`), 비율은 `%` 로 포맷된 문자열이고(`+1.69`), 잔고의
종목코드에는 접두어가 붙는다(`A005930`). 시각은 `HHmmss` 뿐이며 KST 다.

조용히 기본값을 반환하지 않는 것이 이 모듈의 규칙이다. 파싱 실패의 결과로
나온 `0` 은 "돈이 없다"·"주식이 없다" 로 읽히고, 그것이 총한도 계산과 대사의
근거가 된다.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

KST = timezone(timedelta(hours=9), "KST")

_SIGNED_INT = re.compile(r"^[+-]?\d+$")
_HHMMSS = re.compile(r"^([01]\d|2[0-3])([0-5]\d)([0-5]\d)$")
_SIX_DIGITS = re.compile(r"^\d{6}$")
_PREFIXES = frozenset("AJQ")     # A:주식, J:ELW, Q:ETN


class MappingError(Exception):
    """키움 응답의 값을 도메인 값으로 바꿀 수 없다.

    `ValueError` 를 상속하지 않는다. `CorruptRowError` 가 `ValueError` 의
    하위이고 엔진에 `except ValueError` 를 두지 않는다는 규칙이 있으므로,
    매핑 실패가 `ValueError` 이면 어느 층에서 무엇을 잡는지가 흐려진다.
    """


def won(value: object) -> int:
    """원 단위 금액. 부호와 0-padding 을 함께 처리한다."""
    if isinstance(value, bool):
        raise MappingError(f"not a number: {value!r}")
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not _SIGNED_INT.match(value.strip()):
        raise MappingError(f"not a signed integer: {value!r}")
    return int(value.strip())


def qty(value: object) -> int:
    """수량. 음수는 우리가 필드를 잘못 짚었다는 뜻이므로 거부한다."""
    parsed = won(value)
    if parsed < 0:
        raise MappingError(f"negative quantity: {value!r}")
    return parsed


def percent(value: object) -> Decimal:
    """`%` 로 포맷된 문자열을 **비율** Decimal 로 바꾼다 (`+1.69` → `0.0169`).

    백분율을 그대로 흘리면 5% 가 500% 로 해석되어 목표가가 6배가 된다.
    """
    if isinstance(value, bool):
        raise MappingError(f"not a percent: {value!r}")
    text = value if isinstance(value, str) else str(value)
    try:
        return Decimal(text.strip()) / Decimal(100)
    except (InvalidOperation, ArithmeticError):
        # InvalidOperation 은 ArithmeticError 의 하위이고 ValueError 가 아니다.
        # `except ValueError` 로 잡으려던 시도가 Plan 4 에서 이미 한 번 틀렸다.
        raise MappingError(f"not a percent: {value!r}") from None


def stock_code(value: object) -> str:
    """`A005930` → `005930`. 확정 기록 "결정적 사실 2".

    알 수 없는 접두어를 조용히 벗기지 않는다 — 그 종목이 무엇인지 모르는
    채로 수량을 대사에 넣게 된다.
    """
    if not isinstance(value, str):
        raise MappingError(f"not a stock code: {value!r}")
    text = value.strip()
    if _SIX_DIGITS.match(text):
        return text
    if len(text) == 7 and text[0] in _PREFIXES and _SIX_DIGITS.match(text[1:]):
        return text[1:]
    raise MappingError(f"not a stock code: {value!r}")


def kst_time(hhmmss: object, *, on: date) -> datetime:
    """`HHmmss` 에 날짜를 붙여 KST tz-aware datetime 으로 만든다.

    naive 로 두면 9시간 어긋난 시각이 도메인에 들어가고 재매수 쿨다운과
    미체결 타임아웃이 무너진다.
    """
    if not isinstance(hhmmss, str):
        raise MappingError(f"not a time: {hhmmss!r}")
    match = _HHMMSS.match(hhmmss.strip())
    if match is None:
        raise MappingError(f"not HHmmss: {hhmmss!r}")
    hour, minute, second = (int(g) for g in match.groups())
    return datetime(on.year, on.month, on.day, hour, minute, second, tzinfo=KST)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/kiwoom/test_numbers.py -q`
Expected: PASS (전부 통과)

- [ ] **Step 5: codes.py 의 테스트를 쓴다**

`tests/adapters/kiwoom/test_codes.py`:

```python
from __future__ import annotations

from autotrading7s.adapters.kiwoom import codes


def test_the_order_type_codes_match_the_confirmed_spec():
    """확정 기록 — trde_tp: 0=보통(지정가), 3=시장가.

    바꿔 쓰면 지정가 주문이 시장가로 나간다. 이 프로그램에서 시장가는
    긴급청산 전용이므로(설계서 8.2절) 그것은 원칙의 위반이 조용히
    실행되는 것이다.
    """
    assert codes.TRDE_TP_LIMIT == "0"
    assert codes.TRDE_TP_MARKET == "3"


def test_the_side_codes_match_the_confirmed_spec():
    """FID 907 매도수구분 — 1:매도, 2:매수. 바꿔 읽으면 체결 통보의
    매수·매도가 뒤집히고 보유수량이 반대로 계산된다."""
    assert codes.SIDE_SELL == "1"
    assert codes.SIDE_BUY == "2"


def test_the_order_states_cover_every_documented_value():
    """FID 913 주문상태 — 접수, 체결, 확인, 취소, 거부.

    집합으로 단정하므로 키움이 값을 추가했을 때(또는 우리가 하나를 빠뜨렸을
    때) 눈에 띈다. 모르는 상태를 조용히 무시하면 그 주문은 영원히 PENDING
    이고, 규칙 5 가 판정에서 제외하므로 그 자본이 잠긴다.
    """
    assert codes.ORDER_STATES == frozenset(
        {"접수", "체결", "확인", "취소", "거부"})


def test_the_fids_we_read_are_named():
    """FID 를 숫자 리터럴로 코드에 흩어 놓으면 `values["902"]` 가 무엇인지
    읽는 사람이 알 수 없고, 잘못 짚었을 때 아무도 알아채지 못한다."""
    assert codes.FID_ORDER_NO == "9203"
    assert codes.FID_STOCK_CODE == "9001"
    assert codes.FID_ORDER_STATE == "913"
    assert codes.FID_ORDER_QTY == "900"
    assert codes.FID_ORDER_PRICE == "901"
    assert codes.FID_UNFILLED_QTY == "902"
    assert codes.FID_FILLED_AMOUNT == "903"
    assert codes.FID_SIDE == "907"
    assert codes.FID_ORDER_TIME == "908"
    assert codes.FID_REJECT_REASON == "919"
    assert codes.FID_QUOTE_TIME == "20"
    assert codes.FID_CURRENT_PRICE == "10"


def test_no_credit_order_code_is_defined():
    """설계서 6절 — 신용·미수는 타입 차원에서 배제했다."""
    names = [n for n in dir(codes) if not n.startswith("_")]
    assert not any("CRD" in n or "CREDIT" in n for n in names)
```

- [ ] **Step 6: 실패를 확인하고 codes.py 를 만든다**

```python
"""키움 코드·FID 상수 — 확정 기록의 값 그대로.

FID 를 숫자 리터럴로 코드에 흩어 놓으면 `values["902"]` 가 무엇인지 읽는
사람이 알 수 없고, 잘못 짚었을 때 아무도 알아채지 못한다. 이 프로젝트에서
그 부류의 결함은 "조용히 틀린 숫자" 로 나타난다.
"""

from __future__ import annotations

# ── 주문 요청 ────────────────────────────────────────────────────────────
# trde_tp 매매구분. 0:보통(지정가), 3:시장가.
# 그 외(5:조건부지정가, 6:최유리, 7:최우선, 10/13/16:IOC, 20/23/26:FOK,
# 61/62/81:시간외)는 이 프로그램이 쓰지 않는다 — 설계서 2절의 주문 방식은
# "실시간 감시 + 지정가 즉시 발주" 이고 시장가는 긴급청산 전용이다.
TRDE_TP_LIMIT = "0"
TRDE_TP_MARKET = "3"

# ── 실시간 주문체결(00) ──────────────────────────────────────────────────
FID_ORDER_NO = "9203"          # 주문번호
FID_STOCK_CODE = "9001"        # 종목코드
FID_ORDER_STATE = "913"        # 주문상태 — 접수/체결/확인/취소/거부
FID_ORDER_QTY = "900"          # 주문수량
FID_ORDER_PRICE = "901"        # 주문가격
FID_UNFILLED_QTY = "902"       # 미체결수량
FID_FILLED_AMOUNT = "903"      # 체결누계금액
FID_SIDE = "907"               # 매도수구분 — 1:매도, 2:매수
FID_ORDER_TIME = "908"         # 주문/체결시간 HHmmss
FID_REJECT_REASON = "919"      # 거부사유

SIDE_SELL = "1"
SIDE_BUY = "2"

ORD_STT_ACCEPTED = "접수"
ORD_STT_FILLED = "체결"
ORD_STT_CONFIRMED = "확인"
ORD_STT_CANCELED = "취소"
ORD_STT_REJECTED = "거부"

# 집합으로 두는 이유: 모르는 상태를 조용히 무시하면 그 주문은 영원히
# PENDING 이고, 규칙 5 가 판정에서 제외하므로 그 자본이 잠긴다.
ORDER_STATES = frozenset({
    ORD_STT_ACCEPTED, ORD_STT_FILLED, ORD_STT_CONFIRMED,
    ORD_STT_CANCELED, ORD_STT_REJECTED,
})

# ── 실시간 주식체결(0B) ──────────────────────────────────────────────────
FID_QUOTE_TIME = "20"          # 체결시간 HHmmss
FID_CURRENT_PRICE = "10"       # 현재가 — 부호가 포함된 숫자

# ── WebSocket 봉투 ──────────────────────────────────────────────────────
TRNM_REGISTER = "REG"
TRNM_REMOVE = "REMOVE"
TRNM_REAL = "REAL"
```

- [ ] **Step 7: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/adapters/kiwoom/test_numbers.py tests/adapters/kiwoom/test_codes.py -q`
Expected: PASS

- [ ] **Step 8: 커밋**

```bash
git add src/autotrading7s/adapters/kiwoom/numbers.py \
        src/autotrading7s/adapters/kiwoom/codes.py \
        tests/adapters/kiwoom/test_numbers.py \
        tests/adapters/kiwoom/test_codes.py
git commit -m "feat: 키움 숫자·종목코드 정규화와 코드·FID 상수"
```
