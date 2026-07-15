# Scheduled ORB Forward-Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the existing post-session metrics, exact daily record, adaptive evaluation, intraday snapshot, and independent Reviewer into one opt-in fail-closed ORB watch sequence.

**Architecture:** Keep research watch and execution lane ownership separate by invoking the existing `run_orb_lane_forward_validation.py` only as a subprocess after all three research children succeed. Four lane paths are all-or-none configuration, ORB-only, and validated before market waiting or provider access. The lane child retains its own local-preflight, fixed Paper credential, GET/WSS, immutable snapshot, query-only Reviewer, and no-mutation contracts.

**Tech Stack:** Python 3.12, Typer, subprocess, existing audit CSV helpers, pytest, Ruff, basedpyright.

**Design sources:** `docs/superpowers/specs/2026-07-15-lane-control-plane-contracts-design.md`, `docs/superpowers/specs/2026-07-15-orb-lane-daily-review-loop-design.md`

---

### Task 1: Lock the scheduled sequence contract with RED tests

**Files:**
- Create: `tests/test_kis_watch_lane_forward_validation.py`
- Modify: `tests/test_kis_watch_metrics.py`

- [ ] **Step 1: Write failing all-or-none configuration tests**

Assert no lane flags returns `None`, all four paths return an immutable `LaneForwardValidationConfig`, any partial combination raises `typer.BadParameter`, and a complete configuration with a non-ORB strategy raises before any subprocess or provider call.

- [ ] **Step 2: Write failing exact command tests**

Assert the child command contains only:

```text
run_orb_lane_forward_validation.py SESSION
--session-date YYYY-MM-DD
--execution-database PATH
--lane-registry PATH
--review-ledger PATH
--output-dir PATH
```

Reject arm, credential, endpoint, force, fixture, mutation-smoke, and live endpoint strings.

- [ ] **Step 3: Write failing ordered execution tests**

With an existing session DB and closed market, assert successful calls are exactly metrics → daily record → adaptive → lane runner. Parameterize a nonzero result at each phase and prove no later child starts. Assert the lane phase uses `post_session_lane_forward_validation_cycles.csv`.

- [ ] **Step 4: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_kis_watch_lane_forward_validation.py tests/test_kis_watch_metrics.py
```

Expected: failures because the config and fourth phase do not exist.

### Task 2: Implement opt-in ORB watch integration

**Files:**
- Modify: `run_kis_paper_watch.py`
- Modify: `pyproject.toml` only if type coverage is missing
- Test: `tests/test_kis_watch_lane_forward_validation.py`
- Test: `tests/test_kis_watch_metrics.py`

- [ ] **Step 1: Add the frozen lane path configuration**

Define `LaneForwardValidationConfig` with execution database, lane registry, review ledger, and output directory Paths. Add a pure all-or-none validator that accepts only `StrategyMode.ORB` when configured.

- [ ] **Step 2: Add the exact lane child command**

Derive the New York session date from `observed_at` and build the existing lane runner command without exposing credential, endpoint, arm, fixture, force, or mutation arguments.

- [ ] **Step 3: Extend the post-session state machine**

Capture the adaptive exit code instead of returning immediately. If nonzero, return it. If zero and no lane config exists, preserve the existing return value. If configured, invoke the lane child and audit it separately; return its exact exit code so watch failure accounting remains fail-closed.

- [ ] **Step 4: Add optional Typer flags and early validation**

Expose:

```text
--lane-execution-database
--lane-registry
--lane-review-ledger
--lane-forward-output-dir
```

Validate all-or-none and ORB-only before checking market state, waiting, scanning, or loading any provider/credential. Pass the resulting config into `run_session_metrics`.

- [ ] **Step 5: Run focused GREEN and static checks**

```bash
uv run pytest -q tests/test_kis_watch_lane_forward_validation.py tests/test_kis_watch_metrics.py tests/test_orb_lane_forward_validation_cli.py
uv run ruff check run_kis_paper_watch.py tests/test_kis_watch_lane_forward_validation.py tests/test_kis_watch_metrics.py
uv run basedpyright
```

Expected: zero failures, findings, errors, or warnings.

### Task 3: Manual QA, documentation, and checkpoint

**Files:**
- Modify: `README.md`
- Modify: `CODEX_START_HERE.md`
- Modify: `docs/architecture_ko.md`
- Create: `docs/checkpoints/2026-07-15-scheduled-orb-forward-validation-ko.md`
- Modify: `docs/superpowers/plans/2026-07-15-scheduled-orb-forward-validation.md`

- [ ] **Step 1: Add executable CLI boundary tests**

Run direct help and assert the four lane flags are visible while arm, credential, endpoint, force, and fixture options are absent. Run a partial configuration and a configured non-ORB strategy and assert exit code 2 before subprocess/provider access.

- [ ] **Step 2: Run fake manual sequence QA**

Use a temporary session DB and injected child runner to exercise success, adaptive failure, lane failure, and exact replay. Inspect call order and audit rows. Do not load Paper credentials or call broker network.

- [ ] **Step 3: Document authority and scheduling boundaries**

State that the watch schedules only existing child CLIs, lane integration is opt-in and ORB-only, upstream failure suppresses snapshot/Reviewer, lane failure fails the watch, snapshot remains GET/WSS-only, Reviewer remains query-only, and external Alpaca mutation remains zero.

- [ ] **Step 4: Run full verification one heavy command at a time**

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
uv run ruff format --check run_kis_paper_watch.py tests/test_kis_watch_lane_forward_validation.py tests/test_kis_watch_metrics.py
git diff --check
```

Expected: zero failures, Ruff findings, type errors/warnings, formatting drift, or diff errors.

- [ ] **Step 5: Commit, push, and verify origin alignment**

```bash
git add README.md CODEX_START_HERE.md docs/architecture_ko.md docs/checkpoints/2026-07-15-scheduled-orb-forward-validation-ko.md docs/superpowers/plans/2026-07-15-scheduled-orb-forward-validation.md run_kis_paper_watch.py tests/test_kis_watch_lane_forward_validation.py tests/test_kis_watch_metrics.py
git commit -m "feat: schedule ORB lane forward validation"
git push origin feature/paper-account-activities
git rev-list --left-right --count HEAD...origin/feature/paper-account-activities
```

Expected: `0 0`, clean worktree, fixed risk limits unchanged, no new authority path, and external Alpaca POST/DELETE count zero.
