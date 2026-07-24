# M6 KIS 해외선물 actual quote source

- 구현 커밋: `f9f09ec5c86f50056491b86c91dc659cd203f173`
- 공식 계약 기준:
  `koreainvestment/open-trading-api@885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc`
- provider operation: 해외선물종목현재가 GET-only
- 계좌·잔고·주문·포지션 operation: 0

## 구현한 source 경계

공식 KIS 해외선물 현재가 계약만 허용한다.

- origin: `https://openapi.koreainvestment.com:9443`
- path: `/uapi/overseas-futureoption/v1/quotations/inquire-price`
- TR ID: `HHDFC55010000`
- parameter: `SRS_CD`
- method: GET
- redirect: 금지
- request: 같은 root의 고유 계약 2~8개
- transient server status: 기존 bounded 4-attempt 정책 유지

각 응답은 provider status와 구조를 해석하기 전에 mode-600 append-only SQLite에
raw bytes로 확정한다. 성공한 terminal은 계약별 provider process date/time,
business/listing/expiration/last-trade date, exchange, currency, last/bid/ask,
previous close, settlement와 누적 거래량을 canonical quote로 보존한다.

invalid bid/ask, 비공식 origin, 다른 path/TR, redirect, oversized/empty body,
request와 다른 symbol, malformed date/price는 fail-closed한다. terminal exact replay는
credential과 network보다 먼저 끝난다.

## TDD·검증

- missing module RED: collection test import failure
- focused: 4 passed
- 전체 pytest: 3641 passed
- Ruff: 통과
- basedpyright: 0 errors, 0 warnings
- CLI `--help`: exit 0
- 1계약 bad input: exit 1, DB/report 미생성
- fixture two-contract success/replay: fetch 2/0, receipt/run 2/1
- malformed bid/ask: raw receipt 1, failed terminal 1, canonical quote 0

## actual ES 결과

exact frozen runtime에서 `ESU26`, `ESZ26` 두 계약을 요청했다. 첫 계약은 server
status 재시도 뒤 다음 actual evidence로 닫혔다.

- HTTP status: 500
- provider return code: 1
- provider message code: `EGW00550`
- provider reason: CME SUB 거래소 신청 계좌가 아님
- raw receipt: 1
- terminal run: 1
- canonical quote: 0
- second contract request: 0
- report result/failure: `failed/http_status`
- database SHA-256:
  `55aa963cc16bad8d48c7128eff6644e4544db831523dbe4c74d4c82e68da94d9`
- database/report mode: 600

실패를 다른 contract나 source로 대체하지 않았고 raw provider evidence를 삭제하지 않았다.
존재하지 않는 credential 경로의 exact replay는 exit 1, replayed `yes`, network `0`,
receipt/run `1/1`을 유지했다.

## query-only entitlement admission

exact `d574cc9d19f900ff0998409a9de28bfa68c4de85`에서 저장된 terminal과 raw receipt만
읽는 admission CLI를 추가했다. 실제 실패 본문은 UTF-8이지만 첫 `rt_cd` 키가 따옴표
없이 내려온 비표준 JSON이었다. 엄격 JSON 파서 실패 뒤 제한된 ASCII `msg_cd` 필드만
읽도록 TDD로 고쳐, 새 provider 요청 없이 다음 machine-readable 결과를 확정했다.

- admission: `blocked`
- reason: `cme_sub_entitlement_missing`
- source request/run/receipt exact binding: 통과
- canonical quote: `0`
- network/credential read/broker mutation: `0/0/0`
- admission ID:
  `06df49d6764dbce427ae37c33660c7021a4d07feecd9c500970c9e9d5d92223c`
- artifact file SHA-256:
  `e0a1d2d984d3c4d9a83d4a1dcf9689939eaf0b52b27b8ea203db2c1ed3ab205c`
- artifact/report mode: `600`
- exact replay: exit `2`, artifact created `no`
- focused: `7 passed`
- full pytest: `3644 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`

## 남은 외부 조건

실제 CME quote를 만들려면 현재 KIS 계정·앱의 CME SUB 거래소 시세 신청 상태가 먼저
바뀌어야 한다. 이는 `kis.env`에 값을 더 넣는 문제가 아니며, 동일 entitlement 상태에서
같은 provider 요청을 반복하지 않는다.

따라서 현재 상태는 source adapter 구현 완료, actual entitlement blocked,
futures canonical quote 0건이다. 실제 두 계약 quote가 성공하기 전에는 basis·curve,
roll performance trial, derivatives champion 또는 주문 권한을 열지 않는다.
