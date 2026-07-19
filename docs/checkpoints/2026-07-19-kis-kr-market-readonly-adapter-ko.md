# KIS KR market read-only adapter 체크포인트

## 공식 계약

KIS 공식 `open-trading-api` commit `885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc`의 다음 세 sample을 기준으로 고정했다.

- 당일 분봉: `GET /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice`, TR `FHKST03010200`
- 현재가·가격제한·상태: `GET /uapi/domestic-stock/v1/quotations/inquire-price`, TR `FHKST01010100`
- 1호가·장운영·VI: `GET /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn`, TR `FHKST01010200`

client는 `https://openapi.koreainvestment.com:9443` exact origin과 redirect 금지만 허용한다. 미래 완료봉, 실제 clock과 2초보다 차이 나는 요청은 network 전에 차단한다. 계좌·잔고·주문 endpoint는 없다.

## Raw-first projection

- response bytes는 kind, symbol, 수신시각, HTTP status/content type과 함께 repr-hidden frozen receipt가 된다.
- 분봉은 provider date/time으로 KST 시각을 만들고 `bar_end <= received_at`인 행만 사용한다. 09:00부터 공백 없이 이어져야 하며 현재 형성 중인 첫 행은 제외한다.
- `acml_tr_pbmn`의 시간 순 차분을 각 완료봉의 실제 거래대금으로 사용한다. 누적값 역행, OHLC/평균체결가 불일치와 같은 minute의 변경 응답은 차단한다.
- 현재가와 호가는 2초 이내 receipt여야 하고 symbol, current price, base price와 VI code가 일치해야 한다. provider `aspr_acpt_hour`는 receipt보다 미래일 수 없고 최대 5초만 허용한다.
- `new_mkop_cls_code=20`만 regular continuous, `vi_cls_code=N`만 VI clear로 연다. 미등록 코드를 뜻으로 추측하지 않고 unknown으로 보존한다.
- 정지, 투자유의·경고, 단기과열·정리매매·관리종목이 명시적 정상 조합이 아니면 block 상태로 투영한다.

## 검증

- 관련 client/projection/setup/signal/gate: `34 passed`
- 전체 회귀: `2650 passed`
- Ruff, changed-file format: 통과
- basedpyright: `0 errors, 0 warnings`
- compileall, no-excuse: 통과
- fixture transport: reviewed GET `3`, unsafe/stale/future preflight block, external mutation `0`
- fixture E2E: completed bars `4`, setup `1`, signal `1`, unknown VI signal `0`

현재 시각은 `2026-07-19 19:02 KST` 일요일이므로 current regular-session 조건을 우회해 production GET을 실행하지 않았다. 실제 provider GET, credential 출력, 계좌·잔고·포지션·주문 호출과 mutation은 모두 0건이다.

## 다음 단계

다음 열린 KR 정규장에서 top-1 한 종목에 세 endpoint를 bounded read-only로 호출해 실제 code/schema와 raw receipt를 확인한다. 그 전에는 fixture가 권위 있는 transport 검증이다. 이후 private raw receipt store와 운영 CLI를 연결하고, exact day strategy version 사전등록→append-only shadow trial→보수적 KR fill 원장 순서로 진행한다.
