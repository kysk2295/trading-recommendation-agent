# US News-Catalyst Opportunity 체크포인트

## 범위

완전한 bounded Alpaca News evidence를 소비하는 첫 미국 Opportunity Manager baseline을 추가했다. 이 단계는 뉴스 감성, 주가 방향, 진입가, 손절가, 목표가, Trade Signal 또는 주문을 만들지 않는다. 결과는 downstream 기술적 setup 검증 순서를 정하는 shadow discovery 후보일 뿐이다.

## 사전등록 계약

`us_equities/opportunity_manager/news_catalyst` 단일 lane에 다음 가설과 immutable strategy version을 전역 experiment ledger schema v4로 사전등록한다.

- 가설: 선언된 동일 universe에서 최근 5분 provider-updated 뉴스 관측이 많은 종목을 downstream 기술 검증에서 먼저 확인한다.
- 반증: 최소 20 sessions·100 independent observations의 사전등록 shadow trial에서 동일 universe zero-news control 대비 setup-confirmation lift가 없거나 coverage·시점 인과성 결함이 발견되면 기각한다.
- 운영모드: `shadow`
- strategy version: runtime code version SHA-256 prefix와 결합
- 권한: discovery only, downstream validation required, no direction/entry/order/position sizing

projection은 ledger에서 exact strategy version 하나를 찾은 뒤 hypothesis ID, lane, shadow mode, code version, parameter·data·cost·portfolio 계약 전체를 다시 대사한다. 등록이 없거나 하나라도 다르면 evidence를 ranking하지 않는다.

## 결정론적 Ranking

complete evidence bundle의 assessment cutoff를 `observed_at`으로 사용한다.

- `provider_updated_at`이 cutoff 이전 300초 안인 observation만 recent article로 인정한다.
- receipt가 방금 도착했더라도 provider event가 오래됐으면 후보에서 제외한다.
- 정렬은 `recent article count 내림차순 → latest provider update 최신순 → symbol 오름차순`이다.
- 최대 20개 candidate만 유지한다.
- score는 `recent count + freshness fraction`이며 recent count가 최신성보다 항상 우선한다.
- feature는 recent article count, latest provider update, exact age seconds만 포함한다.
- 뉴스 0건 또는 stale news만 있는 complete symbol은 후보에서 제외한다.
- 후보가 모두 제외되면 실패나 빈 성공 snapshot으로 위장하지 않고 `no_candidates` terminal projection을 게시한다.

`OpportunitySnapshot`의 evidence는 coverage assessment와 실제 ranking에 사용된 recent article observation만 가리킨다. source coverage의 record count는 complete bundle 전체 accepted article 수를 보존한다. snapshot은 cutoff부터 5분만 유효하고 운영 CLI는 현재시각이 그 구간 밖이면 artifact 발행 전에 차단한다.

## Immutable Artifact

ranked와 `no_candidates` 결과를 모두 content-addressed mode-600 projection artifact로 게시한다. projection ID는 evidence bundle ID, ledger strategy registration key, strategy version, 시각, status, candidate count와 canonical `OpportunitySnapshot` 전체를 결박한다. 따라서 candidate score·feature·순서 또는 evidence를 같은 ID 아래 바꾸면 reader가 fail-closed한다. exact replay는 새 artifact를 만들지 않는다.

## CLI

```bash
./run_us_news_catalyst_research_register.py \
  --manifest examples/us_news_catalyst/research-registration.json \
  --database outputs/experiment_ledger/global.sqlite3 \
  --output-dir outputs/us_news/news-catalyst-registration

./run_us_news_catalyst_opportunity.py \
  --evidence outputs/us_news/alpaca-news-opportunity-evidence/alpaca_news_opportunity_evidence_<bundle-id>.json \
  --registration-manifest examples/us_news_catalyst/research-registration.json \
  --experiment-ledger outputs/experiment_ledger/global.sqlite3 \
  --output-dir outputs/us_news/news-catalyst-opportunity
```

운영 projection CLI에는 fixture, provider, credential, symbol, 점수 또는 시간 override가 없다. registration manifest와 complete immutable evidence가 입력 전체다.

## 검증

- US news-catalyst focused: `15 passed`
- full suite: `3078 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings`
- actual CLI help: registration/opportunity exit `0`
- registration/replay: exit `0/0`
- missing evidence: redacted exit `1`, projection artifact `0`
- current ranked/replay: exit `0/0`, immutable artifact `1`
- complete no-candidate: exit `2`, terminal artifact `1`
- stale evidence: exit `1`, projection artifact `0`
- artifact/report mode: `600`
- provider request, credential read, account read, order mutation: `0`

다음 경계는 이 exact baseline version의 일별 shadow trial을 등록하고, downstream setup-confirmation과 same-universe zero-news control을 point-in-time으로 비교하는 terminal outcome·독립 Reviewer 계약이다. 실제 forward 표본 없이 이 baseline을 champion, Trade Signal 또는 Paper 실행으로 승격하지 않는다.
