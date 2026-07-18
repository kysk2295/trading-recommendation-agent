# Alpaca SIP Runtime Fleet 체크포인트

- 날짜: 2026-07-19
- 범위: bounded desired subscription의 종목별 read-only runtime owner
- 외부 실제 GET: 0건
- account/order endpoint: 0건
- POST/DELETE mutation: 0건

## 구현

- global `SubscriptionPolicyDecision`의 capacity 안 desired set만 받고 request instrument coverage가 정확히 같아야 실행한다.
- instrument ID와 symbol의 SHA-256을 owner key로 사용한다. owner마다 mode-700 디렉터리, mode-600 runtime DB, mode-600 raw evidence DB와 독립 canonical root를 가진다.
- owner는 global decision을 exact one-symbol decision으로 축소한 뒤 기존 `AlpacaSipRuntimeAdapter`와 `UsMarketDataSupervisor`를 그대로 실행한다.
- READY feature snapshot 하나만 `UsFeatureEvidenceBinding`으로 반환한다. gap, insufficient history, no-new-data와 provider failure는 binding을 만들지 않는다.
- 한 owner의 typed failure는 다른 owner cycle을 막지 않으며 fleet 결과는 `degraded`가 된다.
- symlink owner root, 잘못된 path mode, request coverage mismatch는 HTTP 전에 fail-closed한다.

## Fixture E2E

- 두 symbol에 각각 35개 정규장 완료 분봉을 반환하는 mock SIP GET을 실행했다.
- owner별 raw page, canonical Parquet/DuckDB identity, runtime receipt와 READY feature를 확인했다.
- READY binding 두 개는 기존 M4.4 gate에서 하나의 evidence-gated Opportunity를 만들었다.
- 한 symbol sequence gap은 해당 owner만 `blocked`, 다른 symbol은 READY였고 M4.4 gate는 `missing_evidence`로 닫혔다.
- 한 symbol 503은 해당 owner만 `failed`였고 다른 GET과 binding은 유지됐다.
- 새 fleet process는 owner별 20개 checkpoint 뒤 신규 15개 receipt만 추가했다.

## 검증

- focused Alpaca SIP adapter/gap/fleet: **17 tests**
- full repository: **2202 tests**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 신규 production module 2개 위반 0건

## 남은 경계

운영 `RuntimeFeatureRequest.expected_cumulative_volume`은 historical intraday volume curve의 point-in-time evidence와 결합되어야 한다. 현재 KIS 누적 거래량이나 당일 거래대금으로 denominator를 임의 추정하지 않는다. 이 lineage와 durable fleet cycle audit가 구현된 뒤에만 실제 정규장 SIP fleet GET과 M4.4 운영 연결을 연다.
