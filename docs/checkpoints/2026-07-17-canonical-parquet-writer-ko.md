# Canonical Parquet Writer Checkpoint

- Date: 2026-07-17
- Scope: Institutional Multi-Market Quant Research OS Milestone 3.3b
- Provider network access: 0
- Credential loading: 0
- Broker mutation: 0
- Existing collector, SQLite ledger, Paper execution change: none

## Implemented Writer

- `write_canonical_dataset_parquet()` accepts only an exact, revalidated `CanonicalDatasetBatch`. Subclass, `model_construct`, nested schema tampering, extra sensitive field, empty or mixed lineage batch all fail closed.
- The dataset path is partitioned by source provider/feed, market domain, event type, market date, and canonical event schema version. A content-only SHA-256 dataset ID prevents a machine path or current time from changing dataset identity.
- Each immutable dataset directory has exactly `events.parquet` and `dataset_manifest.json`. The sidecar contains only partition data, raw manifest ID/content hash, event count, canonical event hash, Parquet hash, and schema version.
- The Arrow schema represents every canonical event envelope field. Entity references use a typed list of structs; timestamps are normalized to UTC `timestamp('us')`; source provider/feed are persisted in rows.
- Raw receipt bytes/base64, receipt list, account information, request keys, and execution state are excluded from Parquet rows, Parquet metadata, sidecar JSON, public publication repr, sanitized error text/repr, cause, and context.
- `pyarrow==25.0.0` is pinned so the writer metadata and resulting bytes are reproducible with the project lock.
- Completed staging directories are published without overwrite using `renamex_np(RENAME_EXCL)` on macOS or `renameat2(RENAME_NOREPLACE)` on Linux. Files use `0600`; output and partition directories use `0700`; newly created directory entries and final publish parents are fsynced.
- Windows and unknown platforms fail closed because this private writer requires POSIX descriptor, ownership, mode, and no-follow guarantees.

## Verification

- Focused canonical dataset and Parquet writer tests: `65 passed`
- Full regression: `1928 passed`
- Ruff: passed
- basedpyright: `0 errors, 0 warnings, 0 notes`
- `uv lock --check`: passed
- Specification review: approved
- Code-quality review: approved
- Provider network, credential loading, broker mutation: all 0

## Next Boundary

M3.4 will use DuckDB against these Parquet files to establish deterministic replay-hash conformance before exposing any research query or strategy/backtest consumer.
