# ORB Lane Daily Snapshot And Reviewer Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finalize one causally complete ORB intraday Paper day into the lane registry, then let an independent query-only Reviewer append a non-authoritative decision to a global review ledger.

**Architecture:** Keep broker finalization in the intraday lane Writer and research judgment in a separate Reviewer process. The snapshot producer reuses the existing GET/WSS readiness, binds a canonical execution-ledger identity and exact ORB daily record to one flat `LaneDailySnapshot`, while the Reviewer reads that snapshot through `LaneRegistryReader` and stores daily/adaptive artifact hashes in its own append-only SQLite ledger.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite WAL/query-only URI connections, Typer/argparse-compatible executable scripts, pytest, Ruff, basedpyright.

---

### Task 1: Add A Stable Query-Only Execution Ledger Identity

**Files:**
- Create: `trading_agent/execution_ledger_identity.py`
- Modify: `trading_agent/execution_store_reader.py`
- Create: `tests/test_execution_ledger_identity.py`

- [x] **Step 1: Write failing stability and append-change tests**

Create an initialized execution store, read its identity twice around a WAL checkpoint, then append one intent and prove both generation and hash change.

```python
def test_execution_identity_is_stable_across_readers_and_wal_checkpoint(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    first = store.ledger_snapshot_identity()
    with sqlite3.connect(store.path) as connection:
        _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    assert store.ledger_snapshot_identity() == first


def test_execution_identity_changes_after_an_append(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, OBSERVED_AT)
    before = store.ledger_snapshot_identity()
    with store.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
    after = store.ledger_snapshot_identity()
    assert after.generation > before.generation
    assert after.sha256 != before.sha256
```

- [x] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_execution_ledger_identity.py`

Expected: `ExecutionStore.ledger_snapshot_identity` is missing.

- [x] **Step 3: Implement canonical streaming identity**

Add a frozen result and encode every current-schema user table inside one read transaction.

```python
@dataclass(frozen=True, slots=True)
class ExecutionLedgerSnapshotIdentity:
    generation: int
    sha256: str


def read_execution_ledger_snapshot_identity(
    connection: sqlite3.Connection,
) -> ExecutionLedgerSnapshotIdentity:
    digest = hashlib.sha256()
    generation = 0
    tables = _user_tables(connection)
    for table, schema_sql in tables:
        _hash_value(digest, table)
        _hash_value(digest, schema_sql)
        cursor = connection.execute(
            f'SELECT rowid, * FROM "{_quoted(table)}" ORDER BY rowid'
        )
        for row in cursor:
            generation += 1
            for value in row:
                _hash_value(digest, value)
    return ExecutionLedgerSnapshotIdentity(generation, digest.hexdigest())
```

`ExecutionStoreReader.ledger_snapshot_identity()` must use `_reader_connection()`, execute `BEGIN`, call this function, and never expose path or row content.

- [x] **Step 4: Verify malformed schema and missing DB behavior**

Add tests that a missing DB returns no forged identity and a user-version-9 DB with missing objects raises `ExecutionSchemaIntegrityError` through the existing reader gate.

- [x] **Step 5: Run focused verification**

Run: `uv run pytest -q tests/test_execution_ledger_identity.py tests/test_execution_store.py`

Expected: all tests pass.

- [x] **Step 6: Commit and push the identity checkpoint**

```bash
git add trading_agent/execution_ledger_identity.py trading_agent/execution_store_reader.py tests/test_execution_ledger_identity.py
git commit -m "feat: hash execution ledger snapshots"
git push origin feature/paper-account-activities
```

### Task 2: Add One Exact Daily Research Record Source

**Files:**
- Create: `trading_agent/daily_research_record_source.py`
- Create: `tests/test_daily_research_record_source.py`

- [x] **Step 1: Write failing exact-source tests**

Cover latest ORB record selection, schema-v1 projection without rewrite, parent-ledger membership, and rejection of a different scope/date/strategy.

```python
source = load_daily_research_record_source(
    session,
    dt.date(2026, 7, 14),
    StrategyMode.ORB,
    experiment_scope_key(current_intraday_experiment_scope("H-MOM-ORB-001")),
)
assert source.record.strategy == "orb"
assert source.record_path.is_file()
assert source.raw_sha256 == hashlib.sha256(source.record_path.read_bytes()).hexdigest()
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_daily_research_record_source.py`

Expected: module missing.

- [x] **Step 3: Implement the source loader**

Define a frozen `DailyResearchRecordSource(record, record_path, raw_sha256)`. Parse each JSON with `parse_daily_record`, filter exact date/strategy/scope, choose the greatest `(recorded_at, record_id)`, and require its ID in `read_daily_ledger(session.parent / "daily_research_ledger.jsonl")`.

- [x] **Step 4: Run daily-ledger regressions**

Run:

```bash
uv run pytest -q tests/test_daily_research_record_source.py tests/test_daily_research_lane_scope.py tests/test_adaptive_evaluation_cli.py
```

Expected: all tests pass and no source file is rewritten.

- [x] **Step 5: Commit and push the source checkpoint**

```bash
git add trading_agent/daily_research_record_source.py tests/test_daily_research_record_source.py
git commit -m "feat: resolve exact daily research records"
git push origin feature/paper-account-activities
```

### Task 3: Make The Lane Registry Reader Independently Constructible

**Files:**
- Modify: `trading_agent/lane_registry_store.py`
- Modify: `tests/test_lane_registry_store.py`

- [x] **Step 1: Write a failing reader-only construction test**

```python
reader = LaneRegistryReader(store.path)
assert reader.manifests() == store.manifests()
assert not hasattr(reader, "writer")
with reader._reader_connection() as connection:
    assert connection.execute("PRAGMA query_only").fetchone() == (1,)
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `uv run pytest -q tests/test_lane_registry_store.py -k independently_constructible`

Expected: `LaneRegistryReader` does not accept a path.

- [x] **Step 3: Move path ownership to the base reader**

Give `LaneRegistryReader` `__slots__ = ("path",)` and a resolving constructor. Let `LaneRegistryStore` inherit it with `__slots__ = ()` and no duplicate constructor. Add a `daily_snapshot(lane_id, session_date)` query that returns zero or one row and raises a typed integrity error if SQLite contains duplicates.

- [x] **Step 4: Run registry regressions**

Run: `uv run pytest -q tests/test_lane_registry_store.py tests/test_lane_control_plane_bootstrap_cli.py`

Expected: all tests pass.

- [x] **Step 5: Commit and push the reader checkpoint**

```bash
git add trading_agent/lane_registry_store.py tests/test_lane_registry_store.py
git commit -m "refactor: expose query-only lane registry reader"
git push origin feature/paper-account-activities
```

### Task 4: Build The Intraday Final Snapshot Producer

**Files:**
- Create: `trading_agent/intraday_lane_daily_snapshot.py`
- Create: `tests/test_intraday_lane_daily_snapshot.py`

- [x] **Step 1: Write failing happy-path and replay tests**

Seed current manifests/scopes/binding, an exact ORB daily record, an initialized execution DB, and a fake readiness observed after the 2026-07-14 close.

```python
result = finalize_intraday_lane_day(
    registry,
    execution,
    session,
    dt.date(2026, 7, 14),
    flat_readiness_after_close(),
    evaluated_at=dt.datetime(2026, 7, 15, 0, 5, tzinfo=dt.UTC),
)
assert result.created is True
assert result.snapshot.open_order_count == 0
assert result.snapshot.open_position_count == 0
assert result.snapshot.champion_strategy_versions == ()
assert result.snapshot.allocation_eligible is False
```

Replay with the same evidence and assert `created is False` and one stored snapshot.

- [x] **Step 2: Write failing finalization-gate tests**

Parametrize close-before-finalization, market-open, blocked readiness, open order, nonzero position, wrong account fingerprint, missing current manifest/scope/binding, wrong daily scope, and parent-ledger absence. Assert the registry has no snapshot after every failure.

- [x] **Step 3: Run tests and verify RED**

Run: `uv run pytest -q tests/test_intraday_lane_daily_snapshot.py`

Expected: module missing.

- [x] **Step 4: Implement local preflight and producer result**

Define redacted `InvalidIntradayLaneFinalizationError`, `IntradayLaneSnapshotPreflight`, and `IntradayLaneSnapshotResult`. `preflight_intraday_lane_day()` validates all local sources without credentials or network. `finalize_intraday_lane_day()` repeats immutable source checks after readiness and appends with the registry Writer.

Use:

```python
conservative_equity = min(account.equity, account.last_equity)
realized_pnl = account.equity - account.last_equity
incidents = tuple(sorted(set((*record.incidents, *quality_incidents))))
```

When an existing lane/date snapshot is present, reuse its `finalized_at` in the candidate so timestamp drift does not break exact replay. Every other candidate field is recomputed.

- [x] **Step 5: Verify conflict and incomplete-quality behavior**

Add tests that a changed execution hash/PnL causes `LaneRegistryConflictError`, while an ineligible daily record can still finalize with `data_quality_complete=False`, an explicit `data_quality_incomplete` incident, no champion, and allocation false.

- [x] **Step 6: Run focused snapshot regressions**

Run:

```bash
uv run pytest -q tests/test_intraday_lane_daily_snapshot.py tests/test_lane_contract_models.py tests/test_lane_registry_store.py tests/test_execution_ledger_identity.py
```

Expected: all tests pass.

- [x] **Step 7: Commit and push the producer checkpoint**

```bash
git add trading_agent/intraday_lane_daily_snapshot.py tests/test_intraday_lane_daily_snapshot.py
git commit -m "feat: finalize intraday lane daily snapshots"
git push origin feature/paper-account-activities
```

### Task 5: Add The GET-Only Snapshot CLI

**Files:**
- Create: `run_intraday_lane_daily_snapshot.py`
- Create: `tests/test_intraday_lane_daily_snapshot_cli.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Write failing CLI tests**

Cover executable `--help`, invalid date, missing local registry/execution/session before credential loading, fake flat readiness success, replay, broker-blocked result, and report redaction.

```python
code = snapshot_cli.main(
    args,
    credential_loader=fake_credentials,
    probe_loader=lambda _credentials, _store: flat_readiness_after_close(),
    clock=lambda: FINALIZED_AT,
)
assert code == 0
assert FINGERPRINT not in report
assert "test-secret" not in report
assert "외부 Alpaca mutation: 0건" in report
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_intraday_lane_daily_snapshot_cli.py`

Expected: script missing.

- [x] **Step 3: Implement CLI without a fixture bypass flag**

Use argparse, dependency-injected loaders only in `main()`, and the production defaults `load_alpaca_paper_credentials` plus `probe_paper_runtime`. Run `preflight_intraday_lane_day` before calling the credential loader. Catch API/WSS/schema/source errors and write only a generic blocked report.

- [x] **Step 4: Add basedpyright coverage and run CLI QA**

Run:

```bash
./run_intraday_lane_daily_snapshot.py --help
uv run pytest -q tests/test_intraday_lane_daily_snapshot_cli.py
uv run ruff check run_intraday_lane_daily_snapshot.py trading_agent/intraday_lane_daily_snapshot.py tests/test_intraday_lane_daily_snapshot_cli.py
uv run basedpyright
```

Expected: help 0, fake happy path 0, local malformed input nonzero before credential load, and no POST/DELETE method is opened.

- [x] **Step 5: Document and checkpoint snapshot production**

Create `docs/checkpoints/2026-07-15-orb-lane-daily-snapshot-ko.md`, update README/CODEX status, run full tests, then commit and push.

```bash
git add README.md CODEX_START_HERE.md docs/checkpoints/2026-07-15-orb-lane-daily-snapshot-ko.md pyproject.toml run_intraday_lane_daily_snapshot.py tests/test_intraday_lane_daily_snapshot_cli.py
git commit -m "feat: add GET-only intraday snapshot CLI"
git push origin feature/paper-account-activities
```

### Task 6: Define Immutable Reviewer Events

**Files:**
- Create: `trading_agent/lane_review_models.py`
- Create: `trading_agent/lane_review_keys.py`
- Create: `tests/test_lane_review_models.py`

- [x] **Step 1: Write failing contract and key tests**

Define `LaneReviewerAction` and require exact 64-char hashes, aware timestamps, sorted unique reasons/blockers, false-only authority flags, and deterministic keys.

```python
assert event.automatic_state_change_allowed is False
assert event.order_authority_change_allowed is False
assert len(lane_review_event_key(event)) == 64
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_lane_review_models.py`

Expected: module missing.

- [x] **Step 3: Implement frozen Pydantic contracts**

`LaneReviewEvent` fields must include lane/date, snapshot/scope keys, daily record ID/hash, adaptive hash, strategy/evaluator/reviewer versions, adaptive and Reviewer actions, reasons, blockers, `reviewed_at`, and the two false literals. Canonical JSON and SHA-256 must follow `lane_contract_keys` conventions without accepting caller keys.

- [x] **Step 4: Run focused model verification**

Run: `uv run pytest -q tests/test_lane_review_models.py tests/test_lane_contract_models.py`

Expected: all tests pass.

### Task 7: Add The Global Append-Only Review Ledger

**Files:**
- Create: `trading_agent/lane_review_schema.py`
- Create: `trading_agent/lane_review_store.py`
- Create: `tests/test_lane_review_store.py`

- [x] **Step 1: Write failing schema, lease, replay, and conflict tests**

Verify schema v1, mode 600, UPDATE/DELETE triggers, nonblocking Writer lease, query-only reader, exact replay, and conflict for the same `(snapshot_key, scope_key, reviewer_version)` with changed payload.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_lane_review_store.py`

Expected: module missing.

- [x] **Step 3: Implement one-table review ledger**

Create `lane_review_events` with canonical payload and indexed identity columns. Reader constructor takes a path and has no writer method. Store owns one nonblocking writer context and commits before returning.

- [x] **Step 4: Run store verification**

Run: `uv run pytest -q tests/test_lane_review_store.py tests/test_lane_review_models.py`

Expected: all tests pass.

- [x] **Step 5: Commit and push Reviewer contract/store**

```bash
git add trading_agent/lane_review_models.py trading_agent/lane_review_keys.py trading_agent/lane_review_schema.py trading_agent/lane_review_store.py tests/test_lane_review_models.py tests/test_lane_review_store.py
git commit -m "feat: add append-only lane review ledger"
git push origin feature/paper-account-activities
```

### Task 8: Implement The Independent Reviewer

**Files:**
- Create: `trading_agent/lane_reviewer.py`
- Create: `tests/test_lane_reviewer.py`

- [x] **Step 1: Write failing happy-path action mapping tests**

Seed a finalized snapshot plus matching daily/adaptive artifacts and assert mappings for continue, early stop, diagnose, comparison ready, suspend, and promotion review blocked.

```python
result = review_intraday_lane_day(
    LaneRegistryReader(registry_path),
    LaneReviewStore(review_path),
    session,
    session_date,
    reviewed_at=REVIEWED_AT,
)
assert result.event.reviewer_action is LaneReviewerAction.CONTINUE_COLLECTION
assert result.event.automatic_state_change_allowed is False
```

- [x] **Step 2: Write failing source-integrity tests**

Reject missing/duplicate snapshot, nonflat snapshot, daily scope/date/version mismatch, parent-ledger absence, malformed adaptive JSON, and adaptive date/strategy/evaluator mismatch. Assert no review event is appended.

- [x] **Step 3: Run tests and verify RED**

Run: `uv run pytest -q tests/test_lane_reviewer.py`

Expected: module missing.

- [x] **Step 4: Implement Reviewer without execution or Alpaca imports**

Load the exact daily source and `AdaptiveEvaluation`, hash both raw files, map action, and union sorted blockers from snapshot quality/incidents, daily promotion blockers, adaptive proof blockers, missing champion, and allocation ineligibility. If an event with the same identity exists, reuse its first `reviewed_at` while recomputing every other field.

- [x] **Step 5: Enforce promotion blocking and replay conflict**

Add tests that `PROMOTION_REVIEW` never creates champion/order authority, exact replay adds no row, and changed adaptive bytes/action under the same reviewer version causes typed conflict.

- [x] **Step 6: Run focused Reviewer verification**

Run:

```bash
uv run pytest -q tests/test_lane_reviewer.py tests/test_lane_review_store.py tests/test_adaptive_evaluation_cli.py
```

Expected: all tests pass.

### Task 9: Add The Local-Only Reviewer CLI

**Files:**
- Create: `run_lane_reviewer.py`
- Create: `tests/test_lane_reviewer_cli.py`
- Modify: `pyproject.toml`

- [x] **Step 1: Write failing CLI tests**

Cover executable help, malformed date/path, missing snapshot, happy event, exact replay, immutable conflict, and report redaction. Assert the module has no Alpaca credential loader, HTTP client, execution store, or mutation imports.

- [x] **Step 2: Run tests and verify RED**

Run: `uv run pytest -q tests/test_lane_reviewer_cli.py`

Expected: script missing.

- [x] **Step 3: Implement local Reviewer CLI**

Use `LaneRegistryReader`, `LaneReviewStore`, and `review_intraday_lane_day`. The report may include lane/date, adaptive action, reviewer action, blockers, created/replayed state, and the two authority-denied flags. It must not include paths, hashes, keys, account data, credentials, or broker IDs.

- [x] **Step 4: Run executable manual QA**

Run help, missing input, a fully local fixture happy path, replay, and tampered adaptive input. Confirm exit codes `0/1`, append-only event counts, and redaction.

- [x] **Step 5: Add basedpyright coverage and focused verification**

Run:

```bash
uv run pytest -q tests/test_lane_reviewer_cli.py tests/test_lane_reviewer.py
uv run ruff check run_lane_reviewer.py trading_agent/lane_reviewer.py tests/test_lane_reviewer_cli.py
uv run basedpyright
```

Expected: zero failures, errors, or warnings.

### Task 10: Final Documentation, Full Verification, And Push

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-15-orb-lane-review-loop-ko.md`
- Modify: `docs/superpowers/plans/2026-07-15-orb-lane-daily-review-loop.md`

- [x] **Step 1: Document exact authority boundaries**

State that snapshot generation uses GET/WSS only, Reviewer is local/query-only against the lane registry, review events cannot change strategy/order authority, champion remains absent, Portfolio Manager remains absent, and external POST/DELETE is zero.

- [x] **Step 2: Run focused verification**

```bash
uv run pytest -q tests/test_execution_ledger_identity.py tests/test_daily_research_record_source.py tests/test_intraday_lane_daily_snapshot.py tests/test_intraday_lane_daily_snapshot_cli.py tests/test_lane_review_models.py tests/test_lane_review_store.py tests/test_lane_reviewer.py tests/test_lane_reviewer_cli.py
```

Expected: all tests pass.

- [x] **Step 3: Run full verification one heavy command at a time**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run ruff format --check run_intraday_lane_daily_snapshot.py run_lane_reviewer.py trading_agent/daily_research_record_source.py trading_agent/execution_ledger_identity.py trading_agent/execution_store_reader.py trading_agent/intraday_lane_daily_snapshot.py trading_agent/lane_registry_store.py trading_agent/lane_review_keys.py trading_agent/lane_review_models.py trading_agent/lane_review_schema.py trading_agent/lane_review_store.py trading_agent/lane_reviewer.py tests/test_daily_research_record_source.py tests/test_execution_ledger_identity.py tests/test_intraday_lane_daily_snapshot.py tests/test_intraday_lane_daily_snapshot_cli.py tests/test_lane_registry_store.py tests/test_lane_review_models.py tests/test_lane_review_store.py tests/test_lane_reviewer.py tests/test_lane_reviewer_cli.py
git diff --check
```

Expected: zero failures, zero Ruff findings, zero basedpyright errors/warnings, and no formatting drift.

- [x] **Step 4: Run final manual CLI QA**

Run snapshot help/local-blocked/fake-readiness happy/replay and Reviewer help/missing/happy/replay/tamper. Inspect every report for credential, fingerprint, path, key, hash, broker ID, or raw payload leakage. Do not call POST/DELETE.

- [x] **Step 5: Commit, push, and verify origin alignment**

```bash
git add README.md CODEX_START_HERE.md docs/architecture_ko.md docs/checkpoints/2026-07-15-orb-lane-review-loop-ko.md docs/superpowers/plans/2026-07-15-orb-lane-daily-review-loop.md pyproject.toml run_lane_reviewer.py tests/test_lane_reviewer_cli.py
git commit -m "feat: add independent ORB lane Reviewer loop"
git push origin feature/paper-account-activities
git rev-list --left-right --count HEAD...origin/feature/paper-account-activities
```

Expected: `0 0`, clean worktree, current execution schema still v9, lane registry schema still v1, review ledger schema v1, and external Alpaca POST/DELETE count still zero.
