# KR Source Raw Receipt Projection 체크포인트

- 날짜: 2026-07-17
- 범위: Institutional Multi-Market Quant Research OS Milestone 3.2a
- provider network access: 0건
- credential loading: 0건
- broker mutation: 0건
- 기존 collector, writer, SQLite schema 변경: 없음

## 구현

- `KrThemeReader.source_receipt_projection_snapshot()`은 하나의 query-only SQLite transaction에서 요청 cycle/source의 terminal run과 그 run에 연결된 receipt만 읽는다. unrelated raw BLOB는 projection 과정에 materialize하지 않는다.
- snapshot은 실제 selected receipt `rowid`의 maximum을 parent ledger generation으로 보존한다. 이후 다른 run이 append되어도 이미 확정된 partition identity는 바뀌지 않는다.
- `project_kr_source_run_receipts()`는 snapshot의 cycle/source, terminal status, explicit collection date, receipt ID lineage와 byte SHA-256을 다시 검증한 뒤 기존 generic `project_raw_receipt_partition()`으로만 manifest를 만든다.
- `dart`, `news`, `kis_ranking`, `volume_surge`는 각각 `kr.opendart`, `kr.ls.nws`, `kr.kis.ranking`, `kr.kis.volume_surge` source ID로 고정한다.
- 실패 run, orphan receipt, collection date가 없는 run, terminal run 뒤 추가된 같은 run의 receipt, requested cycle/source와 다른 snapshot은 manifest로 승격되지 않는다.
- provider-backed DART, LS NWS, KIS ranking은 empty success partition을 만들 수 없다. DB-only `volume_surge` derived run만 receipt와 generation이 모두 비어 있을 때 `None`으로 표현한다.
- raw payload, request key, source run ID는 generic public manifest와 sanitized adapter error에 포함하지 않는다.

## 검증

- focused KR projection tests: `20 passed`
- Ruff: 통과
- basedpyright: `0 errors, 0 warnings, 0 notes`
- spec compliance review: 승인
- code quality review: 승인
- provider network, credential loading, broker mutation: 모두 0건

## 다음 경계

M3.2b에서 기존 US Paper `trade_update_raw_receipts`를 같은 generic manifest contract에 read-only로 연결한다. Alpaca daily/minute archive는 raw receipt가 아니므로 source로 가장하지 않는다. 그 뒤 typed Parquet canonical writer와 DuckDB deterministic replay를 추가한다.
