# Alpaca SIP actionability 생성 바인딩 체크포인트

## 목적

supervisor live child가 `new=1`이라고 기록한 시도와 actionability artifact의 실제 최초 append를 독립 verifier가 대사할 수 있도록, artifact와 그 생성 manifest를 하나의 append-only SQLite transaction에 묶는다.

## 구현

- schema v1 artifact payload와 기존 `append()` 계약은 변경하지 않았다.
- schema v2에 artifact ID별 하나의 content-addressed creation row를 추가했다.
- creation은 exact manifest ID와 manifest snapshot의 `observed_at`을 보존한다.
- `append_for_manifest()`는 artifact와 creation을 하나의 `BEGIN IMMEDIATE` transaction에서 append한다.
- exact replay는 원래 creation을 반환하고 새 행을 만들지 않는다.
- v1 query는 migration하지 않으며 빈 creation history를 반환한다.
- 다음 v2 Writer만 schema를 전진시키고, creation 없는 legacy artifact를 사후 추정해 backfill하지 않는다.
- mode 600, current owner, regular file, single hard link, exact schema object와 UPDATE/DELETE trigger를 read마다 검증한다.

## 검증

- 신규 artifact와 creation의 원자적 append 및 exact replay
- v1 query-only 무변경
- v1 store의 다음 신규 manifest append에서만 v2 migration
- legacy artifact creation backfill 차단
- creation trigger/payload tamper fail-closed
- 관련 25개와 전체 2576 tests, Ruff, changed-file format, basedpyright 0/0, compileall, no-excuse를 통과했다.

## 현재 경계

이 체크포인트는 durable store 계약만 추가한다. live projector와 dispatcher는 아직 기존 v1 append 경로를 사용하므로 운영 artifact에 creation row가 자동 생성된다고 주장하지 않는다. 다음 체크포인트에서 manifest-aware append를 live lifecycle에 연결하고 verifier가 v2 creation을 우선 사용하도록 한다. provider, credential, WebSocket, account/order endpoint와 broker mutation은 열지 않았다.
