# 키움 REST API 확정 사실 — 설계서 18.2절 확정 기록

설계서 8.3절의 `endpoints.toml` 은 전부 `<공식 문서로 확정>` 플레이스홀더였고,
18.2절이 그 값을 "구현 1~2단계에서 openapi.kiwoom.com 문서로 확정하고 기록한다"
고 규정했다. 이 문서가 그 기록이다.

## 근거

`openapi.kiwoom.com` 은 JavaScript 로만 렌더링되어 문서 본문을 가져올 수 없다.
대신 **키움증권 공식 GitHub 저장소**에서 기계가 읽을 수 있는 명세를 얻었다.

- 저장소: `https://github.com/Kiwoom-Securities/Kiwoom-REST-API`
- 명세 파일: `kiwoom/_data/kiwoom_api_spec.json` (3.85MB, **337개 API**)
  — API 별로 `meta`(API ID·Method·운영/모의 도메인·URL), `request`(header·body),
  `response`(header·body), `request_example`, `response_example` 를 담는다.
- 이 파일을 `kiwoom/specs.py` 가 API ID 로 색인해 공식 클라이언트가 그대로 쓴다.
  즉 **공식 클라이언트의 동작 근거와 같은 원본**이다.

값은 전부 이 파일에서 직접 읽었다. 블로그·검색 요약은 근거로 쓰지 않았다.

## 도메인

| 환경 | REST | WebSocket |
|---|---|---|
| 실전 | `https://api.kiwoom.com` | `wss://api.kiwoom.com` |
| 모의투자 | `https://mockapi.kiwoom.com` | `wss://mockapi.kiwoom.com` |

WebSocket 경로는 `/api/dostk/websocket` 이다.

## 공통 헤더

모든 TR 이 같은 헤더 네 개를 쓴다. **엔드포인트가 아니라 `api-id` 헤더가 TR 을
가른다** — 예컨대 매수·매도·정정·취소가 모두 `POST /api/dostk/ordr` 이고
`api-id` 만 다르다.

| 헤더 | 필수 | 내용 |
|---|---|---|
| `api-id` | Y | TR 코드. 예: `kt10000` |
| `authorization` | Y | `Bearer <token>` — 토큰타입 접두어를 **붙여서** 보낸다 |
| `cont-yn` | N | 응답 헤더의 `cont-yn` 이 `Y` 면 다음 요청에 그 값을 넣는다 |
| `next-key` | N | 같은 방식으로 응답의 `next-key` 를 넣는다 |

`Content-Type: application/json;charset=UTF-8`.

응답 본문에는 명세의 `response.body` 에 없는 봉투 필드 `return_code`(0=정상)와
`return_msg` 가 함께 온다(`response_example` 로 확인). **`BrokerRejected(code,
message)` 의 재료가 이 둘이다.**

## 인증 — au10001 / au10002

```
POST /oauth2/token     api-id: au10001    발급
POST /oauth2/revoke    api-id: au10002    폐기
```

요청 본문: `grant_type`(=`client_credentials`), `appkey`, `secretkey`.
응답 본문: `token`, `token_type`, `expires_dt`.

**`expires_dt` 는 `YYYYMMDDHHMMSS` 형식의 KST 문자열이다** (초 단위 TTL 이
아니다). 공식 클라이언트가 `datetime.strptime(value, "%Y%m%d%H%M%S")` 후
KST → UTC 로 변환한다. 우리 도메인의 모든 `datetime` 이 tz-aware 이므로
어댑터 경계에서 반드시 KST 를 붙여 UTC 로 바꿔야 한다 — naive 로 두면
9시간 어긋난 만료시각이 조용히 들어간다.

공식 클라이언트의 선제 갱신 여유는 **600초**다(`refresh_buffer_seconds`
기본값). 설계서 8.3절은 60초로 적었다. 600초를 쓴다 — 60초는 갱신 실패 시
재시도 여유가 없다.

## 우리가 쓰는 TR

| 용도 | api-id | Method · URL |
|---|---|---|
| 매수주문 | `kt10000` | POST `/api/dostk/ordr` |
| 매도주문 | `kt10001` | POST `/api/dostk/ordr` |
| 취소주문 | `kt10003` | POST `/api/dostk/ordr` |
| 미체결 조회 | `ka10075` | POST `/api/dostk/acnt` |
| 체결 조회 | `ka10076` | POST `/api/dostk/acnt` |
| 계좌평가잔고 | `kt00018` | POST `/api/dostk/acnt` |
| 예수금 상세 | `kt00001` | POST `/api/dostk/acnt` |
| 주식기본정보(현재가) | `ka10001` | POST `/api/dostk/stkinfo` |
| 실시간 주식체결(시세) | `0B` | WS `/api/dostk/websocket` |
| 실시간 주문체결(주문/체결 통보) | `00` | WS `/api/dostk/websocket` |

**정정주문 `kt10002` 와 신용주문 `kt10006~9` 는 쓰지 않는다.** 신용은 설계서
6절이 타입 차원에서 배제한 것이고, 정정은 이 프로그램의 주문 수명주기에
없다(미체결은 취소하고 다음 틱에 재판정한다 — 설계서 9절 ⑥).

## 주문 요청·응답

```
kt10000 / kt10001  요청 body
  dmst_stex_tp   국내거래소구분  필수  KRX, NXT, SOR
  stk_cd         종목코드       필수
  ord_qty        주문수량       필수  단위 1주
  ord_uv         주문단가       선택  단위 원
  trde_tp        매매구분       필수  0:보통(지정가), 3:시장가, 5:조건부지정가,
                                    6:최유리, 7:최우선, 10:보통(IOC), 13:시장가(IOC),
                                    20:보통(FOK), 23:시장가(FOK), 61/62/81:시간외
  cond_uv        조건단가       선택

  응답 body:  ord_no(주문번호), dmst_stex_tp   (+ return_code, return_msg)

kt10003  요청 body
  dmst_stex_tp, orig_ord_no(원주문번호), stk_cd, cncl_qty(취소수량)
  응답 body:  ord_no, base_orig_ord_no, cncl_qty
```

따라서 `LimitOrderRequest` → `trde_tp="0"` + `ord_uv=price`,
`MarketSellRequest` → `kt10001` + `trde_tp="3"` + `ord_uv=""` 로 사상된다.
**시장가에 단가를 넣지 않는다는 것이 타입 분리(설계서 8.2절)와 정확히
맞물린다.**

## 결정적 사실 1 — 클라이언트 참조값이 없다

**키움 API 는 클라이언트가 정한 참조값을 받지도, 되돌려주지도 않는다.**

337개 API 의 모든 요청·응답 필드를 `사용자|참조|ref|uuid|client|cust|고객|메모|
비고|tag|외부` 로 훑었고, 나온 23건은 전부 WebSocket 의 `refresh`
(기존등록유지여부)로 무관했다. 주문 4종(`kt10000/1/2/3`)의 요청 필드에도
그런 자리가 없다.

이것이 Plan 2B 핸드오버 4번이 경고한 경우다 — 설계서 9절 ⑤의 UNKNOWN 분기는
`client_ref` 대조로만 접수 여부를 확인하도록 쓰여 있고, **그 대조가 불가능하다.**
대응은 아래 "확정 사실 3" 이 답한다.

## 결정적 사실 2 — 잔고의 종목코드에 접두어가 붙는다

`kt00018` 응답의 `stk_cd` 는 **"접두어 1자리 + 종목코드 6자리, 접두어(A: 주식 /
J: ELW / Q: ETN)"** 다. 즉 삼성전자는 `A005930` 으로 온다.

우리 도메인은 `005930` 을 쓴다. 이 접두어를 벗기지 않으면 `broker_qty` 가 항상
`None` 을 반환하고, **대사가 영구히 `INTERNAL_MORE` 를 보고해 모든 사이클이
자동 정지한다**(D13). 어댑터의 매핑이 반드시 벗겨야 하며, 그 사실을 테스트로
고정해야 한다.

또한 `kt00018` 의 금액·수량은 **"좌측 0-padding 처리된 부호 포함 15자리"**
문자열이다(예: `-000000000012345`). 수익률은 `%` 로 포맷된 문자열이다.
금액·가격은 원 단위 `int` 로, 비율만 `Decimal` 로 바꾸는 것이 우리 규칙이므로
매핑에서 부호와 0-padding 을 함께 처리해야 한다.

주요 필드: `stk_cd`, `stk_nm`, `rmnd_qty`(보유수량), `trde_able_qty`(매매가능수량),
`pur_pric`(매입가), `cur_prc`(현재가), `evltv_prft`(평가손익), `tot_evlt_amt` 등.

**미확정**: `kt00018` 이 보유수량 0 인 종목을 응답에 남기는지 아니면 생략하는지
명세에 없다. 핸드오버 3번이 지목한 지점이며 계좌 접근 후 확인해야 한다.

## 결정적 사실 3 — WebSocket `00` 주문체결이 `client_ref` 를 대신한다

`00`(주문체결)은 **모든 주문 사건을 실시간으로 밀어준다.** 등록은
`{"trnm":"REG","grp_no":"1","refresh":"1","data":[{"item":"","type":"00"}]}`
이고 수신은 `trnm:"REAL"` 로 온다. 값은 FID 번호를 키로 갖는다.

| FID | 이름 | 비고 |
|---|---|---|
| `9203` | 주문번호 | 7자리 |
| `9001` | 종목코드 | |
| `913` | 주문상태 | **접수 / 체결 / 확인 / 취소 / 거부** |
| `900` | 주문수량 | |
| `901` | 주문가격 | |
| `902` | 미체결수량 | |
| `903` | 체결누계금액 | |
| `905` | 주문구분 | 매도/매수/매도취소/… |
| `907` | 매도수구분 | 1:매도, 2:매수 |
| `908` | 주문/체결시간 | HHmmss |
| `909`·`910`·`911` | 체결번호·체결가·체결량 | |
| `919` | 거부사유 | |
| `2134`/`2135` | 거래소구분 | 0:통합, 1:KRX, 2:NXT |

**`913='접수'` 가 접수 사실을, `9203` 이 주문번호를 알려준다.** REST 응답이
유실돼도 이 푸시로 접수 여부를 알 수 있으므로, 우리가 설계했던 `client_ref`
대조보다 오히려 강한 수단이다(폴링이 아니라 푸시이고 주문번호까지 온다).

**누적·평균 계약(핸드오버 2번)도 이 필드들로 만족시킬 수 있다.**

```
누적체결수량 = 900(주문수량) − 902(미체결수량)
수량가중평균가 = 903(체결누계금액) ÷ 누적체결수량
```

`911`(체결량)·`910`(체결가)은 **그 체결 건의 값**이므로 그대로 흘리면 안 된다.
`ka10075`(미체결요청)도 같다 — `ord_qty − oso_qty` 와 `cntr_tot_amt` 를 쓴다.
증분을 누적인 것처럼 흘리면 취득원가가 과소 계상되어 사용자에게 보고되는
이익이 부풀려진다.

## 시세 스트림 — `0B` 주식체결

| FID | 이름 |
|---|---|
| `20` | 체결시간 (HHmmss) |
| `10` | 현재가 — **부호가 포함된 숫자** (`+60700`) |
| `11`·`12` | 전일대비·등락율 |
| `27`·`28` | 최우선 매도호가·매수호가 |
| `15` | 거래량 (+매수체결, −매도체결) |

`10` 의 부호를 벗겨 `int` 로 만들어야 한다. `20` 은 시:분:초뿐이므로 날짜를
붙여 tz-aware `datetime` 으로 만들어야 한다 — 그 기준 시간대가 KST 다.

## 연속조회

응답 헤더 `cont-yn == "Y"` 이면 `next-key` 를 다음 요청 헤더에 넣어 반복한다.
공식 클라이언트도 그 루프로 페이징한다. **잔고·미체결 조회는 반드시 끝까지
페이징해야 한다** — 중간에 끊고 "이게 전부다" 로 취급하면 대사와 긴급청산이
없는 보유를 0 으로 본다.

## 미확정으로 남는 것

| 항목 | 왜 미확정인가 | 확정 방법 |
|---|---|---|
| TR 별 호출 제한 수치 | 명세 JSON·공식 클라이언트 어디에도 없다. 공식 클라이언트에 레이트리미터 자체가 없다 | 포털 Q&A·공지 또는 실계좌 관측 |
| `kt00018` 의 0수량 종목 포함 여부 | 명세에 없다 | 계좌 접근 후 관측 |
| 정규장 시간·휴장일 판단 | `0s`(장시작시간) 실시간 항목이 있으나 규격 미확인 | `0s` 명세 확인 또는 설정 파일 |
| WebSocket 하트비트·PING 규격 | 명세에 없다 | `examples/` 의 실시간 예제 확인 |

**호출 제한을 모르는 상태로 값을 지어내지 않는다.** 레이트리미터는 두되
파라미터를 `endpoints.toml` 로 빼고 보수적인 기본값을 쓴다. 잘못된 값을
코드에 박으면 확정 후에 코드를 고쳐야 하고, 그것이 설계서 7절 1항이 막으려는
바로 그 상황이다.
