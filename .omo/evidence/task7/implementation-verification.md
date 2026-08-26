# Task 7 final verification

Verified commit: `1377ca31d48d9733c25f2faf3b4bb0c19e32af43`

## Trusted current-task tool authority

- Scenario: unrelated durable tasks A and B exist; task A invokes `evidence.read` and `task.history`, then attempts to inject task B through both `task_id` and `current_task_id` model arguments.
- Invocation: `uv run pytest -q tests/test_autonomous_supervisor_tool_authority.py tests/test_autonomous_tool_runtime.py tests/test_autonomous_tool_runtime_boundaries.py tests/test_autonomous_supervisor_service.py tests/test_autonomous_supervisor_execution.py tests/test_autonomous_supervisor_execution_safety.py tests/test_autonomous_supervisor_recovery.py tests/test_autonomous_supervisor_runtime.py`.
- Binary observable: the trusted no-argument reads return only task A evidence/history; all four B-targeting calls raise `autonomous_tool_authority_denied` before callback invocation; the focused suite reports `59 passed`.
- Artifacts: `.omo/evidence/task7-tool-context-fix/red-cross-task.txt`, `.omo/evidence/task7-tool-context-fix/focused-tests.txt`.

## Spawned-child context preservation

- Scenario: the reducer derives context from its current durable task, the process boundary serializes only trusted primitives, and the child reconstructs the strict context before dispatch.
- Invocation: the `test_tool_child_receives_trusted_current_task_context` scenario in `uv run pytest -q tests/test_autonomous_supervisor_execution.py`.
- Binary observable: the spawned callback observes the exact durable current-task id; model tool arguments contain no task selector.
- Artifact: `.omo/evidence/task7-tool-context-fix/focused-tests.txt`.

## Task 4–7 regression and due/resume bridge

- Scenario: reasoning/tool boundaries, task and memory stores, supervisor execution/recovery/adapter/service, cutover/atomicity, the due scheduler and evidence projection bridge, Research OS, and CLI surfaces.
- Invocation: `uv run pytest -q tests/test_autonomous_reasoning.py tests/test_autonomous_reasoning_boundaries.py tests/test_autonomous_tool_runtime.py tests/test_autonomous_tool_runtime_boundaries.py tests/test_autonomous_task_models.py tests/test_autonomous_task_store.py tests/test_autonomous_memory_store.py tests/test_autonomous_supervisor_runtime.py tests/test_autonomous_supervisor_execution.py tests/test_autonomous_supervisor_execution_safety.py tests/test_autonomous_supervisor_recovery.py tests/test_autonomous_supervisor_adapter.py tests/test_autonomous_supervisor_tool_authority.py tests/test_research_agent_runtime.py tests/test_research_agent_runtime_supervisor_cutover.py tests/test_research_agent_runtime_supervisor_cutover_atomic.py tests/test_research_agent_runtime_supervisor_due.py tests/test_autonomous_supervisor_service.py tests/test_research_agent_service_boundaries.py tests/test_research_agent_service_runtime.py tests/test_research_os_runtime.py tests/test_research_agent_service_cli.py`.
- Binary observable: `187 passed in 47.54s` at the verified commit, including the due/resume scenarios.
- Artifact: `.omo/evidence/task7-tool-context-fix/final-sha-task4-7-tests.txt`.

## Whole-range static and size gates

- Scenario: every Python file changed from Task 7 base `8c5bd8ded2e3a62e0b40d4d8e13365bde590b94e` through the verified commit, including all due-bridge files and tests (36 files).
- Invocation: `uv run ruff check -- <36-file manifest>`; `uv run basedpyright <36-file manifest>`; `uv run python .../check-no-excuse-rules.py <36-file manifest>`; effective LOC is measured with `awk 'NF && $1 !~ /^#/'` and rejected at 250 or more.
- Binary observable: Ruff clean; basedpyright `0 errors, 0 warnings`; no-excuse `no violations in 36 file(s)`; maximum effective LOC `249`; `git diff --check 8c5bd8d HEAD` clean.
- Artifacts: `.omo/evidence/task7-tool-context-fix/final-sha-ruff.txt`, `.omo/evidence/task7-tool-context-fix/final-sha-basedpyright.txt`, `.omo/evidence/task7-tool-context-fix/final-sha-no-excuse.txt`, `.omo/evidence/task7-tool-context-fix/final-sha-loc.txt`, `.omo/evidence/task7-tool-context-fix/final-sha-diff-check.txt`, `.omo/evidence/task7-tool-context-fix/changed-python-files.txt`.

## Manual CLI

- Scenario: actual CLI discovery, malformed command rejection, and a fresh configured first tick through the public CLI surface.
- Invocations: `uv run python run_research_agent_runtime.py --help`; `uv run python run_research_agent_runtime.py invalid`; `uv run python run_research_agent_runtime.py tick --config .omo/evidence/task7-tool-context-fix/manual-runtime-final/runtime.json`.
- Binary observable: exits are help `0`, bad input `2`, happy tick `0`; happy JSON has `role_agents.status=no_action`, `broker_mutation=0`, and `trading_mutation=0`.
- Artifacts: `.omo/evidence/task7-tool-context-fix/final-sha-cli-help.txt`, `.omo/evidence/task7-tool-context-fix/final-sha-cli-exits.txt`, `.omo/evidence/task7-tool-context-fix/final-sha-cli-happy-tick.json`, `.omo/evidence/task7-tool-context-fix/final-sha-cli-assertions.txt`.

## CLI fixture diagnostic

- Scenario: the first relocated manual fixture silently returned 2, and a later rerun reused mutable state.
- Invocation: the exact happy CLI command above plus a direct load/tick diagnostic used only to expose the CLI-mapped validation exception.
- Binary observable: the missing activation fixture produced `InvalidSystematicInputActivationError(read_invalid)` before provider/tool dispatch; copying that fixture into a fresh runtime root restored deterministic exit 0/no-action behavior without product-code changes.
- Artifact: `.omo/evidence/task7-tool-context-fix/debug-journal.md`.
