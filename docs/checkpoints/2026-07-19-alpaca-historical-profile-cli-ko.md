# Alpaca Historical Profile 운영 CLI 체크포인트

- 날짜: 2026-07-19
- 범위: durable 20-session profile artifact와 actual Paper data GET smoke
- 실제 외부 GET: 20건
- account/order endpoint: 0건
- POST/DELETE mutation: 0건

## 구현

- `run_alpaca_sip_historical_profile.py`에 instrument, symbol, target session, through minute, private state/report 경로를 명시한다.
- 기존 mode-600 Alpaca secret loader만 사용하고 credential 값은 출력·artifact·report에 넣지 않는다.
- state root는 mode 700이며 evidence SQLite, canonical datasets와 content-addressed profile JSON을 그 아래 둔다.
- profile JSON은 mode 600이고 동일 evidence append는 byte-identical일 때만 idempotent하다.
- reader는 source identity 20개의 내부 SHA-256과 source dates, cumulative volumes, median, semantic version, evidence SHA와 filename을 재검증한다.
- symlink root, public mode, 변조된 identity와 불완전 CLI 인자는 fail-closed다.

## Actual Read-Only QA

- 저장된 current Alpaca security master에서 AAPL provider alias와 canonical instrument ID를 read-only로 찾았다.
- target session `2026-07-20`, through minute `35`로 실행했다.
- actual historical SIP GET 20건, raw page 20개, canonical session dataset 20개, profile artifact 1개가 생성됐다.
- 같은 명령을 즉시 재실행해 `new raw page: 0`을 확인했다.
- 두 번의 잘못된 local instrument lookup 시도는 credential HTTP 전에 차단됐고 profile 0개 상태였다.

## 검증

- artifact/CLI focused: **5 tests**
- full repository: **2222 tests**
- Ruff: 통과
- basedpyright: 0 errors/warnings
- compileall: 통과
- no-excuse: 신규 artifact/CLI/test 4개 위반 0건
- CLI `--help`: 0
- CLI incomplete args: 2, credential load 0

## 남은 경계

profile artifact를 broad scanner cycle의 각 desired instrument와 자동 결합하고, owner별 결과·binding·gate 결과를 append-only fleet-cycle audit에 확정하는 운영 orchestrator는 아직 없다. 실제 regular-session current-minute fleet GET도 다음 미국 정규장에 수행해야 한다.
