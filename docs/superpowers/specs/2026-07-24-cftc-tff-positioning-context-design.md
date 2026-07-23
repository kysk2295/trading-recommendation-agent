# CFTC TFF positioning context 설계

## 목적

Institutional Multi-Market Quant Research OS Milestone 6의 미완성 경계인
`CFTC COT positioning context`를 공식 CFTC Public Reporting Environment의
Traders in Financial Futures futures-only dataset으로 연결한다.

이번 vertical은 하나의 CFTC contract market code에 대한 최신 두 주간 관측을
raw-first로 보존하고 dealer, asset manager, leveraged money, other reportable과
non-reportable net positioning 및 주간 변화를 immutable market context로 만든다.
파생상품 agent가 읽을 수 있는 shadow-only evidence이며 추천·주문·allocation
권한을 갖지 않는다.

## 선택한 접근

Legacy COT는 commercial/non-commercial 두 범주만 제공한다. TFF futures-only는
금융선물의 dealer/intermediary, asset manager/institutional, leveraged funds,
other reportables와 non-reportables를 분리하므로 ES 같은 금융선물 context에 더
적합하다. 실제 계약 월별 포지션이 아니라 CFTC market-level weekly aggregate라는
의미를 그대로 유지한다.

단일 최신 행만 저장하면 변화량을 provider의 파생 필드에 의존하게 된다. 최신 두
행의 원시 position을 함께 수집하고 동일 계산기로 net과 주간 변화를 산출한다.
여러 market을 한 요청에 섞는 broad collector는 이번 bounded vertical에서 제외한다.

## Source와 요청 계약

- 고정 origin:
  `https://publicreporting.cftc.gov`
- 고정 dataset:
  `/resource/gpe5-46if.json`
- report kind: `FutOnly`
- 입력: bounded collection ID, 6자리 영숫자 contract market code, `through_date`
- 정렬: report date descending
- 결과 상한: 정확히 최신 두 행
- 인증과 credential: 없음
- redirect: 금지

SoQL select와 order는 코드에 고정한다. market code는 영숫자 계약으로 파싱한 뒤
parameter value에 넣고 `through_date`보다 미래 report를 요청하지 않는다. 응답은
1 MiB를 넘기지 않으며 HTTP status와 content type을 포함한 raw bytes를 parser보다
먼저 private append-only SQLite에 확정한다.

CFTC 설명에 따라 report date는 포지션 기준일이며 우리 시스템의 causal 관측시각이
아니다. context의 `observed_at`은 raw response의 실제 receipt 시각으로 고정하고
두 report date를 별도로 보존한다.

## 검증과 projection

응답은 다음 조건을 모두 만족해야 한다.

- JSON array가 정확히 두 행
- 두 행 모두 요청 market code와 `FutOnly`
- market·exchange 이름과 계약 단위가 동일
- report date가 고유하고 내림차순이며 `through_date` 이하
- open interest와 모든 category long/short/spread가 non-negative integer
- category long 및 short 합계가 각각 open interest와 일치

context는 category별 current net, previous net, weekly change와 current
open-interest 대비 current net bps를 보존한다. 모든 값은 원시 정수 position에서
결정적으로 계산한다. context ID는 canonical payload SHA-256이며 artifact 파일도
content-addressed mode 600이다.

## 저장·재시작·오류

SQLite는 request, raw receipt와 terminal run을 append-only로 보존한다. 같은 request
재실행은 저장된 terminal과 context를 재생하고 network를 열지 않는다. 동일 request의
다른 raw bytes, row 순서·identity·합계 불일치, partial JSON, HTTP 오류, public 또는
변조된 store는 sanitized 실패로 닫는다. 실패 raw response도 삭제하지 않는다.

CLI report는 status, report 수, latest report date, category 수, replay와 network
집계만 노출한다. 개별 포지션, raw payload, 로컬 경로와 provider query는 report에
쓰지 않는다.

## 테스트와 운영 증거

- parser happy와 identity/date/position reconciliation 실패
- raw-before-parse 실패 terminal
- HTTP origin/path/redirect/size 경계
- CLI help, invalid market code, fixture happy와 exact replay
- 공식 CFTC bounded actual GET과 network-free replay
- artifact/database/report mode 600
- provider operation GET-only, credential/account/order/allocation mutation 0

공식 [CFTC COT 설명](https://www.cftc.gov/MarketReports/CommitmentsofTraders/AbouttheCOTReports/index.htm)은
weekly report와 기준일 의미의 권위다.
[TFF futures-only API](https://dev.socrata.com/foundry/publicreporting.cftc.gov/gpe5-46if)는
dataset과 field 계약의 권위다. Actual GET은 source availability와 bounded
market-level context evidence일 뿐 전략 성과, 실시간성, 계약 월별 curve 또는
Paper 권한이 아니다.
