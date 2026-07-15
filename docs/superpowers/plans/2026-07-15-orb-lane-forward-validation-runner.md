# ORB Lane Forward-Validation Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the finalized intraday snapshot and independent Reviewer in one fail-closed post-session sequence without adding order authority, broker mutation, or automatic promotion.

**Architecture:** Add one thin executable that composes the existing `run_intraday_lane_daily_snapshot.py` and `run_lane_reviewer.py` commands. It records a separate append-only audit row for each attempted phase, never starts Reviewer after a failed snapshot, and writes one redacted aggregate report. The child CLIs remain the owners of source validation, Paper GET/WSS readiness, immutable snapshot/review append, and exact replay.

**Tech Stack:** Python 3.12, argparse, subprocess, existing SQLite-backed lane CLIs, pytest, Ruff, basedpyright.

**Design source:** `docs/superpowers/specs/2026-07-15-orb-lane-daily-review-loop-design.md`

---

### Task 1: Specify the fail-closed orchestration contract

**Files:**
- Create: `tests/test_orb_lane_forward_validation_cli.py`
- Create: `run_orb_lane_forward_validation.py`

- [ ] **Step 1: Write failing command-construction tests**

Test that snapshot receives only session/date/execution DB/lane registry/output arguments, Reviewer receives only session/date/lane registry/review ledger/output arguments, and neither command contains an arm option, live endpoint, credential value, or mutation script.

- [ ] **Step 2: Write failing phase-order tests**

Inject a callable runner and assert:

```python
snapshot failure -> calls == ("snapshot",), reviewer_exit_code is None
snapshot success + Reviewer success -> calls == ("snapshot", "reviewer")
snapshot success + Reviewer failure -> both audits exist and aggregate result is blocked
```

Use separate audit files named `post_session_intraday_snapshot_cycles.csv` and `post_session_lane_reviewer_cycles.csv`.

- [ ] **Step 3: Run the new tests and verify RED**

Run: `uv run pytest -q tests/test_orb_lane_forward_validation_cli.py`

Expected: collection fails because `run_orb_lane_forward_validation.py` does not exist.

### Task 2: Implement the thin post-session runner

**Files:**
- Create: `run_orb_lane_forward_validation.py`
- Modify: `pyproject.toml`
- Test: `tests/test_orb_lane_forward_validation_cli.py`

- [ ] **Step 1: Implement immutable path and result models**

Define frozen dataclasses `LaneForwardValidationPaths` and `LaneForwardValidationResult`. The paths model owns the session, execution DB, lane registry, review ledger, and output root. The result exposes exact child exit codes and derives `completed` only when both are zero.

- [ ] **Step 2: Implement exact child command builders**

Build commands from `Path(__file__).with_name(...)`. Snapshot output goes to `output/snapshots/YYYY-MM-DD`; review output goes to `output/reviews/YYYY-MM-DD`. Do not accept or forward credential, endpoint, arm, fixture, or force options.

- [ ] **Step 3: Implement ordered execution and audit**

Run snapshot through the injected runner first. Return immediately on any nonzero result. Run Reviewer only after snapshot returns zero. Each attempted child uses its own audit CSV through the existing `append_cycle_audit` pattern.

- [ ] **Step 4: Implement argparse and redacted aggregate report**

Require:

```text
session --session-date --execution-database --lane-registry --review-ledger --output-dir
```

The report may contain only lane, date, aggregate status, phase status, authority-denied statements, and external mutation count zero. It must omit paths, hashes, keys, account data, credentials, endpoints, broker IDs, and raw payloads. Return 0 only for both successful phases, 1 for a child/runtime block, and 2 when the aggregate report cannot be written.

- [ ] **Step 5: Add static type coverage and run focused GREEN**

Add `run_orb_lane_forward_validation.py` to basedpyright include and run:

```bash
uv run pytest -q tests/test_orb_lane_forward_validation_cli.py tests/test_intraday_lane_daily_snapshot_cli.py tests/test_lane_reviewer_cli.py
uv run ruff check run_orb_lane_forward_validation.py tests/test_orb_lane_forward_validation_cli.py
uv run basedpyright
```

Expected: zero failures, findings, errors, or warnings.

### Task 3: Verify replay, local blocking, and documentation

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-15-orb-lane-forward-validation-runner-ko.md`
- Modify: `docs/superpowers/plans/2026-07-15-orb-lane-forward-validation-runner.md`

- [ ] **Step 1: Add CLI tests for help, malformed date, runtime failure, and report redaction**

Assert help exits 0, malformed date exits 2, a missing local source blocks without Reviewer, exact replay invokes the same two commands in the same order, and secret-looking path segments never appear in the aggregate report.

- [ ] **Step 2: Run executable manual QA without credentials or broker network**

Run help and missing-local-source paths. Then inject fake child results for success/replay/snapshot failure/Reviewer failure and inspect audit counts plus aggregate report redaction. Do not call Alpaca POST/DELETE.

- [ ] **Step 3: Document the operating boundary**

Document that this runner is the D-stage sequence boundary, not a scheduler or order engine; snapshot remains GET/WSS-only, Reviewer remains local/query-only, exact replay does not duplicate immutable rows, automatic champion/promotion/allocation remains absent, and actual Paper mutation remains zero.

- [ ] **Step 4: Run full verification one heavy command at a time**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run ruff format --check run_orb_lane_forward_validation.py tests/test_orb_lane_forward_validation_cli.py
git diff --check
```

Expected: zero failures, Ruff findings, type errors/warnings, formatting drift, or diff errors.

- [ ] **Step 5: Commit, push, and verify origin alignment**

```bash
git add README.md CODEX_START_HERE.md docs/architecture_ko.md docs/checkpoints/2026-07-15-orb-lane-forward-validation-runner-ko.md docs/superpowers/plans/2026-07-15-orb-lane-forward-validation-runner.md pyproject.toml run_orb_lane_forward_validation.py tests/test_orb_lane_forward_validation_cli.py
git commit -m "feat: orchestrate ORB lane daily validation"
git push origin feature/paper-account-activities
git rev-list --left-right --count HEAD...origin/feature/paper-account-activities
```

Expected: `0 0`, a clean worktree, no new order authority, and external Alpaca POST/DELETE count still zero.
