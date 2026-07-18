# Grok Development Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repository-local CLI that validates a bounded task contract, launches a single constrained in-place Grok worker on `main`, and reports whether its changes remain within the approved scope.

**Architecture:** `development_harness` is independent of `trading_agent` and has no provider, credential, broker, or execution imports. A strict Pydantic task contract feeds pure Git/workspace/process modules; `run_grok_task.py` is the only CLI surface. Workers edit allow-listed files in the current main working tree only—no Git worktree, branch, or clone is created.

**Tech Stack:** Python 3.12, Pydantic 2, stdlib `subprocess`/`pathlib`/`json`, pytest, Ruff, basedpyright, installed Grok CLI.

**Residual risk:** Credential, network, push, and external-write prevention is prompt/contract-only. There is no OS sandbox (`--sandbox strict` is intentionally not used) while preserving direct main and `bypassPermissions`.

---

## File Structure

- Create `development_harness/__init__.py`: declares the isolated development-harness package.
- Create `development_harness/task_contract.py`: frozen task-contract models and path/command validation with strict count/length bounds.
- Create `development_harness/grok_workspace_guard.py`: main-only root checks, linked-worktree/symlink rejection, metadata-only workspace snapshots (no content reads; frozen tuples with ctime_ns).
- Create `development_harness/grok_worker_process.py`: process-group worker launch, file-backed stdout polling, DEVNULL stderr, timeout/oversize group kill plus survivor cleanup.
- Create `development_harness/grok_worker_report.py`: bounded structuredOutput parsing with exact verification-set matching, fixed concern enum, JSON depth limits, and RecursionError handling.
- Create `development_harness/grok_task_runner.py`: Git preflight, constrained Grok command construction, offline verification re-run, and changed-path validation.
- Create `run_grok_task.py`: argparse CLI that loads one JSON contract and writes only JSON to stdout.
- Create `tests/test_development_harness_task_contract.py`: contract model tests.
- Create `tests/test_grok_task_runner.py`: temporary-Git-repository orchestration tests.
- Create `tests/test_run_grok_task_cli.py`: help, malformed contract, and dry-run CLI tests.
- Modify `pyproject.toml`: include the harness package and CLI in basedpyright coverage.
- Modify `README.md`: add a short developer-only harness section that explicitly says it is not a trading runtime and documents prompt-only residual risk without an OS sandbox.
- Create `docs/checkpoints/2026-07-18-grok-development-harness-ko.md`: tested operating contract and residual-risk note.

### Task 1: Define the Immutable Task Contract

**Files:**
- Create: `development_harness/__init__.py`
- Create: `development_harness/task_contract.py`
- Test: `tests/test_development_harness_task_contract.py`

- [x] **Step 1: Write failing contract tests** for relative paths, protected roots (including `.omo`), path/command count and length bounds, and sanitized errors.
- [x] **Step 2: Implement strict contract models** with allow-list and command bounds.
- [x] **Step 3: Verify contract tests pass**

### Task 2: Add Main-Only Preflight, Workspace Snapshot, and Scoped Worker Invocation

**Files:**
- Create: `development_harness/grok_workspace_guard.py`
- Create: `development_harness/grok_worker_process.py`
- Create: `development_harness/grok_task_runner.py`
- Test: `tests/test_grok_task_runner.py`

- [x] **Step 1: Write failing orchestration tests** using a temporary Git repository on `main`.
- [x] **Step 2: Implement fail-closed preflight** requiring `main`, rejecting linked worktrees/symlink roots, snapshotting Git refs/reflog/objects and user-owned/ignored metadata, building the in-place `bypassPermissions` command without `--sandbox`, and killing the worker process group on timeout.
- [x] **Step 3: Independently re-run required and manual QA commands offline before `completed`.**
- [x] **Step 4: Verify runner tests pass**

### Task 3: Expose a Safe CLI

**Files:**
- Create: `run_grok_task.py`
- Test: `tests/test_run_grok_task_cli.py`

- [x] **Step 1: Write failing CLI tests** for dry-run, invalid contract, help, and rejection of `--worktree-root`.
- [x] **Step 2: Implement the argparse CLI** without worktree/branch creation.
- [x] **Step 3: Verify CLI tests and manual CLI QA**

### Task 4: Document Residual Risk and Verify the Harness

**Files:**
- Modify: `README.md`
- Create/Update: `docs/checkpoints/2026-07-18-grok-development-harness-ko.md`
- Update: `docs/superpowers/specs/2026-07-18-grok-development-harness-design.md`
- Update: this plan (stale worktree language removed)

- [x] **Step 1: Document that credential/network/push/external-write prevention is prompt-only residual risk without an OS sandbox.**
- [x] **Step 2: Run focused harness verification**

```bash
uv run pytest tests/test_development_harness_task_contract.py tests/test_grok_task_runner.py tests/test_run_grok_task_cli.py -q
uv run ruff check development_harness tests run_grok_task.py
uv run basedpyright development_harness run_grok_task.py
uv run python run_grok_task.py --help
```

### Task 5: Codex Reconciliation and Main Integration

**Files:**
- Review: all worker changes only

- [ ] **Step 1: Inspect the worker diff without trusting its summary**
- [ ] **Step 2: Run Codex's independent verification**
- [ ] **Step 3: Integrate only accepted work on main after review**

Do not stage `.hermes/` or `.omo/`. Do not create a worktree for later tasks.

## Plan Self-Review

- Scope coverage: contract validation, main-only launch, workspace snapshot, process-group timeout, changed-path checking, offline re-run, CLI, TDD, documentation, and independent review each have an implementation task.
- Stale worktree language removed: workers run in-place on main only.
- Residual risk explicit: no OS sandbox; prompt/contract residual risk for credential/network/push/external writes.
- Bootstrap boundary: harness implementation itself is complete; later feature work uses this CLI.
