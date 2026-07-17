# Canonical DuckDB Replay Checkpoint

- Date: 2026-07-17
- Scope: Institutional Multi-Market Quant Research OS Milestone 3.4
- Provider network access: 0
- Credential loading: 0
- Broker mutation: 0
- Existing collector, SQLite ledger, Paper execution change: none

## Implemented Replay Conformance

- `replay_canonical_dataset()` accepts one completed M3.3b dataset directory and returns only safe dataset, manifest, Parquet, event-count, and canonical-hash fields.
- The reader verifies the private output root and every hive/dataset directory with descriptor-backed no-follow opens, current-user ownership, and exact `0700`; the two dataset files must be current-user regular files with exact `0600`.
- The sidecar must be canonical ASCII JSON with the exact safe writer key set. The final seven hive components must match the sidecar partition and dataset ID.
- The Parquet file is SHA-256 checked and schema-checked before replay. DuckDB reads the same verified inode through a stable `/dev/fd/<fd>` path; if that path is unavailable, replay closes fail-closed.
- DuckDB uses in-memory `read_parquet(?)` with `hive_partitioning=false` and `ORDER BY event_id`. Physical Parquet source/feed/type/schema fields are not replaced by hive path values.
- Every replayed row is revalidated as a `CanonicalEventEnvelope` and must match partition source/type/schema. Sorted unique IDs, count, canonical event hash, and content-only dataset ID must all match the sidecar.
- Modified files, metadata/extra column injection, noncanonical sidecar, self-consistent malformed events, path/root mode changes, symlinks, and post-verification named-file swaps are rejected with a sanitized error that has no cause/context leak.
- DuckDB is pinned to `1.5.4`; no provider, credential, broker, order, or external extension is used.

## Verification

- Focused canonical contract, writer, replay tests: `105 passed`
- Full regression: `1949 passed`
- Ruff: passed
- basedpyright: `0 errors, 0 warnings, 0 notes`
- `uv lock --check`: passed
- Specification review: approved
- Code-quality review: approved
- Provider network, credential loading, broker mutation: all 0

## Next Boundary

Only a restricted local research/backtest read model may consume a replay-verified dataset next. It must retain this dataset ID, canonical replay hash, data requirement, and experiment/strategy version in every artifact; it must not receive a broker or Paper-order capability.
