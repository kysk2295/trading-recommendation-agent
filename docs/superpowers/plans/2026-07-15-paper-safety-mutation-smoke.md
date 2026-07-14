# Alpaca Paper Safety Mutation Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose cancel and exact-position EOD flatten only through an explicitly armed, current-epoch Alpaca Paper smoke CLI while making the operating-session arm requirement impossible to bypass.

**Architecture:** Keep broker mutation methods private behind the existing single-Writer `PaperOperatingSession`. Require and runtime-validate `PaperMutationArm` on every public entry, protective OCO, and safety mutation method; the new CLI then invokes `execute_safety_actions` with the reduced smoke risk contract and renders only non-secret outcome summaries. A safety plan containing cancels and closes executes only the cancel stage, reconciles current-epoch broker state, and allows a later close-only plan to use the new exact position. The plan-producing REST snapshot must also pass a pre-mutation order/position/symbol/notional scope check, and a barrier change after any broker call raises a distinct post-mutation reconciliation error.

**Tech Stack:** Python 3.12, dataclasses and protocols, SQLite append-only execution ledger, httpx2 Alpaca Paper adapter, pytest, Ruff, basedpyright.

---

### Task 1: Require Explicit Arm At Every Operating-Session Mutation Boundary

**Files:**
- Modify: `tests/test_paper_operating_mutation_execution.py`
- Modify: `tests/test_paper_protective_oco_smoke_cli.py`
- Modify: `trading_agent/paper_mutation_arm.py`
- Modify: `trading_agent/paper_operating_session_models.py`
- Modify: `trading_agent/paper_operating_session.py`
- Modify: `run_alpaca_paper_protective_oco_smoke.py`

- [x] **Step 1: Write failing signature and call-through tests**

Add `PaperMutationArm(PAPER_MUTATION_ARM_VALUE)` to the safety and protective OCO session calls. Update the protective CLI fake session to capture the arm and assert that it receives the exact validated value:

```python
def execute_protective_oco(
    self,
    parent_intent_id: IntentId,
    arm: PaperMutationArm,
) -> PaperProtectiveMutationExecution | BlockedProtectiveExitPlan | NoProtectiveExitRequired:
    self.calls.append((parent_intent_id, arm))
    return self.result

assert calls == [(intent().intent_id, PaperMutationArm(PAPER_MUTATION_ARM_VALUE))]
```

- [x] **Step 2: Run tests and verify the new calls fail**

Run:

```bash
uv run pytest -q tests/test_paper_operating_mutation_execution.py tests/test_paper_protective_oco_smoke_cli.py
```

Expected: FAIL because `execute_safety_actions` and `execute_protective_oco` do not yet accept an arm, and the CLI does not pass one.

- [x] **Step 3: Add the required arm parameters**

Use these public signatures in both the protocol and live implementation:

```python
def execute_safety_actions(
    self,
    arm: PaperMutationArm,
    config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
) -> PaperSafetyMutationExecution | BlockedPaperSafetyPlan:
    _ = arm
    with self._exclusive_operation():
        return self._mutations.execute_safety(config)

def execute_protective_oco(
    self,
    parent_intent_id: IntentId,
    arm: PaperMutationArm,
) -> PaperProtectiveMutationExecution | NoProtectiveExitRequired | BlockedProtectiveExitPlan:
    _ = arm
    with self._exclusive_operation():
        return self._mutations.execute_protection(parent_intent_id)
```

Construct the arm once in `run_alpaca_paper_protective_oco_smoke.py` and pass it into the session call.

Runtime-validate the arm before active-session state is touched:

```python
def require_paper_mutation_arm(value: object) -> PaperMutationArm:
    if not isinstance(value, PaperMutationArm) or value.value != PAPER_MUTATION_ARM_VALUE:
        raise InvalidPaperMutationArmError
    return value
```

- [x] **Step 4: Run targeted tests and static checks**

Run:

```bash
uv run pytest -q tests/test_paper_operating_mutation_execution.py tests/test_paper_protective_oco_smoke_cli.py
uv run ruff check trading_agent/paper_operating_session.py trading_agent/paper_operating_session_models.py run_alpaca_paper_protective_oco_smoke.py tests/test_paper_operating_mutation_execution.py tests/test_paper_protective_oco_smoke_cli.py
uv run basedpyright trading_agent/paper_operating_session.py trading_agent/paper_operating_session_models.py run_alpaca_paper_protective_oco_smoke.py tests/test_paper_operating_mutation_execution.py tests/test_paper_protective_oco_smoke_cli.py
```

Expected: all commands exit 0.

### Task 2: Add The Armed Safety Mutation Smoke CLI

**Files:**
- Create: `tests/test_alpaca_paper_safety_mutation_smoke.py`
- Create: `run_alpaca_paper_safety_mutation_smoke.py`
- Modify: `tests/test_paper_safety_mutation_executor.py`
- Modify: `trading_agent/paper_mutation_executor.py`

- [x] **Step 1: Write failing CLI tests**

Cover four externally visible behaviors:

```python
def test_safety_mutation_smoke_requires_initialized_ledger(tmp_path: Path) -> None:
    assert cli.main(_arguments(tmp_path / "missing.sqlite3", tmp_path / "out")) == 1

def test_wrong_arm_fails_before_credentials(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.main(_arguments(tmp_path / "db", tmp_path / "out", arm="WRONG"))
    assert captured.value.code == 2

def test_safety_mutation_smoke_executes_reduced_risk_plan_and_reports_ack(tmp_path: Path) -> None:
    assert cli.main(arguments, credential_loader=fake_credentials, session_opener=fake_opener) == 0
    assert session.config.daily_loss_limit_dollars == 30.0
    assert "결과: acknowledged" in report

def test_safety_mutation_smoke_returns_two_for_ambiguous_result(tmp_path: Path) -> None:
    assert cli.main(arguments, credential_loader=fake_credentials, session_opener=fake_opener) == 2
```

- [x] **Step 2: Run the test and verify import failure**

Run:

```bash
uv run pytest -q tests/test_alpaca_paper_safety_mutation_smoke.py
```

Expected: collection ERROR with `ModuleNotFoundError: run_alpaca_paper_safety_mutation_smoke`.

- [x] **Step 3: Implement the minimal CLI**

The CLI must:

```python
SMOKE_RISK_CONFIG = PaperRiskConfig(
    max_risk_dollars=10.0,
    risk_fraction=0.0003333333333333333,
    max_notional_dollars=100.0,
    max_open_positions=1,
    daily_loss_limit_dollars=30.0,
    per_side_cost_bps=20.0,
)

parser.add_argument("--arm-paper-mutation", required=True, choices=(PAPER_MUTATION_ARM_VALUE,))
parser.add_argument("--database", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, required=True)

arm = PaperMutationArm(args.arm_paper_mutation)
with session_opener(credential_loader(), store) as session:
    result = session.execute_safety_actions(arm, SMOKE_RISK_CONFIG)
```

Return 1 for a blocked plan, 0 for no actions or all `ACKNOWLEDGED`/`ALREADY_ACKNOWLEDGED` results, and 2 for rejected, ambiguous, or caught execution errors. Write `paper_safety_mutation_smoke_ko.md` atomically without account identifiers, order IDs, request IDs, credentials, or raw broker payloads.

If the plan contains any cancel action, stop before its first close action. The operating session must reconcile the cancel stage before a later close-only plan can submit an exact-position close.

- [x] **Step 4: Run targeted CLI tests and checks**

Run:

```bash
uv run pytest -q tests/test_alpaca_paper_safety_mutation_smoke.py
uv run ruff check run_alpaca_paper_safety_mutation_smoke.py tests/test_alpaca_paper_safety_mutation_smoke.py
uv run basedpyright run_alpaca_paper_safety_mutation_smoke.py tests/test_alpaca_paper_safety_mutation_smoke.py
```

Expected: all commands exit 0.

### Task 3: Document The Safety Mutation Checkpoint

**Files:**
- Create: `docs/checkpoints/2026-07-15-paper-safety-mutation-smoke-cli-ko.md`
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`

- [x] **Step 1: Document the exact invocation and safety boundary**

Add this command and state that it may issue Paper-only DELETE calls after current-epoch reconciliation:

```bash
./run_alpaca_paper_safety_mutation_smoke.py \
  --arm-paper-mutation ARM_ALPACA_PAPER_ONLY \
  --database outputs/paper_execution/paper_execution.sqlite3 \
  --output-dir outputs/paper_execution/safety_mutation_smoke/latest
```

Document the fixed smoke limits, result exit codes, broker/shadow reconciliation requirement, and that the closed-market fixture QA performs no POST/DELETE.

- [x] **Step 2: Update the next priority**

Keep the first real-session sequence explicit: reduced entry, immediate protective OCO, WSS/REST/Account Activities reconciliation, armed safety mutation at cutoff/EOD, and final zero-order/zero-position reconciliation. Keep OCO resize/cancel-replace as the next implementation checkpoint.

### Task 4: Verify, Commit, And Push The Checkpoint

**Files:**
- Verify all changed files and the complete repository.

- [x] **Step 1: Run focused and full verification**

Run:

```bash
uv run pytest -q tests/test_paper_operating_mutation_execution.py tests/test_paper_protective_oco_smoke_cli.py tests/test_alpaca_paper_safety_mutation_smoke.py tests/test_paper_safety_mutation_executor.py tests/test_paper_safety_mutation_scope.py
uv run pytest -q
uv run ruff check .
uv run ruff format --check <15 changed Python files>
uv run basedpyright
```

Expected: all commands exit 0 with no failures, lint errors, type errors, or warnings.

- [x] **Step 2: Perform manual CLI QA without broker mutation**

Run `--help`, a wrong arm, and a fixture-backed acknowledged path. Confirm that the market is closed before deciding not to run the real CLI, and record that actual Paper POST/DELETE remains unchanged.

- [x] **Step 3: Inspect the final diff and commit**

Run:

```bash
git diff --check
git status --short
git diff --stat
git add CODEX_START_HERE.md README.md docs/checkpoints/2026-07-15-paper-safety-mutation-smoke-cli-ko.md docs/superpowers/plans/2026-07-15-paper-safety-mutation-smoke.md run_alpaca_paper_entry_smoke.py run_alpaca_paper_protective_oco_smoke.py run_alpaca_paper_safety_mutation_smoke.py tests/test_alpaca_paper_safety_mutation_smoke.py tests/test_paper_operating_mutation_execution.py tests/test_paper_protective_oco_smoke_cli.py tests/test_paper_safety_mutation_executor.py tests/test_paper_safety_mutation_scope.py trading_agent/paper_mutation_arm.py trading_agent/paper_mutation_executor.py trading_agent/paper_operating_mutation_execution.py trading_agent/paper_operating_session.py trading_agent/paper_operating_session_models.py trading_agent/paper_runtime_session.py trading_agent/paper_safety_mutation_scope.py
git commit -m "feat: add armed Paper safety mutation smoke"
git push origin feature/paper-account-activities
```

- [x] **Step 4: Verify remote alignment**

Run:

```bash
git status --short --branch
git rev-list --left-right --count HEAD...origin/feature/paper-account-activities
```

Expected: clean worktree and `0 0` divergence.
