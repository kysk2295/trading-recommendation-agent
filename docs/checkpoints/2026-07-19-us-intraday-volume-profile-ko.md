# US Intraday Volume Profile 체크포인트

- 날짜: 2026-07-19
- 범위: M4 runtime RVOL denominator의 point-in-time historical lineage
- 외부 실제 GET: 0건
- account/order endpoint: 0건
- POST/DELETE mutation: 0건

## 구현

- 숫자형 `expected_cumulative_volume` runtime 입력을 제거했다.
- 목표 분까지 거래 가능한 직전 20개 완료 정규장을 exact calendar 날짜로 요구한다.
- 각 과거 세션은 정규장 open부터 calendar close까지 연속된 완료 1분봉이어야 한다.
- 세션별 목표 분 누적 거래량의 Decimal median을 계산한다.
- verified `ResearchInputIdentity`, instrument, 목표일·분, source 날짜·누적값, semantic version과 SHA-256을 하나의 frozen evidence로 보존한다.
- runtime request, indicator snapshot, M4.4 evidence hash가 동일 profile lineage를 공유한다.
- 현재·미래·오래된·누락·공백·미완료 세션, 변조된 median/hash와 평가일 불일치를 fail-closed한다.

## Fixture E2E

- 두 instrument마다 20개 완료 historical session을 생성했다.
- 각 profile은 35분 누적 거래량 USD가 아닌 share volume 4,000의 median을 만들었다.
- 두 profile을 기존 bounded SIP fleet에 전달해 owner별 raw-first GET receipt, canonical Parquet/DuckDB identity와 READY feature를 생성했다.
- 두 binding은 기존 M4.4 gate에서 하나의 READY Opportunity를 만들었다.
- 외부 provider, credential, account, order 또는 mutation 경로는 사용하지 않았다.

## 검증

- focused causal profile/runtime/fleet/evidence: **68 tests**
- full repository: **2214 tests**
- Ruff: 통과
- changed-file format check: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 신규 핵심 파일 5개 위반 0건

## 남은 경계

실제 Alpaca historical archive 또는 canonical catalog에서 최근 20개 적격 세션을 읽어 profile evidence를 지속 생성하는 loader는 아직 없다. 따라서 실제 정규장 fleet GET과 장기 soak는 계속 열지 않는다. 이 체크포인트는 fixture 기반 인과성·계보 검증이며 수익성이나 운영 feed 준비 완료를 의미하지 않는다.
