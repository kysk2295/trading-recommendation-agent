# KR Loop Engineer Release Runtime Implementation Plan

> **Execution contract:** implement with `superpowers:test-driven-development`, execute this plan task by task, and run `superpowers:verification-before-completion` before handoff.

**Goal:** Make a promoted KR Loop Engineer challenger become the code used by the persistent Research Agent, generate shadow and health receipts from isolated runtime evidence, automatically roll back unhealthy releases, and run the control plane after Korean market close on macOS.

**Architecture:** A successful mutation is retained as a private immutable release checkout. A small active-release manifest is the single runtime authority; the stable launcher verifies it and executes the selected checkout. An idempotent reconciler projects the append-only release ledger into that manifest and restarts the existing paper/research-only Research Agent. The scheduled Loop service runs champion and challenger cycles sequentially in isolated state roots, scores actual KR outcome memories, records receipts, reconciles promotion/rollback, and never receives broker mutation authority.

**Tech stack:** Python 3.12+, Pydantic v2, SQLite append-only ledgers, subprocess, macOS launchd, pytest, Ruff, basedpyright.

---

## Task 1: Persist and verify candidate releases

**Files:**
- Create: `trading_agent/kr_loop_release_artifacts.py`
- Modify: `trading_agent/kr_loop_engineer_mutation.py`
- Test: `tests/test_kr_loop_release_artifacts.py`
- Modify: `tests/test_kr_loop_engineer_mutation.py`

1. Write failing tests proving a successful candidate checkout remains under `loop-artifacts/releases/<candidate_id>`, has the expected commit and clean Git tree, and a rejected mutation leaves no release.
2. Add strict release manifest models and canonical private publication.
3. Move a validated checkout atomically into the release store before mutation cleanup; verify HEAD, patch hash, ownership, and no symlinks.
4. Run the focused tests.

## Task 2: Add active-release authority and real runtime cutover

**Files:**
- Create: `trading_agent/kr_loop_active_release.py`
- Create: `trading_agent/kr_loop_release_reconciler.py`
- Create: `run_active_research_agent_runtime.py`
- Test: `tests/test_kr_loop_active_release.py`
- Test: `tests/test_kr_loop_release_reconciler.py`
- Test: `tests/test_active_research_agent_runtime_cli.py`

1. Write failing tests for baseline launch, promoted candidate selection, tamper rejection before execution, idempotent reconciliation, kickstart, and rollback selection.
2. Implement an atomically replaceable, private active-release manifest whose candidate path can only be derived from the trusted release store.
3. Implement the stable launcher: validate the manifest and release checkout, set `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPATH`, then execute that checkout's Research Agent CLI with the original config.
4. Implement an idempotent ledger reconciler that writes the desired active manifest and kickstarts `gui/<uid>/ai.trading-agent.research-agent-runtime`; failed kickstart restores the previous manifest.
5. Run focused tests.

## Task 3: Generate real champion/challenger shadow receipts

**Files:**
- Create: `trading_agent/kr_loop_shadow_runtime.py`
- Create: `trading_agent/kr_loop_evaluation.py`
- Modify: `trading_agent/autonomous_memory_store.py`
- Test: `tests/test_kr_loop_shadow_runtime.py`
- Test: `tests/test_kr_loop_evaluation.py`

1. Write failing tests for lane isolation, sequential process execution, same-session outcome requirements, deterministic scores, and refusal to fabricate missing evidence.
2. Add a bounded read-only recent-memory query needed to consume lane outcome memories.
3. Derive champion and challenger Research Agent configs with separate cycle, output, Hermes, systematic ledger, artifact, queue, review, receipt, and run paths.
4. Execute one bounded `cycle` per lane sequentially with the selected source root and no trading authority.
5. Parse actual KR outcome memories and produce a shadow receipt only when both lanes contain current-session outcomes.
6. Run focused tests.

## Task 4: Automate health monitoring and rollback

**Files:**
- Create: `trading_agent/kr_loop_health_monitor.py`
- Test: `tests/test_kr_loop_health_monitor.py`

1. Write failing tests for fresh healthy service evidence, failed/stale runtime health, KR data eligibility failures, virtual state integrity, and automatic rollback reconciliation.
2. Build health receipts from persisted Research Agent health plus production KR outcome memories since promotion.
3. Feed the receipt through the existing controller and reconcile a rollback immediately when thresholds fail.
4. Run focused tests.

## Task 5: Add the off-hours service and macOS LaunchAgent contract

**Files:**
- Create: `trading_agent/kr_loop_automation_config.py`
- Create: `trading_agent/kr_loop_automation_service.py`
- Create: `trading_agent/kr_loop_launchd.py`
- Create: `run_kr_loop_automation.py`
- Modify: `run_kr_loop_engineer.py`
- Test: `tests/test_kr_loop_automation_service.py`
- Test: `tests/test_kr_loop_launchd.py`
- Test: `tests/test_kr_loop_automation_cli.py`

1. Write failing tests for canonical private config, market-close gate, lifecycle progression, stable active-runtime plist, Loop scheduling, and fail-closed invalid input.
2. Implement one idempotent `tick`: sync evidence, mutate at most one candidate after close, run at most one pending shadow session, reconcile promotion, monitor active release, reconcile rollback.
3. Implement versioned Research Agent launcher and Loop Engineer LaunchAgent plists. Schedule only after Korean close and run all heavy work sequentially.
4. Add provision/verify/status/tick CLI commands without printing secrets or raw evidence.
5. Run focused tests and manual CLI help, bad-input, and fixture happy-path checks.

## Task 6: Integrate, document, and verify

**Files:**
- Modify: relevant checkpoint/operator documentation

1. Run all Loop Engineer, Research Agent launcher, autonomous memory, dashboard, and Hermes regression tests.
2. Run Ruff and basedpyright for changed Python files.
3. Manually inspect generated plists and invoke the fixture automation surface end to end.
4. Record exact commands and results in a checkpoint document.
5. Commit the worktree, merge locally into `main` without touching unrelated untracked files, and report that launchd installation remains an explicit operational action unless performed in this turn.
