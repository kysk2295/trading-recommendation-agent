# Global Experiment Ledger Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate append-only global experiment ledger that records current strategy lineage, experiment attempts, and next-session-effective lifecycle state without granting broker, risk, or promotion authority.

**Architecture:** Keep the lane registry, independent review ledger, and execution ledger unchanged. A new schema-v1 SQLite store owns hypotheses, strategy versions, trial registrations/events, and strategy lifecycle events; readers recompute canonical keys and state projections, while a local-only bootstrap verifies the exact lane registry scopes before importing the four current intraday research contracts as `experimental_shadow` from the next NYSE session.

**Tech Stack:** Python 3.12, Pydantic v2, stdlib `sqlite3`/`fcntl`/`hashlib`, pytest, Ruff, basedpyright, existing lane registry and US equity calendar.

---

### Task 1: Make current research lineage constants reusable

**Files:**
- Modify: `trading_agent/daily_research_contract.py`
- Modify: `trading_agent/daily_research_ledger.py`
- Create: `tests/test_daily_research_contract.py`

- [ ] **Step 1: Write the failing canonical-contract test**

Add a test proving the daily record and future global ledger can use one immutable cost/data/portfolio contract source.

```python
from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
    strategy_contract,
)
from trading_agent.strategy_factory import StrategyMode


def test_current_intraday_contracts_have_canonical_global_lineage() -> None:
    contracts = tuple(strategy_contract(mode) for mode in StrategyMode)

    assert len({contract.strategy_version for contract in contracts}) == 4
    assert CURRENT_DATA_CONTRACT == (
        "completed_bars_only=true",
        "point_in_time_candidate_inputs=true",
        "source=KIS_read_only_rankings",
    )
    assert CURRENT_COST_MODEL == (
        "side_cost_bps=5,10,20",
        "same_bar_stop_target=stop_first",
        "time_exit=last_completed_bar_fallback",
    )
    assert SHADOW_PORTFOLIO_POLICY == (
        "max_ranked_candidates=10",
        "max_one_symbol_strategy_recommendation_per_day",
        "broker_orders=false",
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest -q tests/test_daily_research_contract.py
```

Expected: import failure because the three constants do not exist.

- [ ] **Step 3: Add the constants and reuse them**

Define sorted canonical tuples in `daily_research_contract.py` with the exact values above. In `build_daily_record()`, replace the inline cost and portfolio tuples with `CURRENT_COST_MODEL` and `SHADOW_PORTFOLIO_POLICY`; keep the serialized daily record byte-for-byte equivalent for those fields.

- [ ] **Step 4: Verify GREEN and existing daily records**

```bash
uv run pytest -q tests/test_daily_research_contract.py tests/test_daily_research_record_cli.py tests/test_daily_research_lane_scope.py
uv run ruff check trading_agent/daily_research_contract.py trading_agent/daily_research_ledger.py tests/test_daily_research_contract.py
uv run basedpyright trading_agent/daily_research_contract.py trading_agent/daily_research_ledger.py tests/test_daily_research_contract.py
```

Expected: all tests pass and basedpyright reports 0 errors/warnings.

- [ ] **Step 5: Commit and push**

```bash
git add trading_agent/daily_research_contract.py trading_agent/daily_research_ledger.py tests/test_daily_research_contract.py
git commit -m "refactor: centralize strategy research lineage"
git push origin feature/paper-account-activities
```

### Task 2: Define global experiment and lifecycle models

**Files:**
- Create: `trading_agent/experiment_ledger_models.py`
- Create: `trading_agent/experiment_ledger_keys.py`
- Create: `tests/test_experiment_ledger_models.py`

- [ ] **Step 1: Write valid model and canonical-key tests**

Create fixtures for one ORB hypothesis/version, one pre-registered shadow trial, its `started` and `completed` events, and one imported lifecycle registration.

```python
def test_canonical_models_and_keys_are_stable() -> None:
    hypothesis = _hypothesis()
    version = _version(hypothesis)
    trial = _trial(version)
    started = _trial_event(trial, sequence=1, event_kind=TrialEventKind.STARTED)
    lifecycle = _lifecycle_registration(version)

    assert len(hypothesis_registration_key(hypothesis)) == 64
    assert len(strategy_version_registration_key(version)) == 64
    assert len(experiment_trial_registration_key(trial)) == 64
    assert len(experiment_trial_event_key(started)) == 64
    assert len(strategy_lifecycle_event_key(lifecycle)) == 64
    assert canonical_experiment_ledger_json(hypothesis) == canonical_experiment_ledger_json(
        HypothesisRegistration.model_validate_json(hypothesis.model_dump_json())
    )
```

- [ ] **Step 2: Write invalid-contract tests**

Parameterize tests that reject:

- malformed identifiers and non-64-character evidence hashes
- naive datetimes and `ledger_recorded_at < source_registered_at`
- unsorted or duplicate tuple fields
- hypothesis/scope/lane mismatch
- strategy version with a different hypothesis or scope
- trial with `planned_start < registered_at.date()` or `planned_end < planned_start`
- cross-lane trial using a single-lane scope
- trial event sequence 0; `started` with artifacts/reasons; `completed` without an artifact; `failed`/`censored` without a reason
- lifecycle registration with non-null `from_state` or previous key
- imported registration without `existing_contract_import` and evidence hashes
- champion/suspended/rejected as an initial state
- transition with null `from_state`, invalid closed-table transition, or non-session effective date

- [ ] **Step 3: Run the model tests and verify RED**

```bash
uv run pytest -q tests/test_experiment_ledger_models.py
```

Expected: collection failure because the model and key modules do not exist.

- [ ] **Step 4: Implement the exact model surface**

Create these enums and frozen, `extra="forbid"` Pydantic models:

```python
class TrialKind(StrEnum):
    HISTORICAL_REPLAY = "historical_replay"
    SHADOW_FORWARD = "shadow_forward"
    BROKER_PAPER_FORWARD = "broker_paper_forward"
    EQUAL_RISK_COMPARISON = "equal_risk_comparison"
    CROSS_LANE_HYPOTHESIS = "cross_lane_hypothesis"


class TrialEventKind(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CENSORED = "censored"


class StrategyLifecycleState(StrEnum):
    IDEA = "idea"
    HISTORICAL = "historical"
    EXPERIMENTAL_SHADOW = "experimental_shadow"
    EXPERIMENTAL_PAPER = "experimental_paper"
    CHALLENGER = "challenger"
    PAPER_CHAMPION = "paper_champion"
    SUSPENDED = "suspended"
    REJECTED = "rejected"


class StrategyLifecycleEventKind(StrEnum):
    REGISTRATION = "registration"
    TRANSITION = "transition"
```

Implement `HypothesisRegistration`, `StrategyVersionRegistration`, `ExperimentTrialRegistration`, `ExperimentTrialEvent`, and `StrategyLifecycleEvent` with every field and invariant in the design. Add `lifecycle_transition_allowed(from_state, to_state)` using the closed transition table. Require set-like evidence/reason/hash tuples to equal `tuple(sorted(set(values)))`; require ordered parameter/data/cost/portfolio contract tuples to contain unique, non-empty, stripped values while preserving their registered order for legacy daily-ledger compatibility.

In `experiment_ledger_keys.py`, mirror `lane_contract_keys.py` with five `NewType` keys, canonical ASCII JSON (`sort_keys=True`, compact separators), and SHA-256 over the canonical bytes.

- [ ] **Step 5: Verify GREEN and static checks**

```bash
uv run pytest -q tests/test_experiment_ledger_models.py
uv run ruff check trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py tests/test_experiment_ledger_models.py
uv run ruff format --check trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py tests/test_experiment_ledger_models.py
uv run basedpyright trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py tests/test_experiment_ledger_models.py
```

- [ ] **Step 6: Commit and push**

```bash
git add trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py tests/test_experiment_ledger_models.py
git commit -m "feat: define global experiment contracts"
git push origin feature/paper-account-activities
```

### Task 3: Add append-only registration store

**Files:**
- Create: `trading_agent/experiment_ledger_schema.py`
- Create: `trading_agent/experiment_ledger_store.py`
- Create: `tests/test_experiment_ledger_store.py`

- [ ] **Step 1: Write schema, lease, registration, and reader tests**

Write tests that create a real temporary SQLite ledger and prove:

```python
store = ExperimentLedgerStore(database)
with store.writer() as writer:
    assert writer.register_hypothesis(hypothesis) is True
    assert writer.register_hypothesis(hypothesis) is False
    assert writer.register_strategy_version(version) is True
    assert writer.register_trial(trial) is True

reader = ExperimentLedgerReader(database)
assert reader.hypotheses()[0].registration == hypothesis
assert reader.strategy_versions()[0].registration == version
assert reader.trials()[0].registration == trial
assert stat.S_IMODE(database.stat().st_mode) == 0o600
```

Also assert a second Writer fails nonblocking, inactive writers fail, UPDATE/DELETE triggers abort, missing readers return empty tuples without creating files, reader connections are query-only, unsupported versions fail, payload/key corruption is detected, foreign source mismatches fail, exact replay is idempotent, immutable identity conflicts raise typed errors, and an exception inside a Writer context rolls back every insert from that context.

- [ ] **Step 2: Run store tests and verify RED**

```bash
uv run pytest -q tests/test_experiment_ledger_store.py
```

Expected: import failure because schema/store modules do not exist.

- [ ] **Step 3: Implement schema version 1**

Create five tables with canonical payload columns and UPDATE/DELETE triggers:

```sql
hypotheses(registration_key PRIMARY KEY, hypothesis_id UNIQUE, experiment_scope_key, lane_id, payload_json)
strategy_versions(registration_key PRIMARY KEY, strategy_version UNIQUE, strategy_id, hypothesis_id, lane_id, payload_json)
experiment_trials(registration_key PRIMARY KEY, trial_id UNIQUE, strategy_version, experiment_scope_key, payload_json)
experiment_trial_events(event_key PRIMARY KEY, trial_id, sequence, event_kind, previous_event_key, payload_json, UNIQUE(trial_id, sequence))
strategy_lifecycle_events(event_key PRIMARY KEY, strategy_version, sequence, event_kind, effective_session_date, previous_event_key, payload_json, UNIQUE(strategy_version, sequence))
```

Add foreign keys from version to hypothesis, trial to version, and both event tables to their parent. Add indexes for trial events by trial/sequence, lifecycle events by version/effective date, and versions by lane.

Define `EXPERIMENT_LEDGER_SCHEMA_VERSION = 1` and these generic typed errors: `ExperimentLedgerConflictError`, `InvalidExperimentLedgerSourceError`, `ExperimentLedgerWriterLeaseUnavailableError`, `UnsupportedExperimentLedgerSchemaError`, and `InactiveExperimentLedgerWriterError`. Their `__str__` values must not include paths, payloads, keys, strategy IDs, or SQLite messages.

- [ ] **Step 4: Implement registration reader/writer APIs**

Expose the following concrete calls and return values:

```python
reader = ExperimentLedgerReader(database)
assert reader.is_initialized() is True
assert reader.hypotheses()[0].registration == hypothesis
assert reader.strategy_versions()[0].registration == version
assert reader.trials()[0].registration == trial

with ExperimentLedgerStore(database).writer() as writer:
    hypothesis_created = writer.register_hypothesis(hypothesis)
    version_created = writer.register_strategy_version(version)
    trial_created = writer.register_trial(trial)

assert (hypothesis_created, version_created, trial_created) == (False, False, False)
```

Use the same lock/permission/query-only patterns as `LaneRegistryStore`, but use experiment-specific generic error strings. After schema preparation, begin one transaction for the Writer context, commit only on clean context exit, and roll back on any exception; individual append methods do not commit. Before inserting a version, parse and verify its exact parent hypothesis; before inserting a trial, verify exact parent version, scope, and registration time.

- [ ] **Step 5: Verify GREEN and commit**

```bash
uv run pytest -q tests/test_experiment_ledger_store.py
uv run ruff check trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_store.py
uv run basedpyright trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_store.py
git add trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_store.py
git commit -m "feat: add global experiment registration ledger"
git push origin feature/paper-account-activities
```

### Task 4: Enforce event chains and lifecycle projection

**Files:**
- Modify: `trading_agent/experiment_ledger_store.py`
- Modify: `tests/test_experiment_ledger_store.py`

- [ ] **Step 1: Write failing trial event-chain tests**

After registering a trial, assert sequence 1 `started` and sequence 2 terminal completion append, exact replay returns false, and `reader.trial_events(trial_id)` returns canonical order. Add failures for sequence gap, wrong previous key, fork, event before trial registration, terminal event followed by another event, and changed payload for the same identity.

- [ ] **Step 2: Write failing lifecycle-chain and projection tests**

Register an imported `experimental_shadow` event effective on 2026-07-16 and assert:

```python
assert reader.lifecycle_state(version.strategy_version, dt.date(2026, 7, 15)) is None
assert reader.lifecycle_state(version.strategy_version, dt.date(2026, 7, 16)).event.to_state is (
    StrategyLifecycleState.EXPERIMENTAL_SHADOW
)
```

Add failures for sequence gap, wrong previous key, `from_state` mismatch, a second transition while the first is pending, invalid transition, recovery above the pre-suspension state, terminal rejected continuation, and payload/key corruption on read.

- [ ] **Step 3: Run the new tests and verify RED**

```bash
uv run pytest -q tests/test_experiment_ledger_store.py -k "event or lifecycle"
```

Expected: attribute failures because event append/read/projection APIs do not exist.

- [ ] **Step 4: Implement event append/read/projection APIs**

Add APIs that satisfy these concrete calls:

```python
with ExperimentLedgerStore(database).writer() as writer:
    assert writer.append_trial_event(started) is True
    assert writer.append_lifecycle_event(registration_event) is True

reader = ExperimentLedgerReader(database)
assert reader.trial_events(trial.trial_id)[0].event == started
assert reader.lifecycle_events(version.strategy_version)[0].event == registration_event
projected = reader.lifecycle_state(version.strategy_version, effective_date)
assert projected is not None
assert projected.event == registration_event
```

Validate the complete stored chain before every append and after every read. Reject a candidate transition when the latest stored event has `effective_session_date > candidate.decision_session_date`. For a suspended recovery, walk backward to the most recent non-suspended state and reject a target with a higher lifecycle rank. Projection filters to events effective on or before `as_of_session_date` and returns the last event.

- [ ] **Step 5: Verify GREEN, all store tests, and commit**

```bash
uv run pytest -q tests/test_experiment_ledger_models.py tests/test_experiment_ledger_store.py
uv run ruff check trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_models.py tests/test_experiment_ledger_store.py
uv run basedpyright trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_models.py tests/test_experiment_ledger_store.py
git add trading_agent/experiment_ledger_store.py tests/test_experiment_ledger_store.py
git commit -m "feat: project append-only strategy lifecycle"
git push origin feature/paper-account-activities
```

### Task 5: Bootstrap the four current intraday contracts

**Files:**
- Create: `trading_agent/experiment_ledger_bootstrap.py`
- Create: `run_experiment_ledger_bootstrap.py`
- Create: `tests/test_experiment_ledger_bootstrap.py`
- Create: `tests/test_experiment_ledger_bootstrap_cli.py`

- [ ] **Step 1: Write failing bootstrap service tests**

Seed a real `LaneRegistryStore` with `DEFAULT_LANE_MANIFESTS` and `CURRENT_INTRADAY_EXPERIMENT_SCOPES`. Assert bootstrap creates four hypotheses, four versions, and four sequence-1 lifecycle registration events, all `experimental_shadow`, all effective on the first regular session after `recorded_at` in New York.

Assert exact replay creates zero new rows and reuses the original event timestamps. Add failures for missing registry, missing/changed manifest, missing/changed scope, code version with invalid identity, naive time, and an existing conflicting global registration. For lane-source failures, assert the experiment database remains absent. For an existing global conflict, assert the database bytes/rows are preserved and no partial registration is added.

- [ ] **Step 2: Write failing CLI contract tests**

The CLI accepts only:

```text
--database
--lane-registry
--output-dir
--code-version
```

Inject `recorded_at` into `main()` for tests. Assert help exits 0, unknown options exit 2 before DB creation, a missing lane registry exits 1 with a generic Korean blocker, and a fake happy path writes a report containing only created/replayed counts and state names, not paths or keys.

- [ ] **Step 3: Run tests and verify RED**

```bash
uv run pytest -q tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py
```

Expected: import/file failures because service and CLI do not exist.

- [ ] **Step 4: Implement source verification and registrations**

Return the following frozen result from this concrete call:

```python
@dataclass(frozen=True, slots=True)
class ExperimentLedgerBootstrapResult:
    hypotheses_created: int
    versions_created: int
    lifecycle_events_created: int
    effective_session_date: dt.date


result = bootstrap_current_intraday_experiments(
    lane_registry=lane_registry,
    experiment_ledger=experiment_ledger,
    code_version="test-code",
    recorded_at=recorded_at,
)
assert isinstance(result, ExperimentLedgerBootstrapResult)
```

Read and validate the complete lane source before opening the experiment Writer. Require exact current intraday manifest and four exact scope keys/payloads. Build hypothesis/version registrations from each `StrategyResearchContract`, the canonical data/cost/shadow portfolio constants, and the source scope timestamp. Use `ledger_recorded_at=recorded_at` and one `existing_contract_import` lifecycle registration with evidence keys `(hypothesis_key, scope_key, version_key)`.

Compute the next regular session by scanning at most 10 calendar days with `regular_session_bounds`; fail closed outside the published calendar.

Expose `InvalidExperimentLedgerBootstrapSourceError` with a fixed generic Korean message for missing or changed lane contracts, invalid time/code identity, and incomplete bootstrap inputs. Preserve lower-level experiment ledger conflict/lease/schema errors as typed causes handled by the CLI's redacted boundary.

- [ ] **Step 5: Implement the local-only CLI**

Use argparse, generic typed errors, atomic report writing, and no imports from Alpaca/KIS HTTP, credentials, execution store, mutation, or Portfolio Manager modules. The CLI must verify the lane registry before creating the experiment DB.

- [ ] **Step 6: Verify service/CLI and direct QA**

```bash
uv run pytest -q tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py
./run_experiment_ledger_bootstrap.py --help
./run_experiment_ledger_bootstrap.py --database /tmp/no-experiment.sqlite3 --lane-registry /tmp/no-lane.sqlite3 --output-dir /tmp/no-experiment-report --code-version test-code
```

Expected: tests pass; help exits 0; missing source exits 1; both database paths remain absent; no credential or network call occurs.

For the actual CLI happy path, create a temporary source through the typed store and run the executable twice:

```bash
QA_ROOT="$(mktemp -d)"
uv run python - "$QA_ROOT/lane.sqlite3" <<'PY'
import sys
from pathlib import Path

from trading_agent.lane_defaults import CURRENT_INTRADAY_EXPERIMENT_SCOPES, DEFAULT_LANE_MANIFESTS
from trading_agent.lane_registry_store import LaneRegistryStore

with LaneRegistryStore(Path(sys.argv[1])).writer() as writer:
    for manifest in DEFAULT_LANE_MANIFESTS:
        writer.register_manifest(manifest)
    for scope in CURRENT_INTRADAY_EXPERIMENT_SCOPES:
        writer.register_experiment_scope(scope)
PY
./run_experiment_ledger_bootstrap.py --database "$QA_ROOT/experiment.sqlite3" --lane-registry "$QA_ROOT/lane.sqlite3" --output-dir "$QA_ROOT/report" --code-version test-code
./run_experiment_ledger_bootstrap.py --database "$QA_ROOT/experiment.sqlite3" --lane-registry "$QA_ROOT/lane.sqlite3" --output-dir "$QA_ROOT/replay" --code-version test-code
stat -f '%Lp' "$QA_ROOT/experiment.sqlite3"
```

Expected: first run reports 4/4/4 created, second run reports 0/0/0 created, database mode is `600`, and no credential or network call occurs.

- [ ] **Step 7: Static checks, commit, and push**

```bash
uv run ruff check trading_agent/experiment_ledger_bootstrap.py run_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py
uv run ruff format --check trading_agent/experiment_ledger_bootstrap.py run_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py
uv run basedpyright trading_agent/experiment_ledger_bootstrap.py run_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py
git add trading_agent/experiment_ledger_bootstrap.py run_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py
git commit -m "feat: bootstrap global experiment lineage"
git push origin feature/paper-account-activities
```

### Task 6: Document and verify the checkpoint

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-15-global-experiment-ledger-ko.md`

- [ ] **Step 1: Document the new global boundary**

State that the ledger is separate from execution/lane/review DBs, stores current four intraday contracts as next-session-effective `experimental_shadow`, preserves failed/censored trials, and does not implement the Controller, champion, order authority, risk allocation, or Portfolio Manager yet.

- [ ] **Step 2: Run complete verification**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check trading_agent/daily_research_contract.py trading_agent/daily_research_ledger.py trading_agent/experiment_ledger_models.py trading_agent/experiment_ledger_keys.py trading_agent/experiment_ledger_schema.py trading_agent/experiment_ledger_store.py trading_agent/experiment_ledger_bootstrap.py run_experiment_ledger_bootstrap.py tests/test_daily_research_contract.py tests/test_experiment_ledger_models.py tests/test_experiment_ledger_store.py tests/test_experiment_ledger_bootstrap.py tests/test_experiment_ledger_bootstrap_cli.py
uv run basedpyright
git diff --check
```

Expected: all tests pass, Ruff passes, changed Python files are formatted, basedpyright reports 0 errors/warnings, and diff check is empty.

- [ ] **Step 3: Record manual QA and safety evidence**

Record actual test counts, help/bad-input/fake-happy-path results, database mode 600, UPDATE/DELETE trigger proof, exact replay counts, absent fixed Paper credential file, absent production outputs, and external Alpaca Paper POST/DELETE count 0. Do not record account, broker, path, or raw payload identifiers.

- [ ] **Step 4: Commit, push, and verify alignment**

```bash
git add README.md CODEX_START_HERE.md docs/architecture_ko.md docs/checkpoints/2026-07-15-global-experiment-ledger-ko.md
git commit -m "docs: document global experiment ledger"
git push origin feature/paper-account-activities
git status --short --branch
git rev-list --left-right --count HEAD...origin/feature/paper-account-activities
```

Expected: clean worktree and `0 0`. The next plan connects exact `LaneReviewEvent` evidence to the Lifecycle Controller; actual regular-session Paper smoke remains separately gated on credentials and current market conditions.
