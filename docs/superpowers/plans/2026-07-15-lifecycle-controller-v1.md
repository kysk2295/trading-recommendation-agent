# Lifecycle Controller v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only deterministic Controller that can append only an exact mature-degradation `suspended` lifecycle transition while leaving promotion, recovery, champion, order authority, and risk allocation closed.

**Architecture:** Reuse the existing lane registry, lane review ledger, and global experiment ledger without schema changes. The Controller revalidates canonical ORB lineage and three immutable evidence keys through query-only readers, then uses the existing experiment single Writer to append a next-session event; a separate CLI emits only redacted aggregate policy results.

**Tech Stack:** Python 3.12, Pydantic v2 existing models, stdlib dataclasses/enum/argparse/tempfile, SQLite readers and experiment Writer, pytest, Ruff, basedpyright.

---

### Task 1: Share the current Reviewer version and define Controller behavior tests

**Files:**
- Modify: `trading_agent/lane_review_models.py`
- Modify: `trading_agent/lane_reviewer.py`
- Create: `tests/test_lifecycle_controller.py`

- [ ] **Step 1: Move the Reviewer version to the review contract module**

Add this shared constant and keep the old public alias for compatibility:

```python
# trading_agent/lane_review_models.py
from typing import Final, Literal, Self

CURRENT_LANE_REVIEWER_VERSION: Final = "lane_reviewer_v1"

# trading_agent/lane_reviewer.py
from trading_agent.lane_review_models import CURRENT_LANE_REVIEWER_VERSION

LANE_REVIEWER_VERSION: Final = CURRENT_LANE_REVIEWER_VERSION
```

- [ ] **Step 2: Write failing Controller service tests**

Create real temporary lane/review/experiment SQLite stores. Seed `DEFAULT_LANE_MANIFESTS`, `CURRENT_INTRADAY_EXPERIMENT_SCOPES`, bootstrap the four current experiments on 2026-07-14, append a flat finalized ORB snapshot for 2026-07-15, and append its exact review event.

Tests must prove these concrete calls:

```python
result = control_intraday_orb_lifecycle(
    lane_registry=LaneRegistryReader(lane_path),
    review_ledger=LaneReviewReader(review_path),
    experiment_ledger=ExperimentLedgerStore(experiment_path),
    session_date=dt.date(2026, 7, 15),
    decided_at=dt.datetime(2026, 7, 15, 20, 30, tzinfo=dt.UTC),
)

assert result.outcome is LifecycleControllerOutcome.TRANSITIONED
assert result.created is True
assert result.from_state is StrategyLifecycleState.EXPERIMENTAL_SHADOW
assert result.to_state is StrategyLifecycleState.SUSPENDED
assert result.event is not None
assert result.event.effective_session_date == dt.date(2026, 7, 16)
```

Add tests for:

- exact replay with a later invocation time returns the original event and `created=False`
- collecting/shadow-continue/diagnose create no lifecycle row
- early-stop, comparison-ready, and promotion-review return fixed blockers and create no row
- a dirty/incomplete snapshot cannot create suspension
- suspend without `five_day_clear_degradation` or with mismatched Reviewer action is invalid source
- changed snapshot/review key, strategy/scope/date/evaluator lineage, review before finalization, review after Controller time, and naive Controller time fail closed
- a future-effective pending lifecycle event blocks a second decision
- already suspended/rejected states do not append another event

- [ ] **Step 3: Run the tests and verify RED**

```bash
uv run pytest -q tests/test_lifecycle_controller.py
```

Expected: collection fails because `trading_agent.lifecycle_controller` does not exist.

- [ ] **Step 4: Verify the Reviewer refactor has no regression**

```bash
uv run pytest -q tests/test_lane_review_models.py tests/test_lane_reviewer.py tests/test_lane_reviewer_cli.py
```

Expected: all existing Reviewer tests pass unchanged.

- [ ] **Step 5: Commit the shared contract and RED tests only after the service implementation in Task 2**

Do not commit a branch that leaves test collection broken; Task 1 and Task 2 form one implementation checkpoint.

### Task 2: Implement the deterministic Controller service

**Files:**
- Create: `trading_agent/lifecycle_controller.py`
- Modify: `tests/test_lifecycle_controller.py`

- [ ] **Step 1: Define the exact public result surface**

```python
class LifecycleControllerOutcome(StrEnum):
    NO_CHANGE = "no_change"
    BLOCKED = "blocked"
    TRANSITIONED = "transitioned"


@dataclass(frozen=True, slots=True)
class LifecycleControllerResult:
    outcome: LifecycleControllerOutcome
    created: bool
    session_date: dt.date
    from_state: StrategyLifecycleState
    to_state: StrategyLifecycleState | None
    reason_codes: tuple[str, ...]
    blockers: tuple[str, ...]
    event: StrategyLifecycleEvent | None


class InvalidLifecycleControllerSourceError(RuntimeError):
    def __str__(self) -> str:
        return "Lifecycle Controller가 exact immutable evidence를 확인하지 못했습니다"
```

- [ ] **Step 2: Implement source revalidation before any Writer opens**

Expose this service:

```python
def control_intraday_orb_lifecycle(
    *,
    lane_registry: LaneRegistryReader,
    review_ledger: LaneReviewReader,
    experiment_ledger: ExperimentLedgerStore,
    session_date: dt.date,
    decided_at: dt.datetime,
) -> LifecycleControllerResult:
    ...
```

Recompute `lane_manifest_key`, `experiment_scope_key`, `lane_daily_snapshot_key`, and `lane_review_event_key`. Verify canonical intraday manifest/scope, flat snapshot, ORB strategy contract, shared evaluator/reviewer versions, exact timestamps, global hypothesis/version fields, current lifecycle projection, and absence of a future-effective event. Catch lane/review parsing and SQLite source failures and raise only the fixed Controller error; preserve experiment conflict/lease/schema errors at the append boundary.

- [ ] **Step 3: Implement the closed v1 decision table**

Use only fixed safe blocker codes:

```python
PROMOTION_BLOCKERS = (
    "broker_shadow_promotion_evidence_missing",
    "dsr_pbo_evidence_missing",
    "parameter_plateau_evidence_missing",
    "sip_validation_evidence_missing",
)
```

`collecting` and `shadow_continue` return `NO_CHANGE`; `diagnose` returns `NO_CHANGE` with `diagnosis_required`; early-stop and comparison-ready return `BLOCKED`; promotion-review returns `BLOCKED` with all four blockers. Do not copy arbitrary Reviewer blocker strings into the Controller result or report.

- [ ] **Step 4: Append only the exact suspend event**

For clean `AdaptiveAction.SUSPEND` plus `LaneReviewerAction.STOP_RECOMMENDED` and reason `five_day_clear_degradation`, build:

```python
StrategyLifecycleEvent(
    strategy_version=orb_version,
    sequence=latest.event.sequence + 1,
    event_kind=StrategyLifecycleEventKind.TRANSITION,
    from_state=latest.event.to_state,
    to_state=StrategyLifecycleState.SUSPENDED,
    policy_version="lifecycle_controller_v1",
    decision_session_date=session_date,
    effective_session_date=next_regular_session,
    decided_at=decided_at,
    evidence_keys=tuple(sorted((str(latest.event_key), str(review_key), str(snapshot_key)))),
    reason_codes=("five_day_clear_degradation", "review_evidence_verified"),
    previous_event_key=latest.event_key,
)
```

Before constructing a new candidate, scan validated lifecycle events for the same strategy/policy/session. Exact evidence returns the stored event with `created=False`; any mismatch raises the fixed source error. Use `ExperimentLedgerStore.writer().append_lifecycle_event()` only after all source checks pass.

- [ ] **Step 5: Run GREEN and static checks**

```bash
uv run pytest -q tests/test_lifecycle_controller.py tests/test_lane_review_models.py tests/test_lane_reviewer.py tests/test_lane_reviewer_cli.py tests/test_experiment_ledger_store.py
uv run ruff check trading_agent/lane_review_models.py trading_agent/lane_reviewer.py trading_agent/lifecycle_controller.py tests/test_lifecycle_controller.py
uv run ruff format --check trading_agent/lane_review_models.py trading_agent/lane_reviewer.py trading_agent/lifecycle_controller.py tests/test_lifecycle_controller.py
uv run basedpyright trading_agent/lane_review_models.py trading_agent/lane_reviewer.py trading_agent/lifecycle_controller.py tests/test_lifecycle_controller.py
```

- [ ] **Step 6: Commit and push**

```bash
git add trading_agent/lane_review_models.py trading_agent/lane_reviewer.py trading_agent/lifecycle_controller.py tests/test_lifecycle_controller.py
git commit -m "feat: control evidence-bound lifecycle suspension"
git push origin feature/paper-account-activities
```

### Task 3: Add the redacted local-only Controller CLI

**Files:**
- Create: `run_lifecycle_controller.py`
- Create: `tests/test_lifecycle_controller_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Tests must assert:

- executable `--help` exits 0 and exposes only `--experiment-ledger`, `--lane-registry`, `--review-ledger`, `--session-date`, and `--output-dir`
- an unknown option exits 2 before DB/output creation
- missing local sources exit 1 and write a generic report without any supplied path
- fixture collecting returns exit 0 and `outcome: no_change`
- fixture suspend creates one transition; replay exits 0 with `created: false`
- promotion-review exits 0 with the four fixed policy blockers and no transition
- reports contain no DB path, canonical key/hash, strategy version, account/broker identifier, raw Reviewer reason, credential, or endpoint

- [ ] **Step 2: Run CLI tests and verify RED**

```bash
uv run pytest -q tests/test_lifecycle_controller_cli.py
```

Expected: import/file failure because the CLI does not exist.

- [ ] **Step 3: Implement the CLI and atomic report**

Use `argparse`, `datetime.date.fromisoformat`, an injected `decided_at` in `main()` for tests, and `tempfile.NamedTemporaryFile` plus `Path.replace` for the report. The uv script dependency list is only `pydantic>=2.11`; do not include network libraries.

Return 0 for successfully evaluated `NO_CHANGE`, `BLOCKED`, and `TRANSITIONED`. Return 1 only for typed source/schema/conflict/lease/SQLite/OSError failures. Report only:

```text
result: completed|blocked_source
outcome: no_change|blocked|transitioned
created: true|false
from_state: ...
to_state: none|suspended
policy_blockers: comma-separated fixed codes or none
external broker mutation: 0
```

- [ ] **Step 4: Verify CLI and direct QA**

```bash
uv run pytest -q tests/test_lifecycle_controller_cli.py tests/test_lifecycle_controller.py
./run_lifecycle_controller.py --help
./run_lifecycle_controller.py --experiment-ledger /tmp/missing-experiment.sqlite3 --lane-registry /tmp/missing-lane.sqlite3 --review-ledger /tmp/missing-review.sqlite3 --session-date 2026-07-15 --output-dir /tmp/controller-missing
```

Expected: tests pass; help exits 0; missing source exits 1; all three DBs remain absent; no credential or network call occurs.

- [ ] **Step 5: Static checks, commit, and push**

```bash
uv run ruff check run_lifecycle_controller.py trading_agent/lifecycle_controller.py tests/test_lifecycle_controller_cli.py tests/test_lifecycle_controller.py
uv run ruff format --check run_lifecycle_controller.py trading_agent/lifecycle_controller.py tests/test_lifecycle_controller_cli.py tests/test_lifecycle_controller.py
uv run basedpyright run_lifecycle_controller.py trading_agent/lifecycle_controller.py tests/test_lifecycle_controller_cli.py tests/test_lifecycle_controller.py
git add run_lifecycle_controller.py tests/test_lifecycle_controller_cli.py
git commit -m "feat: add local lifecycle controller CLI"
git push origin feature/paper-account-activities
```

### Task 4: Document and verify the Controller checkpoint

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-15-lifecycle-controller-v1-ko.md`

- [ ] **Step 1: Document what v1 can and cannot transition**

State that exact mature-window degradation can suspend ORB from an eligible active lifecycle state on the next session, while early reject, challenger, promotion, recovery, champion, order authority, risk allocation, and Portfolio Manager remain closed. Describe `run_lifecycle_controller.py` as local-only and redacted.

- [ ] **Step 2: Run complete verification**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check trading_agent/lane_review_models.py trading_agent/lane_reviewer.py trading_agent/lifecycle_controller.py run_lifecycle_controller.py tests/test_lifecycle_controller.py tests/test_lifecycle_controller_cli.py
uv run basedpyright
git diff --check
```

- [ ] **Step 3: Record safety evidence**

Record actual test counts, help/unknown/missing/fake suspend/replay/promotion-blocked QA, mode 600 ledgers, absent fixed Paper credential file, absent production outputs, unchanged intraday pilot limits, and Alpaca Paper POST/DELETE 0. Do not record paths, keys, hashes, strategy/account/broker identifiers, or raw payloads.

- [ ] **Step 4: Commit, push, and verify alignment**

```bash
git add README.md CODEX_START_HERE.md docs/architecture_ko.md docs/checkpoints/2026-07-15-lifecycle-controller-v1-ko.md docs/superpowers/specs/2026-07-15-lifecycle-controller-v1-design.md docs/superpowers/plans/2026-07-15-lifecycle-controller-v1.md
git commit -m "docs: document lifecycle controller v1"
git push origin feature/paper-account-activities
git status --short --branch
git rev-list --left-right --count HEAD...origin/feature/paper-account-activities
```

Expected: clean worktree, `0 0`, and no broker mutation. The next milestone registers ORB daily forward trials and terminal evidence before considering any challenger transition.
