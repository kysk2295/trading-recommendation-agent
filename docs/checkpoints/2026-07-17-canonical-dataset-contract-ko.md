# Canonical Dataset Contract Checkpoint

- Date: 2026-07-17
- Scope: Institutional Multi-Market Quant Research OS Milestone 3.3a
- Provider network access: 0
- Credential loading: 0
- Broker mutation: 0
- Existing collector, SQLite ledger, Paper execution change: none

## Implemented Contract

- `CanonicalDatasetPartition` fixes canonical `DataSourceId`, explicit `DataMarketDomain`, canonical event type, date-only market date, and canonical event schema version.
- `CanonicalDatasetBatch` binds one partition to one exact `RawObjectPartitionManifest` and a nonempty, sorted, unique tuple of exact `CanonicalEventEnvelope` values.
- Every event must match the partition source, event type, and schema version; its `raw_receipt_ref` must exist in the raw manifest. The raw manifest date must equal the partition date. A manifest receipt is not required to yield an event.
- Partition and batch `model_copy` calls revalidate their complete state. Tampered nested source/schema data, empty events, mixed source/type, or mismatched receipt/date lineage fail closed.
- Sensitive raw bytes and sensitive lineage keys are rejected before canonical validation for constructor, `model_validate`, and `model_copy`. Public dumps and validation error `str`/`repr` do not disclose raw payload data.
- `CanonicalDatasetBatch` keeps Pydantic's native `model_validate` API and is verified with the declared lower Pydantic 2.11.10 and the locked environment.

## Verification

- Focused contract tests: `44 passed`
- Focused tests with Pydantic 2.11.10: `44 passed`
- Ruff: passed
- basedpyright: `0 errors, 0 warnings, 0 notes`
- Specification review: approved
- Code-quality review: approved
- Provider network, credential loading, broker mutation: all 0

## Next Boundary

M3.3b will add a deterministic typed Parquet writer that accepts only this validated batch and publishes source/market/event/date/schema partitions without raw receipt payloads or execution-account data. M3.4 will then add DuckDB replay-hash conformance on those canonical files.
