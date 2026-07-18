# US Broad Scanner Foundation 체크포인트

- 날짜: 2026-07-19
- 범위: KIS broad opportunity에서 M4.2 scanner input까지의 causal operational foundation
- 외부 요청: 0건
- account/order endpoint: 0건
- POST/DELETE mutation: 0건

## 구현

- `ranking_momentum` Opportunity의 producer를 `kis-risk-screen-v1`로 고정하고 AMS/NAS/NYS의 상승률·거래량 6개 coverage와 `nyse_halts`가 모두 complete인지 검증한다.
- 1일 이내의 Alpaca security-master snapshot만 결합하고 미래 snapshot, 누락 coverage, 부분 수집, 다른 producer를 fail-closed한다.
- `alpaca/assets`, `kis/us_ranking`, `nyse/current_halts` capability·entitlement·requirement를 가진 non-fixture `ready` foundation을 만든다.
- manifest ID는 exact Opportunity ID, security snapshot ID, source coverage를 결합한다.
- scanner projection schema v2는 exact foundation JSON, manifest ID, security snapshot ID를 replay-bound scanner snapshot과 같은 append-only row에 저장한다.
- latest reader는 canonical Parquet/DuckDB identity와 persisted foundation readiness·causality를 모두 다시 검증한다.
- KIS watch는 projection store, canonical root, security-master store를 all-or-none으로 하위 scan에 전달하며 arm, credential, endpoint 또는 fixture override를 추가하지 않는다.

## 실제 로컬 QA

- 기존 raw-first Alpaca store의 actual latest snapshot 13,011 instruments를 사용했다.
- synthetic complete KIS Opportunity의 공개 symbol 하나가 `alpaca:` instrument로 해석됐다.
- raw row 1개, projection row 1개, canonical candidate 1개와 three-source `ready` foundation을 확인했다.
- 이 QA는 기존 SQLite와 local temporary directory만 읽고 썼으며 외부 GET, 계좌, 주문 또는 broker mutation을 만들지 않았다.

## 검증

- focused scanner/foundation/KIS/watch contracts: **45 passed**
- full repository: **2196 passed**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 신규·분리 production module 8개 위반 0건
- CLI help, partial-path error, mutually-exclusive input error, actual-security local E2E: 통과

## 다음 경계

정규장에 operational watch를 실행해 KIS 실제 6개 ranking coverage와 NYSE halt snapshot이 같은 cycle에서 foundation과 scanner row로 누적되는지 관찰한다. 이후 desired candidate별 SIP runtime owner를 생성해 완료 분봉 feature evidence를 M4.4 gate에 공급한다. 실제 Paper 주문 smoke는 이 read-only data vertical과 별개이며 기존 arm·Paper origin·account binding·current-bar 안전 게이트를 그대로 유지한다.
