# Source-bound Paper Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove free-form Paper entry inputs and derive the first armed entry request from exactly one current ORB recommendation in the read-only KIS watch database.

**Architecture:** Add a focused query-only source loader that reads the existing recommendation, candidate input, and first-observed minute-bar tables inside one SQLite read transaction. The entry CLI accepts only the watch database path and passes the loader's existing `PaperOrderAdmissionRequest` into the unchanged current-epoch operating session, which independently rechecks market, stream, broker, portfolio, and bar freshness before mutation.

**Tech Stack:** Python 3.12, stdlib `sqlite3`/`datetime`/`math`, pytest, Ruff, basedpyright, existing Paper execution and KIS archive models.

---

### Task 1: Exact current ORB source loader

**Files:**
- Create: `trading_agent/paper_entry_source.py`
- Create: `tests/test_paper_entry_source.py`

- [ ] **Step 1: Write the valid-source failing test**

Create a real `PaperStore` database with one `Recommendation`, `CandidateInputSnapshot`, and `CandidateBarBatch`. Store the recommendation in New York time and the candidate input at the same instant with a different UTC offset. Assert that `load_current_orb_paper_entry()` returns the exact recommendation ID, symbol, prices, bar times, spread, `liquidity_allowed_quantity == 1`, and `INTRADAY_PILOT_PAPER_RISK_CONFIG`.

```python
request = load_current_orb_paper_entry(database, EVALUATED_AT)

assert request.candidate_intent.intent_id == RECOMMENDATION_ID
assert request.latest_bar.started_at == BAR_START
assert request.latest_bar.first_observed_at == OBSERVED_AT
assert request.liquidity_allowed_quantity == 1
assert request.estimated_spread_bps == 12.5
assert request.config is INTRADAY_PILOT_PAPER_RISK_CONFIG
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest -q tests/test_paper_entry_source.py::test_loads_one_exact_current_orb_candidate_across_timezone_offsets
```

Expected: collection failure because `trading_agent.paper_entry_source` does not exist.

- [ ] **Step 3: Implement the minimal read-only loader**

Create a generic source error and one public loader:

```python
class InvalidCurrentOrbPaperEntrySourceError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "현재 ORB Paper entry source를 안전하게 확정하지 못했습니다"


def load_current_orb_paper_entry(
    path: Path,
    evaluated_at: dt.datetime,
) -> PaperOrderAdmissionRequest:
    if not path.is_file() or not _is_aware(evaluated_at):
        raise InvalidCurrentOrbPaperEntrySourceError
    try:
        with _connect_readonly(path) as connection:
            _ = connection.execute("BEGIN")
            recommendations = _recommendations(connection)
            inputs = _candidate_inputs(connection)
            bars = _candidate_bars(connection)
        requests = _current_requests(recommendations, inputs, bars, evaluated_at)
    except (OSError, sqlite3.Error, ValueError, OverflowError):
        raise InvalidCurrentOrbPaperEntrySourceError from None
    if len(requests) != 1:
        raise InvalidCurrentOrbPaperEntrySourceError
    return requests[0]
```

Use `sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)`, set `PRAGMA query_only = ON`, parse every timestamp as aware, compare aware datetimes by instant, and project the selected row into `PaperOrderAdmissionRequest` with `PaperOrderSide.BUY`, fixed strategy/version, fixed one-share liquidity, stored spread, and `INTRADAY_PILOT_PAPER_RISK_CONFIG`.

- [ ] **Step 4: Run the valid-source test and verify GREEN**

Run the same test. Expected: `1 passed`.

- [ ] **Step 5: Add validation tests and verify RED/GREEN**

Add parameterized or focused tests for:

```python
def test_missing_database_is_rejected_without_creation(...): ...
def test_rejects_stale_or_future_recommendation(...): ...
def test_rejects_unfinished_or_wrong_minute_bar(...): ...
def test_rejects_multiple_current_candidates(...): ...
def test_rejects_invalid_prices_spread_volume_or_identity(...): ...
def test_source_connection_is_query_only(...): ...
```

The source is valid only when:

```python
expected_start = evaluated_at.astimezone(NEW_YORK).replace(second=0, microsecond=0) - dt.timedelta(minutes=1)
valid = (
    strategy == "opening_range_breakout"
    and state == "setup"
    and observed_at == created_at
    and bar_start == latest_completed_bar_at == expected_start
    and bar_start + dt.timedelta(minutes=1) <= first_observed_at <= created_at <= evaluated_at
    and evaluated_at - created_at <= dt.timedelta(seconds=30)
    and symbol == symbol.upper()
    and recommendation_id == f"{created_at.isoformat()}:{symbol}:{strategy}"
    and 0 < stop < entry < target_1r < target_2r
    and math.isfinite(spread_bps)
    and spread_bps >= 0
    and volume > 0
)
```

Run:

```bash
uv run pytest -q tests/test_paper_entry_source.py
```

Expected: all source tests pass and a missing path remains absent.

- [ ] **Step 6: Run static checks and commit**

```bash
uv run ruff check trading_agent/paper_entry_source.py tests/test_paper_entry_source.py
uv run ruff format --check trading_agent/paper_entry_source.py tests/test_paper_entry_source.py
uv run basedpyright trading_agent/paper_entry_source.py tests/test_paper_entry_source.py
git add trading_agent/paper_entry_source.py tests/test_paper_entry_source.py
git commit -m "feat: bind Paper entry to current ORB source"
```

### Task 2: Migrate the armed entry CLI

**Files:**
- Modify: `run_alpaca_paper_entry_smoke.py`
- Modify: `tests/test_alpaca_paper_entry_smoke.py`

- [ ] **Step 1: Write failing CLI contract tests**

Change `_arguments()` to contain only arm, execution DB, output dir, and watch DB. Update the direct help assertion to require `--watch-database` and reject `--intent-id`. Add tests proving old free-form options exit 2 and source rejection happens before credentials/session opening.

```python
def _arguments(database: Path, output: Path, watch_database: Path) -> list[str]:
    return [
        "--arm-paper-mutation",
        "ARM_ALPACA_PAPER_ONLY",
        "--database",
        str(database),
        "--output-dir",
        str(output),
        "--watch-database",
        str(watch_database),
    ]
```

Inject a source loader and clock into happy-path tests so no real watch DB or provider is used:

```python
source_loader=lambda _path, _now: request(),
clock=lambda: NOW,
```

- [ ] **Step 2: Run CLI tests and verify RED**

```bash
uv run pytest -q tests/test_alpaca_paper_entry_smoke.py
```

Expected: failures because the parser and `main()` still use free-form inputs.

- [ ] **Step 3: Replace free-form parsing with source loading**

Keep only the four required options and add injected production defaults:

```python
type SourceLoader = Callable[[Path, dt.datetime], PaperOrderAdmissionRequest]
type Clock = Callable[[], dt.datetime]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
```

In `main()`, keep the uninitialized execution-ledger check first, then call:

```python
request = source_loader(args.watch_database, clock())
```

inside the existing redacted exception boundary before credential loading and `session_opener`. Add `InvalidCurrentOrbPaperEntrySourceError` to the caught types. Delete `_aware_datetime()` and `_request()` because no production CLI path may synthesize the request.

- [ ] **Step 4: Run CLI and source tests and verify GREEN**

```bash
uv run pytest -q tests/test_paper_entry_source.py tests/test_alpaca_paper_entry_smoke.py
```

Expected: all tests pass.

- [ ] **Step 5: Run direct CLI QA**

```bash
./run_alpaca_paper_entry_smoke.py --help
./run_alpaca_paper_entry_smoke.py --arm-paper-mutation WRONG --database /tmp/no-touch.sqlite3 --output-dir /tmp/no-touch --watch-database /tmp/no-watch.sqlite3
```

Expected: help exits 0 and shows `--watch-database`; wrong arm exits 2 before source, credentials, DB creation, or network.

- [ ] **Step 6: Run static checks and commit**

```bash
uv run ruff check run_alpaca_paper_entry_smoke.py tests/test_alpaca_paper_entry_smoke.py
uv run ruff format --check run_alpaca_paper_entry_smoke.py tests/test_alpaca_paper_entry_smoke.py
uv run basedpyright run_alpaca_paper_entry_smoke.py tests/test_alpaca_paper_entry_smoke.py
git add run_alpaca_paper_entry_smoke.py tests/test_alpaca_paper_entry_smoke.py
git commit -m "feat: require source-bound Paper entry CLI"
```

### Task 3: Update the first-session operating contract

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md`
- Create: `docs/checkpoints/2026-07-15-source-bound-paper-entry-ko.md`

- [ ] **Step 1: Replace free-form entry commands**

Change README and the runbook entry invocation to:

```bash
./run_alpaca_paper_entry_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database "$PAPER_DB" \
  --output-dir "$SMOKE_RUN/06_entry" \
  --watch-database "$WATCH_RUN/paper_recommendations.sqlite3"
```

Keep the SQL query only as optional human-readable audit evidence or remove it; it must not supply mutation arguments. Document exactly-one current source selection, fixed one-share liquidity, source-before-credential order, runtime revalidation, and no actual mutation.

- [ ] **Step 2: Add checkpoint evidence**

Record source and CLI test counts, full suite count, Ruff, changed-file format, basedpyright, direct help/wrong-arm QA, absent credential file, and Alpaca Paper POST/DELETE count 0. Do not include account or broker identifiers.

- [ ] **Step 3: Run full verification**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check trading_agent/paper_entry_source.py run_alpaca_paper_entry_smoke.py tests/test_paper_entry_source.py tests/test_alpaca_paper_entry_smoke.py
uv run basedpyright
git diff --check
```

Expected: all tests pass, Ruff passes, changed files are formatted, basedpyright reports 0 errors/warnings, and diff check is empty.

- [ ] **Step 4: Commit and push**

```bash
git add README.md CODEX_START_HERE.md docs/runbooks/alpaca-paper-first-regular-session-smoke-ko.md docs/checkpoints/2026-07-15-source-bound-paper-entry-ko.md
git commit -m "docs: document source-bound Paper entry"
git push origin feature/paper-account-activities
git status --short --branch
```

Expected: local HEAD and origin branch align with a clean worktree. The next remaining gate is an actual supervised regular-session Paper smoke after credentials and all runtime prerequisites exist.
