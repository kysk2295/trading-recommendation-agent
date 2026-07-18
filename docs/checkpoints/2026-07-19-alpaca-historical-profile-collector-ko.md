# Alpaca Historical Profile Collector 체크포인트

- 날짜: 2026-07-19
- 범위: 직전 20개 완료 정규장 SIP 1분봉의 durable raw/canonical profile source
- 실제 외부 GET: 0건
- account/order endpoint: 0건
- POST/DELETE mutation: 0건

## 구현

- 목표 분에 충분한 직전 20개 적격 정규장 날짜를 calendar로 결정한다.
- 기존 Alpaca SIP GET-only minute page client로 각 세션 open부터 close까지 요청한다.
- exact response bytes를 완료성 검사 전에 append-only evidence SQLite에 저장한다.
- sequence가 open의 1부터 calendar close까지 완전 연속일 때만 세션을 canonical Parquet/DuckDB identity로 투영한다.
- profile은 20세션을 하나의 합성 identity로 축약하지 않고 날짜와 정렬된 verified identity 20개를 직접 보존한다.
- 저장된 exact request의 page index, token chain, terminal token, receipt ID와 payload hash를 재검증해 무네트워크 재시작을 지원한다.

## Fixture E2E

- 첫 process가 20개 full regular session을 20 mock GET으로 수집했다.
- raw page 20개와 canonical dataset 20개가 생성됐다.
- 새 process는 HTTP responder를 열지 않고 같은 profile evidence를 반환했다.
- 한 세션의 마지막 1분을 제거하면 raw page는 보존되지만 profile은 발행되지 않았다.
- 저장된 canonical Parquet에 bytes를 추가하면 재생이 차단됐고 provider fallback은 발생하지 않았다.

## 검증

- focused profile/collector/runtime/fleet/evidence: **71 tests**
- full repository: **2217 tests**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 신규·핵심 파일 6개 위반 0건

## 남은 경계

실제 Paper data credential로 historical GET smoke를 아직 수행하지 않았다. collector를 호출하는 운영 CLI, profile artifact 저장 위치 정책, fleet cycle audit도 다음 단계다. 이 collector는 market-data GET만 사용하며 계좌·주문 권한을 추가하지 않는다.
