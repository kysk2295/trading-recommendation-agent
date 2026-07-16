# US Swing Shadow Trial And Reviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register each forward-only US swing new-high/RVOL signal as a global shadow trial, close it only from exact swing shadow evidence, and write an authority-free independent review.

**Architecture:** Add a canonical swing research contract that matches the source-bound hypothesis card. A trial service reads the existing swing shadow ledger query-only and writes only the global experiment ledger. A separate review model/store/service reads both ledgers query-only and writes a dedicated append-only review ledger. A local CLI orchestrates one operation at a time without provider, credential, Paper, or broker imports.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, pytest, Ruff, basedpyright.

---

### Task 1: Canonical Swing Research Contract

**Files:**
- Create: `trading_agent/swing_research_contract.py`
- Create: `tests/test_swing_research_contract.py`

- [ ] **Step 1: Write the failing contract tests**

```python
from trading_agent.swing_research_contract import SWING_RESEARCH_CONTRACT


def test_swing_contract_matches_the_source_bound_hypothesis_card() -> None:
    contract = SWING_RESEARCH_CONTRACT

    assert contract.hypothesis_id == "H-SWING-NEW-HIGH-RVOL-001"
    assert contract.strategy_id == "new_high_momentum"
    assert contract.strategy_version == "new_high_rvol_20d_1p5_v1"
    assert contract.experiment_scope.primary_lane is LaneId.SWING_MOMENTUM
    assert "execution_costs=not_modeled" in contract.cost_model
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_swing_research_contract.py`

Expected: FAIL because `swing_research_contract` does not exist.

- [ ] **Step 3: Implement the immutable contract**

```python
@dataclass(frozen=True, slots=True)
class SwingResearchContract:
    hypothesis_id: str
    hypothesis: str
    falsification_rule: str
    strategy_id: str
    strategy_version: str
    experiment_scope: ExperimentScope
    parameter_set: tuple[str, ...]
    data_contract: tuple[str, ...]
    cost_model: tuple[str, ...]
    portfolio_policy: tuple[str, ...]


SWING_RESEARCH_CONTRACT = SwingResearchContract(...)
```

Use the exact scope timestamp, hypothesis text, falsification rule, lane, and strategy version in `examples/research/us-swing-new-high-rvol-v1.json` and `trading_agent/swing_new_high_rvol.py`. Keep all contract tuples non-empty, canonical, and explicit that costs are not modeled.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest -q tests/test_swing_research_contract.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/swing_research_contract.py tests/test_swing_research_contract.py
git commit -m "feat: add US swing research contract"
```

### Task 2: Global Signal-Level Shadow Trials

**Files:**
- Create: `trading_agent/swing_shadow_trial.py`
- Create: `tests/test_swing_shadow_trial.py`

- [ ] **Step 1: Write failing registration and lifecycle tests**

```python
result = register_swing_shadow_trial(
    experiment_ledger=experiments,
    shadow_ledger=SwingShadowReader(shadow.path),
    signal_id=signal.signal_id,
    runtime_code_version="test-code",
    registered_at=PREOPEN,
)

assert result.created is True
assert result.registration.trial_kind is TrialKind.SHADOW_FORWARD
assert result.registration.planned_start == signal.valid_until.astimezone(NEW_YORK).date()
assert experiments.lifecycle_events(SWING_RESEARCH_CONTRACT.strategy_version)[0].event.to_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
```

Add tests for exact research-card verification, new registration after the next open rejection, post-open replay only, code-version conflict, source-key mismatch, and no external imports.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_swing_shadow_trial.py -k registration`

Expected: FAIL because the trial service does not exist.

- [ ] **Step 3: Implement prospective registration**

```python
def swing_shadow_trial_id(signal: TradeSignalEnvelope) -> str: ...

def swing_shadow_trial_data_version(
    signal: TradeSignalEnvelope,
    created: SwingShadowEvent,
) -> str: ...

def register_swing_shadow_trial(...) -> SwingTrialRegistrationResult:
    # Validate query-only sources before one global Writer transaction.
    # Append only exact missing version, lifecycle registration, and trial.
    ...
```

Require exactly one `signal_created` event; require `registered_at` between that event's observation and the next regular open; use the request time rather than any backdated source timestamp; and atomically register version/lifecycle/trial. The lifecycle event has the signal source session as decision date and the trial planned start as effective date.

- [ ] **Step 4: Write failing start/finalize tests**

```python
started = start_swing_shadow_trial(..., started_at=REGULAR_OPEN + dt.timedelta(minutes=1))
terminal = finalize_swing_shadow_trial(..., finalized_at=TERMINAL.observed_at + dt.timedelta(minutes=1))

assert started.event.event_kind is TrialEventKind.STARTED
assert terminal.event.event_kind is TrialEventKind.COMPLETED
assert terminal.event.artifact_sha256s == swing_shadow_trial_artifact_sha256s(signal, events)
```

Cover regular-session-only start, open trial with no shadow terminal rejection, `expired` completed no-entry outcome, stopped/targeted/time-exit outcomes, tampered signal/event rejection, terminal replay, and conflicting terminal rejection.

- [ ] **Step 5: Verify RED**

Run: `uv run pytest -q tests/test_swing_shadow_trial.py -k 'start or finalize'`

Expected: FAIL because start/finalize APIs do not exist.

- [ ] **Step 6: Implement start and terminal projection**

```python
def start_swing_shadow_trial(...) -> SwingTrialEventResult: ...

def swing_shadow_trial_artifact_sha256s(
    signal: TradeSignalEnvelope,
    events: tuple[SwingShadowEvent, ...],
) -> tuple[str, ...]: ...

def finalize_swing_shadow_trial(...) -> SwingTrialEventResult: ...
```

Accept only an exact global trial whose static data-version matches canonical `signal_created` evidence. Require the global started event, an observed terminal final swing event, monotonic event sequence, and a finalization time at or after terminal observation. Append only one global `completed` event with canonical signal/event hashes. Never create `censored` or `failed` from absent source data.

- [ ] **Step 7: Verify GREEN and commit**

Run: `uv run pytest -q tests/test_swing_shadow_trial.py`

Expected: PASS.

```bash
git add trading_agent/swing_shadow_trial.py tests/test_swing_shadow_trial.py
git commit -m "feat: link US swing shadow signals to trials"
```

### Task 3: Authority-Free Independent Swing Reviewer

**Files:**
- Create: `trading_agent/swing_shadow_review_models.py`
- Create: `trading_agent/swing_shadow_review_store.py`
- Create: `trading_agent/swing_shadow_reviewer.py`
- Create: `tests/test_swing_shadow_reviewer.py`

- [ ] **Step 1: Write failing review/store tests**

```python
result = review_swing_shadow_trial(
    experiment_ledger=ExperimentLedgerReader(experiments.path),
    shadow_ledger=SwingShadowReader(shadow.path),
    reviews=SwingShadowReviewStore(review_path),
    signal_id=signal.signal_id,
    reviewed_at=AFTER_TERMINAL,
)

assert result.event.action is SwingShadowReviewerAction.CONTINUE_COLLECTION
assert result.event.automatic_state_change_allowed is False
assert result.event.order_authority_change_allowed is False
assert result.event.allocation_change_allowed is False
```

Add tests for global terminal/evidence mismatch rejection, source tampering rejection, exact replay, update/delete trigger rejection, query-only reader, second Writer failure, and mode `600` artifacts.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_swing_shadow_reviewer.py`

Expected: FAIL because the Reviewer modules do not exist.

- [ ] **Step 3: Implement review contracts and append-only store**

```python
class SwingShadowReviewerAction(StrEnum):
    CONTINUE_COLLECTION = "continue_collection"

class SwingShadowReviewEvent(BaseModel):
    signal_id: str
    trial_id: str
    terminal_event_key: str
    artifact_sha256s: tuple[str, ...]
    terminal_kind: ShadowEventKind
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False
```

Use a dedicated SQLite schema with one review table, update/delete triggers, owner-only Writer lock, and query-only Reader. Validate canonical event keys and payload hashes on every read.

- [ ] **Step 4: Implement independent review projection**

```python
def review_swing_shadow_trial(...) -> SwingShadowReviewResult:
    # Read both source ledgers query-only, then append a single review record.
    ...
```

Require a completed global terminal whose artifacts equal recomputed swing artifacts. Emit only `continue_collection`; include terminal-kind reason plus `automatic_state_change_forbidden`, `paper_authority_forbidden`, `cost_model_unmodeled`, and `forward_sample_insufficient` blockers. Do not import lifecycle, broker, provider, execution, or Paper modules.

- [ ] **Step 5: Verify GREEN and commit**

Run: `uv run pytest -q tests/test_swing_shadow_reviewer.py`

Expected: PASS.

```bash
git add trading_agent/swing_shadow_review_models.py trading_agent/swing_shadow_review_store.py trading_agent/swing_shadow_reviewer.py tests/test_swing_shadow_reviewer.py
git commit -m "feat: add US swing shadow reviewer"
```

### Task 4: Local Trial CLI

**Files:**
- Create: `run_swing_shadow_trial.py`
- Create: `tests/test_swing_shadow_trial_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
assert trial_cli.main(("--help",)) == 0
assert trial_cli.main(bad_register_arguments) == 1
assert not experiment_database.exists()
assert trial_cli.main(register_arguments, now=PREOPEN, runtime_code_version="test-code") == 0
assert trial_cli.main(start_arguments, now=OPEN) == 0
assert trial_cli.main(finalize_arguments, now=AFTER_TERMINAL) == 0
assert trial_cli.main(review_arguments, now=AFTER_REVIEW) == 0
```

Verify subcommand help, malformed source no database creation, fixture register/start/finalize/review and replay, mode-600 report/ledger/lock, redacted report, and static absence of provider/credential/broker/Paper imports.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_swing_shadow_trial_cli.py`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement four local-only operations**

```python
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace: ...

def main(
    argv: Sequence[str] | None = None,
    *,
    now: dt.datetime | None = None,
    runtime_code_version: str | None = None,
) -> int: ...
```

Expose only required local file paths, signal ID, operation, and output directory. Use current UTC time and `git rev-parse HEAD` only when tests do not inject values. Catch source/SQLite/lease errors, return exit 1, and write an atomic mode-600 redacted report with `external broker mutation: 0`.

- [ ] **Step 4: Verify GREEN and manual QA**

Run: `uv run pytest -q tests/test_swing_shadow_trial_cli.py`

Run: `uv run python run_swing_shadow_trial.py --help`

Run: `uv run python run_swing_shadow_trial.py register --experiment-ledger /tmp/missing.sqlite3 --shadow-ledger /tmp/missing-shadow.sqlite3 --signal-id missing --code-version test --output-dir /tmp/swing-trial-report`

Run the committed swing fixture through register/start/finalize/review with isolated temporary databases and injected test times.

Expected: help succeeds; bad source returns nonzero without experiment/review database; fixture path completes and exact replay adds no new rows.

- [ ] **Step 5: Commit**

```bash
git add run_swing_shadow_trial.py tests/test_swing_shadow_trial_cli.py
git commit -m "feat: add US swing shadow trial cli"
```

### Task 5: Documentation, Review, And Integration

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-16-us-swing-shadow-trial-review-ko.md`

- [ ] **Step 1: Document authority and operation boundaries**

Document the local CLI sequence and state explicitly that no provider, Paper account/order, lifecycle transition, champion, allocation, or performance claim is created.

- [ ] **Step 2: Run full verification one heavy process at a time**

Run: `uv run pytest -q`

Run: `uv run ruff check .`

Run: `uv run basedpyright`

Expected: all tests pass and static checks report zero findings.

- [ ] **Step 3: Independent code review and fixes**

Review source-lineage binding, prospective registration time, terminal artifact hashes, review authority booleans, append-only schema, and imports. Add a failing regression test before each substantive fix, then rerun the focused and full quality gates.

- [ ] **Step 4: Commit, merge, and push**

```bash
git add README.md CODEX_START_HERE.md docs/architecture_ko.md docs/checkpoints/2026-07-16-us-swing-shadow-trial-review-ko.md docs/superpowers/specs/2026-07-16-us-swing-shadow-trial-review-design.md docs/superpowers/plans/2026-07-16-us-swing-shadow-trial-review.md
git commit -m "docs: record US swing trial review checkpoint"
git checkout main
git merge --ff-only feature/us-swing-shadow-trial-review
git push origin main
```
