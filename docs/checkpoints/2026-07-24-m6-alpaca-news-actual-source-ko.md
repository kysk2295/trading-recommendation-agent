# M6 Alpaca News actual source·Opportunity evidence

- 관측 시각: 2026-07-24 13:24~13:27 KST
- runtime code version: `c36816c0d4782c6e7bce3ccd7a8df333c225986c`
- source: Alpaca Market Data `GET /v1beta1/news`
- 범위: 10개 명시 심볼, 50분, 최대 2페이지
- 권한: licensed GET-only, shadow research

## 실제 source 결과

- collection result: `success`
- raw response pages: 1
- raw response bytes: 534
- accepted article metadata: 1
- 최초 network access: GET-only 1회
- 존재하지 않는 credential 경로의 exact replay network access: 0
- receipt/run과 normalized metadata: append-only private SQLite
- database와 report mode: 600

raw receipt는 parser 전에 확정했고 headline, URL, summary, content를 downstream
evidence artifact에 복제하지 않았다. 계좌·주문·포지션 endpoint와 mutation은 0건이다.

## capability·coverage 결합

- local capability result: `complete`
- capability/entitlement 신규: 1/1
- capability/entitlement resolved: 1/1
- declared symbols: 10
- successful symbols: 10/10
- completeness: 10000 bps
- successful/failed/missing slices: 1/0/0
- Opportunity evidence snapshots: 10
- coverage/evidence 최초 artifact: 1/1
- exact replay 신규 artifact: 0/0
- 모든 artifact와 report mode: 600

## 사전등록 shadow baseline

exact code-coupled `us_equities/opportunity_manager/news_catalyst` 가설과 strategy
version을 별도 experiment ledger에 먼저 등록했다.

- hypothesis/strategy version 최초: 1/1
- registration replay 신규: 0/0
- operating mode: shadow
- 방향·진입가·손절·목표·수량·주문 권한: 없음

coverage cutoff 이전 최근 5분의 provider update만 후보로 인정하는 고정 baseline을
actual evidence에 실행했다. 관측된 기사 metadata는 이 freshness 조건을 통과하지
못했으므로 결과는 `no_candidates`, eligible symbol 0으로 닫혔다. 이는 source 실패나
0수익 trial이 아니다.

- terminal projection 최초: 1
- exact replay 신규 projection: 0
- replay provider/credential/account/order operation: 0

## 검증 해석

이 체크포인트는 Alpaca News API가 fixture가 아니라 실제 bounded raw-first source,
capability registry, point-in-time evidence와 사전등록 shadow discovery까지 연결됐음을
증명한다. 시장 전체 coverage, 실시간 WebSocket news entitlement, Trade Signal,
추천 성과, Paper champion 또는 Allocation 권한을 의미하지 않는다.
