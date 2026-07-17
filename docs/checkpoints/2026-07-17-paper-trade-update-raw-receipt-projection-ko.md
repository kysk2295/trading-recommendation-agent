# Paper Trade Update Raw Receipt Projection 체크포인트

- 날짜: 2026-07-17
- 범위: Institutional Multi-Market Quant Research OS Milestone 3.2b
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건
- 기존 Paper writer, execution schema, collector 변경: 없음

## 구현

- `ExecutionStoreReader.trade_update_receipt_projection_snapshot()`은 query-only SQLite transaction 하나에서 raw receipt의 `rowid`와 `received_at` metadata만 먼저 읽고, `America/New_York` calendar date가 요청 날짜와 같은 row만 선택한다.
- 선택된 full receipt rows만 deterministic 500-row chunk로 읽고 기존 raw receipt validator로 hash, key, wire kind, aware timestamp를 재검증한다. unrelated BLOB는 projection 과정에서 materialize하지 않는다.
- selected receipt의 actual maximum `rowid`가 parent ledger generation이다. 이후 다른 New York 날짜의 receipt가 append되어도 기존 partition identity는 변하지 않는다.
- account fingerprint, connection epoch, wire kind과 `alpaca:raw:` prefixed key는 validation 직후 narrow snapshot에서 제거한다. adapter에는 bare 64-hex receipt digest, aware received time, payload SHA-256, repr-hidden raw bytes만 전달된다.
- adapter는 `us.alpaca.paper.trade_updates` source ID와 selected NY market date를 고정하고 기존 generic `project_raw_receipt_partition()`으로만 manifest를 만든다.
- no-file 또는 empty date는 manifest 없이 `None`을 반환한다. malformed timestamp, invalid key/hash, non-BLOB SQLite payload, invalid snapshot 또는 requested date와 다른 receipt는 public detail 없이 fail-closed 한다.

## 검증

- focused US projection and receipt tests: `18 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- spec compliance review: 승인
- code quality review: 승인
- provider network, credential loading, broker mutation: 모두 0건

## 다음 경계

M3.2 existing collector projection은 KR source receipt와 US Paper trade-update ledger 모두 완료됐다. M3.3에서 typed Parquet canonical writer와 source/market/event/date/schema partition contract를 추가하고, 이후 M3.4 DuckDB deterministic replay hash conformance를 연결한다.
