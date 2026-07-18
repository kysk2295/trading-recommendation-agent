# US runtime capability registry 체크포인트

## 완성 범위

- 기존 append-only M4 runtime fleet audit의 최신 확정 cycle을 query-only로 읽는다.
- owner별 READY, blocked, failed와 runtime status·profile/feature identity를 재검증한다.
- bounded runtime universe의 READY owner 비율을 canonical evidence와 같은 `alpaca/sip` completeness bps로 집계한다.
- aggregate health를 complete, degraded, failed로 나눠 전역 capability registry에 append한다.
- 고정 Paper-recommendation entitlement와 시점별 health assessment를 분리한다.

## 무결성

- fleet audit store는 mode 600, current owner, regular file, no-symlink와 `BEGIN IMMEDIATE`를 요구한다.
- policy/fleet/gate status, owner/runtime status, feature 존재 조건과 SHA-256 identity를 교차검증한다.
- owner instrument evidence는 원래 fleet audit에 보존하고 source-level registry가 개별 feed identity를 위조하지 않는다.
- cycle 완료시각은 actual minute-bar event 시각이 아니라 source heartbeat로만 사용한다.
- exact retry는 같은 capability·entitlement를 다시 append하지 않는다.

## 수동 QA

- `--help`: exit 0, provider·arm·주문 옵션 없음
- audit 누락: exit 1, blocked report
- 2/2 READY fixture: exit 0, complete, capability·entitlement 각 1/1 resolved
- exact retry: capability 0, entitlement 0 추가
- audit, registry와 report mode: 600

## 검증

- `pytest`: 2288 passed
- Ruff: passed
- basedpyright: 0 errors, 0 warnings
- compileall, changed-file format, no-excuse rules: passed

## 안전 경계

- existing fleet audit mutation 0건
- fixture 외 provider·credential·network 접근 0건
- account/order endpoint와 broker mutation 0건
- complete는 선택된 bounded owner coverage이며 Alpaca 전체 시장 coverage, streaming 또는 전략 성과를 뜻하지 않음

## 다음 단계

- canonical event의 entity/claim/burst/corroboration research read model
- 열린 NYSE 정규장에서만 bounded Alpaca data GET smoke
- provider deletion cursor와 retention 이행 상태
