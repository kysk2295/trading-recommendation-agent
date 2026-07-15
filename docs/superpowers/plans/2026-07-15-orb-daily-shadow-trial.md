# ORB Daily Shadow Trial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind each preregistered ORB shadow-forward session to one immutable completed, censored, or failed global trial terminal event.

**Architecture:** Add a broker-free `orb_forward_trial` service over the existing lane, review, daily research, and global experiment stores. Expose register/start/finalize/fail through one redacted local CLI, then opt the existing ORB watch into those child commands without moving existing Writer ownership.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, pytest, argparse, Ruff, basedpyright

---

### Task 1: Prospective Daily Trial Contract

**Files:**
- Create: `trading_agent/orb_forward_trial.py`
- Create: `tests/test_orb_forward_trial.py`

- [ ] **Step 1: Write failing registration tests**

Cover deterministic one-session trial ID, canonical prospective data-contract digest, fixed evidence budget, exact ORB lane/global lineage, pre-open-only creation, code-version mismatch, lifecycle rejection, and post-open exact replay.

```python
result = register_orb_shadow_trial(
    lane_registry=LaneRegistryReader(registry.path),
    experiment_ledger=experiments,
    session_date=SESSION_DATE,
    runtime_code_version="test-code",
    registered_at=PREOPEN,
)
assert result.created is True
assert result.registration.planned_start == SESSION_DATE
assert result.registration.planned_end == SESSION_DATE
assert result.registration.trial_kind is TrialKind.SHADOW_FORWARD
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_orb_forward_trial.py -k registration`

Expected: import or symbol failure because `orb_forward_trial` does not exist.

- [ ] **Step 3: Implement the minimal registration service**

Add these public contracts:

```python
class InvalidOrbForwardTrialSourceError(RuntimeError): ...

@dataclass(frozen=True, slots=True)
class OrbTrialRegistrationResult:
    created: bool
    registration: ExperimentTrialRegistration

def orb_shadow_trial_id(session_date: dt.date, strategy_version: str) -> str: ...
def orb_shadow_trial_data_version() -> str: ...
def register_orb_shadow_trial(...) -> OrbTrialRegistrationResult: ...
```

Use `CURRENT_DATA_CONTRACT`, `REQUIRED_ARTIFACTS`, and `OPTIONAL_ARTIFACTS` for the canonical digest. Read lane/global stores before taking the experiment Writer lease. Preserve the original registration on replay.

- [ ] **Step 4: Verify GREEN and static checks**

Run:

```bash
uv run pytest -q tests/test_orb_forward_trial.py -k registration
uv run ruff check trading_agent/orb_forward_trial.py tests/test_orb_forward_trial.py
uv run basedpyright trading_agent/orb_forward_trial.py tests/test_orb_forward_trial.py
```

Expected: all pass with zero type errors or warnings.

- [ ] **Step 5: Commit**

```bash
git add trading_agent/orb_forward_trial.py tests/test_orb_forward_trial.py
git commit -m "feat: preregister daily ORB shadow trials"
git push origin feature/paper-account-activities
```

### Task 2: Started and Terminal Evidence

**Files:**
- Modify: `trading_agent/orb_forward_trial.py`
- Modify: `tests/test_orb_forward_trial.py`

- [ ] **Step 1: Write failing start and finalization tests**

Test start only inside `[open, close)`, start replay, terminal-before-start rejection, exact completed artifacts, data-quality-derived censor reasons, terminal replay, and conflicting terminal rejection.

```python
started = start_orb_shadow_trial(
    experiment_ledger=experiments,
    session_date=SESSION_DATE,
    started_at=OPEN + dt.timedelta(minutes=1),
)
terminal = finalize_orb_shadow_trial(
    experiment_ledger=experiments,
    lane_registry=LaneRegistryReader(registry.path),
    review_ledger=LaneReviewReader(reviews.path),
    session=session,
    session_date=SESSION_DATE,
    occurred_at=CLOSE + dt.timedelta(minutes=5),
)
assert terminal.event.event_kind is TrialEventKind.COMPLETED
assert len(terminal.event.artifact_sha256s) == 4
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_orb_forward_trial.py -k 'start or finalize or censor'`

Expected: missing start/finalize symbols.

- [ ] **Step 3: Implement start and exact completed/censored projection**

Add:

```python
@dataclass(frozen=True, slots=True)
class OrbTrialEventResult:
    created: bool
    event: ExperimentTrialEvent

def start_orb_shadow_trial(...) -> OrbTrialEventResult: ...
def finalize_orb_shadow_trial(...) -> OrbTrialEventResult: ...
```

Recompute all current daily artifacts with `load_artifacts`, compare them to the record, recompute `data_version`, parse adaptive bytes, and verify the lane review binds the same record/adaptive/snapshot. A complete clean session creates `COMPLETED`; otherwise create `CENSORED` with only the four closed reason codes from the design.

- [ ] **Step 4: Add tamper and mismatch tests**

Cover changed artifact bytes, missing parent JSONL row, daily code/evaluator/feed/parameter/cost/portfolio mismatch, adaptive hash mismatch, nonflat snapshot, review key mismatch, and post-close time mismatch. Assert no sequence-2 event is appended.

- [ ] **Step 5: Verify GREEN**

Run: `uv run pytest -q tests/test_orb_forward_trial.py`

Expected: all service tests pass.

- [ ] **Step 6: Commit**

```bash
git add trading_agent/orb_forward_trial.py tests/test_orb_forward_trial.py
git commit -m "feat: finalize daily ORB trial evidence"
git push origin feature/paper-account-activities
```

### Task 3: Audited Failed Terminal and Local CLI

**Files:**
- Modify: `trading_agent/orb_forward_trial.py`
- Create: `run_orb_forward_trial.py`
- Create: `tests/test_orb_forward_trial_cli.py`
- Modify: `tests/test_orb_forward_trial.py`

- [ ] **Step 1: Write failing audited-failure tests**

For each closed phase enum, create a cycle audit with a same-session nonzero row and assert a `FAILED` event with the audit SHA-256 and fixed reason. Reject missing, malformed, success-only, wrong-date, and unapproved phase evidence.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_orb_forward_trial.py -k fail`

Expected: missing failure API.

- [ ] **Step 3: Implement audited failure**

Add `OrbTrialFailurePhase` and `fail_orb_shadow_trial(...)`. Parse the CSV through `csv.DictReader`, validate every selected row with a frozen Pydantic model, require a same-session nonzero `failed` row, hash the raw file, and append one `FAILED` terminal after the exact started event.

- [ ] **Step 4: Write CLI RED tests**

Test executable help and absence of credential/endpoint/arm/force options, unknown option creates nothing, missing source returns 1, register/start/finalize/fail fixture paths, mode 600 report, exact replay, and redaction of paths, IDs, keys, hashes, strategy and raw reasons.

- [ ] **Step 5: Implement the CLI**

Use an argparse subparser per operation. `main(..., now=None, runtime_code_version=None)` permits test injection, while direct execution uses the current UTC clock and clean `git rev-parse HEAD`. Return 0 for evaluated success/replay and 1 for source/schema/lease/conflict errors. Reports contain only fixed aggregate fields and `external broker mutation: 0`.

- [ ] **Step 6: Verify GREEN and manual QA**

Run:

```bash
uv run pytest -q tests/test_orb_forward_trial.py tests/test_orb_forward_trial_cli.py
./run_orb_forward_trial.py --help
./run_orb_forward_trial.py register --unknown-option
```

Use fixed-clock fixtures for register/start/completed replay, censored, and audited failed. Confirm all reports are mode 600 and contain no source path or canonical key.

- [ ] **Step 7: Commit**

```bash
git add trading_agent/orb_forward_trial.py run_orb_forward_trial.py tests/test_orb_forward_trial.py tests/test_orb_forward_trial_cli.py
git commit -m "feat: operate daily ORB trial lifecycle"
git push origin feature/paper-account-activities
```

### Task 4: Opt-in Watch Integration

**Files:**
- Modify: `run_kis_paper_watch.py`
- Modify: `tests/test_kis_watch_lane_forward_validation.py`
- Create: `tests/test_kis_watch_orb_trial.py`

- [ ] **Step 1: Write failing configuration and ordering tests**

Require `--experiment-ledger` to be ORB-only and paired with all four lane paths. Assert register occurs before premarket/provider commands, start before the first regular scan, finalize after successful lane review, and each post-session failure invokes `fail` after its audit.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_kis_watch_orb_trial.py`

Expected: missing trial configuration and command builders.

- [ ] **Step 3: Implement command-only integration**

Add `OrbTrialConfig`, exact command builders, one optional Typer path, and child audits. Keep global ledger connections out of the watch process. When terminal projection itself fails, return nonzero without appending a guessed failed event.

- [ ] **Step 4: Verify focused watch and lane regressions**

Run:

```bash
uv run pytest -q tests/test_kis_watch_orb_trial.py tests/test_kis_watch_lane_forward_validation.py tests/test_orb_lane_forward_validation_cli.py
uv run ruff check run_kis_paper_watch.py tests/test_kis_watch_orb_trial.py
uv run basedpyright run_kis_paper_watch.py tests/test_kis_watch_orb_trial.py
```

- [ ] **Step 5: Commit**

```bash
git add run_kis_paper_watch.py tests/test_kis_watch_lane_forward_validation.py tests/test_kis_watch_orb_trial.py
git commit -m "feat: schedule ORB daily trial evidence"
git push origin feature/paper-account-activities
```

### Task 5: Full Verification and Checkpoint

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-15-orb-daily-shadow-trial-ko.md`

- [ ] **Step 1: Run full verification one heavy process at a time**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check trading_agent/orb_forward_trial.py run_orb_forward_trial.py run_kis_paper_watch.py tests/test_orb_forward_trial.py tests/test_orb_forward_trial_cli.py tests/test_kis_watch_orb_trial.py
uv run basedpyright
git diff --check
```

- [ ] **Step 2: Recheck safety state**

Confirm by existence only that `~/.config/trading-agent/alpaca-paper.env` and repository `outputs/` are absent. Confirm actual Alpaca Paper POST/DELETE remains 0 and fixed pilot risk limits are unchanged.

- [ ] **Step 3: Update current-state documentation**

Document per-session preregistration, exact terminal evidence, completed/censored/failed semantics, watch opt-in behavior, replay/recovery limits, local-only imports, manual QA and exact verification counts. Continue describing all strategies as Paper forward-validation candidates.

- [ ] **Step 4: Commit and push the checkpoint**

```bash
git add README.md CODEX_START_HERE.md docs/architecture_ko.md docs/checkpoints/2026-07-15-orb-daily-shadow-trial-ko.md
git commit -m "docs: document ORB daily shadow trials"
git push origin feature/paper-account-activities
```

- [ ] **Step 5: Verify final repository state**

Run `git status --short --branch` and `git rev-list --left-right --count HEAD...origin/feature/paper-account-activities`. Expected: clean worktree and `0 0`.
