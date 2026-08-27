# Task 4 KR autonomous decision tools verification

Base commit: `364df823a95ae1bf212b91df257dc346d9920298`

## Fixture dispatch: valid durable vertical

Invocation:

```text
uv run pytest -q tests/test_autonomous_kr_tools.py::test_kr_tools_recover_market_observation_after_restart_and_critic_alone_approves
```

Binary observable: PASS. The fixture appends exact private browser evidence, normalizes a task-root-bound signal, calls a monkeypatched read-only corroborator twice, persists its `AutonomousToolObservation`, rebuilds the tool runtime, recovers and revalidates the market observation, produces a `pending_critic` plan without approval/recommendation tokens, and receives `APPROVED` only through `critic.request` under the Critic role.

## Fixture dispatch: bad authority input

Invocation:

```text
uv run pytest -q tests/test_autonomous_kr_tools.py::test_kr_tool_denies_wrong_role_and_incomplete_arguments_before_store_access
```

Binary observable: PASS. Wrong role, an extra argument, a wrong market scope, and missing exact arguments are rejected. Browser, social-signal, and task store paths remain absent after the attempted calls.

## Runtime, recovery, security, and regression suite

Invocation:

```text
uv run pytest -q tests/test_autonomous_kr_tools.py::test_kr_tools_recover_market_observation_after_restart_and_critic_alone_approves tests/test_autonomous_kr_tools.py::test_kr_tool_denies_wrong_role_and_incomplete_arguments_before_store_access tests/test_autonomous_browser_tools.py tests/test_autonomous_supervisor_service.py
```

Binary observable: `20 passed in 3.38s`.

Invocation:

```text
uv run pytest -q tests/test_autonomous_tool_runtime.py tests/test_autonomous_tool_runtime_boundaries.py tests/test_autonomous_supervisor_execution_safety.py tests/test_autonomous_supervisor_recovery.py tests/test_autonomous_task_store_security.py tests/test_kr_social_signal.py tests/test_kr_social_signal_store.py tests/test_kr_autonomous_market_service.py tests/test_autonomous_kr_market_tool.py tests/test_kr_autonomous_trade_planner.py tests/test_kr_autonomous_critic.py tests/test_kr_autonomous_trade_store.py tests/test_autonomous_reasoning_boundaries.py
```

Binary observable: `128 passed in 11.49s`.

## Static gates

Invocation: Ruff format/check, basedpyright, no-excuse check for all changed Python files, and `git diff --check`.

Binary observable: formatter clean; Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`; no-excuse checker `no violations in 7 file(s)`; diff check exited zero.

## Final post-change regression rerun

Invocation: the combined Task 1–4, tool-runtime, process, recovery, security, and browser suite listed above, followed by the same static gates.

Binary observable: `149 passed in 13.27s`; formatter clean; Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`; no-excuse checker `no violations in 7 file(s)`; diff check exited zero.

## Pending-plan/Critic separation repair at `fd0940a`

Invocation:

```text
uv run pytest -q tests/test_autonomous_kr_tools.py tests/test_autonomous_kr_tool_bindings.py tests/test_kr_autonomous_pending_plan_store.py tests/test_kr_autonomous_trade_planner.py tests/test_kr_autonomous_trade_store.py
```

Binary observable: `27 passed in 2.09s`. Trading persists a content-addressed pending plan with numeric fields while the final-event ledger remains empty. A restarted Critic creates exactly one final event and exact replay returns the same event/verdict. Pre-Critic no-trade returns bounded `reason_codes` and `next_wake_at` without a pending record; another valid task cannot read that event. Stale pending plans fail closed before finalization and do not append a recommendation.

Invocation:

```text
uv run pytest -q tests/test_kr_autonomous_pending_plan_store.py
```

Binary observable: PASS. Exact append/replay/get, divergent proposal or plan identity rejection, schema/payload tamper rejection, private `0700` parent and `0600` file modes, symlink/hardlink/wrong-mode rejection, and replacement between descriptor validation and SQLite connection are all asserted.

Invocation:

```text
uv run pytest -q
uv run ruff check
uv run basedpyright trading_agent/autonomous_kr_tools.py trading_agent/autonomous_kr_tool_runtime.py trading_agent/kr_autonomous_pending_plan_models.py trading_agent/kr_autonomous_pending_plan_store.py trading_agent/kr_autonomous_trade_models.py trading_agent/kr_autonomous_trade_planner.py trading_agent/kr_autonomous_trade_proposal.py trading_agent/_autonomous_kr_tool_support.py
git diff --check
```

Binary observable: complete project regression exited zero; Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`; whitespace check exited zero. Pure Python LOC checks for each changed/new Task 4 source and test file are at most 250.

## Pending-plan reverse-finalization repair

Invocation:

```text
uv run pytest -q tests/test_autonomous_kr_tools.py tests/test_autonomous_kr_tool_bindings.py tests/test_kr_autonomous_pending_plan_store.py tests/test_kr_autonomous_trade_planner.py tests/test_kr_autonomous_trade_store.py tests/test_kr_autonomous_critic.py
```

Binary observable: `33 passed in 3.05s`. Two distinct valid pending plans are created before either finalization. A finalized second plan is recovered by `critic.request` directly from the append-only trade ledger without an auxiliary map, then Critic finalizes the first plan against the refreshed ledger tail. The persisted event order is exact and the second event points to the first event ID; replay returns the existing exact final event with no duplicate.

Invocation: `uv run ruff check`; targeted basedpyright over the Task 3/4 planner, proposal, pending store, tool callbacks, and integration support; `git diff --check`.

Binary observable: Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`; whitespace check exited zero.

## Originating pending-plan identity repair

Invocation:

```text
uv run pytest -q tests/test_autonomous_kr_tools.py tests/test_autonomous_kr_tool_bindings.py tests/test_kr_autonomous_pending_plan_store.py tests/test_kr_autonomous_trade_planner.py tests/test_kr_autonomous_trade_store.py tests/test_kr_autonomous_critic.py
```

Binary observable: `33 passed in 2.52s`. Every final trade event carries its immutable originating `plan_id`. The reverse-order recovery scenario asserts persisted event plan IDs correspond exactly to their two distinct pending plans; replay matching compares the complete validated event payload apart from only the chain/event IDs, which includes `plan_id`.

Invocation: `uv run ruff check`; targeted basedpyright over changed Task 3/4 code and test; `git diff --check`.

Binary observable: Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`; whitespace check exited zero. All modified/new Python files remain at or below 250 pure lines.

## Pending-only collision repair

Binary observable: the KR integration test creates a second content-addressed pending plan by changing only `next_wake_at`, which is absent from a recommendation's final payload. Reverse finalization persists both distinct originating plan IDs, proving complete replay matching does not conflate them.

## Final focused rerun

Invocation: focused Task 3/4 suite including KR tool, Critic-safety, binding, pending-store, planner, trade-store, and Critic tests; full Ruff; targeted basedpyright; diff check; physical line checks.

Binary observable: `35 passed in 1.71s`; Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`; diff check exited zero; `trading_agent/autonomous_kr_tools.py` is 250 physical lines and every checked modified/new Python file is at most 250 lines.
