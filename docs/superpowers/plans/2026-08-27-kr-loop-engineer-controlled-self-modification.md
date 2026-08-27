# KR Loop Engineer Controlled Self-Modification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn durable KR repeated-failure bundles into bounded code challengers, validate them in an isolated checkout, require future shadow evidence before paper-only promotion, and automatically restore the previous release when host health gates fail.

**Architecture:** A content-addressed event ledger owns each candidate and an append-only release pointer. A fixed host policy maps failure codes to a small file allow-list; the existing bounded Grok development harness edits only those files inside a private standalone Git checkout. Independent verification produces an immutable patch artifact, future shadow receipts drive deterministic promotion, and host health receipts drive rollback. Candidate code never receives broker, credential, risk-kernel, endpoint-policy, or release-policy authority.

**Tech Stack:** Python 3.12, Pydantic v2, SQLite, existing `development_harness` Grok runner, Git, pytest, Ruff, basedpyright.

---

### Task 1: Candidate contract and append-only lifecycle ledger

**Files:**
- Create: `trading_agent/kr_loop_engineer_models.py`
- Create: `trading_agent/kr_loop_engineer_store.py`
- Test: `tests/test_kr_loop_engineer_store.py`

- [x] **Step 1: Write failing lifecycle tests**

Add Given/When/Then tests proving content-addressed identities, legal transition order, exact replay idempotency, append-only failed transitions, and atomic promotion/rollback release generations.

- [x] **Step 2: Run the store tests and confirm RED**

Run: `uv run pytest -q tests/test_kr_loop_engineer_store.py`

Expected: failure because the new model/store modules do not exist.

- [x] **Step 3: Implement strict models and SQLite store**

Implement frozen Pydantic models for `detected`, `candidate_ready`, `shadowing`, `promoted`, `rejected`, and `rolled_back` snapshots. Every snapshot and release pointer is SHA-256 content-addressed, preserves `bundle_id`, `base_commit`, candidate patch lineage, verification receipts, shadow receipts, and authority literals `paper_only=True`, `trading_authority=False`, `policy_mutation_authority=False`. The SQLite writer appends immutable snapshots and changes the active release only in the same transaction as promotion or rollback.

- [x] **Step 4: Run the store tests and confirm GREEN**

Run: `uv run pytest -q tests/test_kr_loop_engineer_store.py`

Expected: all tests pass.

### Task 2: Fixed mutation scope and isolated Grok challenger

**Files:**
- Create: `trading_agent/kr_loop_engineer_policy.py`
- Create: `trading_agent/kr_loop_engineer_mutation.py`
- Test: `tests/test_kr_loop_engineer_mutation.py`

- [x] **Step 1: Write failing policy and mutation tests**

Test that each `KrLoopFailureCode` maps to exact existing implementation/test paths, protected host-policy/provider/credential files can never enter the allow-list, a local isolated checkout is used, zero-file or out-of-scope changes are rejected, and successful changes create a mode-0600 immutable binary patch plus candidate commit identity.

- [x] **Step 2: Run the mutation tests and confirm RED**

Run: `uv run pytest -q tests/test_kr_loop_engineer_mutation.py`

Expected: failure because policy and mutation execution do not exist.

- [x] **Step 3: Implement the bounded mutation executor**

Build a `GrokTaskContract` from the evidence bundle and fixed policy. Clone the pinned base locally with hard-link sharing disabled, run the existing Grok harness, verify the resulting changed-path set exactly, commit as the host with hooks disabled, publish the base-to-candidate binary patch atomically, then remove the disposable checkout. Never pass credentials, provider URLs, network targets, broker modules, or release-store paths to the worker.

- [x] **Step 4: Run the mutation tests and confirm GREEN**

Run: `uv run pytest -q tests/test_kr_loop_engineer_mutation.py`

Expected: all tests pass.

### Task 3: Deterministic validation, future shadow promotion, and rollback

**Files:**
- Create: `trading_agent/kr_loop_engineer_controller.py`
- Test: `tests/test_kr_loop_engineer_controller.py`

- [x] **Step 1: Write failing controller tests**

Cover bundle ingestion idempotency, mutation failure preservation, validation rejection, fewer than two distinct future sessions remaining shadow-only, deterministic multi-session superiority promoting the candidate, single-session profit never promoting, and error/data/task/order health breaches rolling the active release back exactly once.

- [x] **Step 2: Run the controller tests and confirm RED**

Run: `uv run pytest -q tests/test_kr_loop_engineer_controller.py`

Expected: failure because the controller does not exist.

- [x] **Step 3: Implement lifecycle orchestration**

Ingest only valid `KrLoopEngineerEvidenceBundle` values with at least three source memories. Require independent static/test/manual receipts and a deterministic replay receipt before entering `shadowing`. Require two distinct sessions later than candidate creation, zero safety failures, and a fixed score margin before promotion. Treat replay/backtest output as evidence only. On promoted-release health failure, atomically restore the previous release and retain the failed candidate lineage.

- [x] **Step 4: Run the controller tests and confirm GREEN**

Run: `uv run pytest -q tests/test_kr_loop_engineer_controller.py`

Expected: all tests pass.

### Task 4: CLI and persistent supervisor integration

**Files:**
- Create: `run_kr_loop_engineer.py`
- Modify: `trading_agent/kr_autonomous_operator_paths.py`
- Modify: `trading_agent/research_agent_service_projection.py`
- Test: `tests/test_kr_loop_engineer_cli.py`
- Test: `tests/test_research_agent_service_kr_loop_engineer.py`

- [x] **Step 1: Write failing CLI/service tests**

Test CLI help, invalid private input rejection without mutation, bundle sync from outcome memory into the loop ledger, one bounded `tick` transition, shadow-receipt ingestion, health-receipt rollback, restart replay idempotency, and zero broker/provider calls.

- [x] **Step 2: Run CLI/service tests and confirm RED**

Run: `uv run pytest -q tests/test_kr_loop_engineer_cli.py tests/test_research_agent_service_kr_loop_engineer.py`

Expected: failure because the CLI and service hook are absent.

- [x] **Step 3: Implement the operational boundary**

Extend KR operator paths with private loop ledger/artifact roots. During normal service projection, sync new evidence bundles into durable `detected` candidates without running a long coding process in the market tick. Provide a restart-safe one-shot CLI for the Local Agent Computer scheduler to execute one pending mutation, accept bounded future-shadow/health receipts, print only redacted IDs/states, and never perform a broker or provider request.

- [x] **Step 4: Run CLI/service tests and confirm GREEN**

Run: `uv run pytest -q tests/test_kr_loop_engineer_cli.py tests/test_research_agent_service_kr_loop_engineer.py`

Expected: all tests pass.

### Task 5: Hermes/dashboard lineage and end-to-end proof

**Files:**
- Modify: `trading_agent/kr_autonomous_hermes.py`
- Modify: `trading_agent/dashboard_kr_autonomous_operator.py`
- Modify: `trading_agent/dashboard_kr_autonomous_operator_render.py`
- Test: `tests/test_kr_loop_engineer_operator_surface.py`
- Test: `tests/test_kr_loop_engineer_vertical.py`

- [x] **Step 1: Write failing operator and vertical tests**

Test Korean, redacted Hermes messages and dashboard DAG nodes for detected/candidate/shadow/promoted/rejected/rolled-back states. Prove one fixture repeated-failure bundle traverses isolated mutation, independent verification, two future shadow sessions, promotion, health failure, rollback, and restart without duplicate events.

- [x] **Step 2: Run operator/vertical tests and confirm RED**

Run: `uv run pytest -q tests/test_kr_loop_engineer_operator_surface.py tests/test_kr_loop_engineer_vertical.py`

Expected: failure because loop lifecycle projection is absent.

- [x] **Step 3: Implement safe lifecycle projection**

Project immutable lifecycle IDs into the existing task → decision → outcome graph. Messages must say code challenger, future shadow, paper-only release, or rollback; never claim profitability. Exclude paths, raw patches, prompts, headers, credentials, account identifiers, and worker stdout.

- [x] **Step 4: Run operator/vertical tests and confirm GREEN**

Run: `uv run pytest -q tests/test_kr_loop_engineer_operator_surface.py tests/test_kr_loop_engineer_vertical.py`

Expected: all tests pass.

### Task 6: Project verification and delivery

**Files:**
- Create: `docs/checkpoints/2026-08-27-kr-loop-engineer-controlled-self-modification-ko.md`

- [x] **Step 1: Run focused and broad regression suites**

Run the new loop tests plus KR autonomous, dashboard, Hermes, research-agent-service, development-harness, day-agent challenger, Alpaca endpoint-guard, KIS/LS mutation-boundary tests.

- [x] **Step 2: Run static verification**

Run Ruff formatting/check, basedpyright on all changed Python files, `git diff --check`, and the Omo no-excuse Python checker.

- [x] **Step 3: Perform manual QA through the CLI**

Run CLI help, a malformed private receipt, and a fixture happy path that reaches shadow, promotes only after two future sessions, then rolls back on a host health breach. Confirm zero KIS/LS/Alpaca calls and no secrets/paths/raw worker output in terminal, Hermes, or dashboard payloads.

- [x] **Step 4: Record evidence, commit, and merge locally**

Write exact commands/results and the natural-session observation caveat to the checkpoint, commit implementation and verification records, fast-forward local `main`, rerun the focused merged verification, then remove the owned worktree and feature branch while preserving all user-owned untracked files.
