# US runtime terminal 재사용 soak 체크포인트

## 완료 범위

- current manifest dispatch 전에 private actionability store 전체를 query-only 재생한다.
- `(base signal ID, scan_started_at)` terminal이 이미 있으면 새 minute manifest라도 WebSocket과 manifest receipt를 만들지 않고 replay로 집계한다.
- 기존 terminal key와 current batch key의 중복은 첫 connector 전에 fail-closed한다.
- 새 signal ID는 과거 terminal과 분리해 새 bounded quote/trade lifecycle을 실행한다.

## 2분 통합 soak

- 동일한 장중 conditional signal을 2분 동안 유효하게 유지했다.
- scanner opportunity와 completed-minute snapshot은 두 번째 minute에 인과적으로 갱신했다.
- dynamic plan은 하나의 stable epoch를 재사용했다.
- 결과는 manifest 2개, WebSocket 연결 1회, receipt DB 1개, actionability terminal 1개였다.
- supervisor parent는 두 번 모두 READY였고 live child aggregate는 `1/1/0`, `1/0/1`이었다.

## 검증

- 관련 runtime/actionability/audit 회귀: `23 passed`
- 전체 회귀: `2563 passed`
- Ruff, basedpyright `0 errors, 0 warnings`, compileall, changed-file no-excuse 통과
- fixture transport 밖 provider WebSocket, account/order/position endpoint와 broker mutation: 0건

## 다음 경계

- 실제 열린 NYSE 정규장과 private market-data credential·SIP entitlement가 모두 맞을 때만 bounded read-only smoke를 실행한다.
- supervisor child aggregate와 manifest별 receipt terminal·actionability artifact를 대사하는 query-only cross-store verifier를 다음 구현 단위로 둔다.
