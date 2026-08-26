# Task 7 implementation verification

## Production service and lifecycle

- Scenario: private restart-safe supervisor stores, exact read-only tool allowlist, safe spawn wire, durable missing-provider failure, persisted Research OS status, and partial-close ownership.
- Invocation: `uv run pytest -q tests/test_autonomous*.py tests/test_day_agent_runtime.py tests/test_day_agent_task_store.py tests/test_research_agent_runtime.py tests/test_research_agent_runtime_supervisor_cutover.py tests/test_research_agent_runtime_supervisor_cutover_atomic.py tests/test_research_agent_service_runtime.py tests/test_research_agent_service_boundaries.py tests/test_research_os_runtime.py tests/test_research_agent_service_cli.py`.
- Binary observable: exit `0`; `287 passed in 51.64s`; no skipped supervisor test.
- Artifact: `.omo/evidence/task7/task1-7-regression-final.txt`.

## TDD RED evidence

- Scenario: production supervisor service did not exist.
- Invocation: `uv run pytest -q tests/test_autonomous_supervisor_service.py` before implementation.
- Binary observable: collection failed with `ModuleNotFoundError: trading_agent.autonomous_supervisor_service`.
- Artifact: `.omo/evidence/task7/red-service.txt`.
- Scenario: Research OS omitted autonomous status.
- Invocation: focused persisted-status test before report implementation.
- Binary observable: `ResearchOsRuntimeReport` had no `autonomous_supervisor` attribute.
- Artifact: `.omo/evidence/task7/red-os-status.txt`.
- Scenario: temporary store ownership was not explicit.
- Invocation: repeated status/tool lifecycle test before owned close API.
- Binary observable: missing `autonomous_supervisor_status_for_config`.
- Artifact: `.omo/evidence/task7/red-resource-close.txt`.

## Static, size, and safety gates

- Scenario: all changed Python modules and tests satisfy strict lint and types.
- Invocation: `uv run ruff check <all changed Python files>` and `uv run basedpyright <all changed Python files>`.
- Binary observable: Ruff `All checks passed!`; basedpyright `0 errors, 0 warnings, 0 notes`.
- Artifacts: `.omo/evidence/task7/ruff.txt`, `.omo/evidence/task7/basedpyright.txt`.
- Scenario: forbidden escape hatches and oversized changed files are absent.
- Invocation: programming skill `check-no-excuse-rules.py <all changed Python files>` plus pure-LOC measurement.
- Binary observable: `no violations in 16 file(s)`; every changed file is strictly below 250 pure lines.
- Artifacts: `.omo/evidence/task7/no-excuse.txt`, `.omo/evidence/task7/loc.txt`.
- Scenario: patch contains no whitespace errors.
- Invocation: `git diff --check`.
- Binary observable: exit `0`, empty output.
- Artifact: `.omo/evidence/task7/diff-check.txt`.

## Manual CLI surface

- Scenario: real CLI help.
- Invocation: `uv run --offline python run_research_agent_runtime.py --help`.
- Binary observable: exit `0`; command roster includes `run`, `tick`, `cycle`, and `status`.
- Artifact: `.omo/evidence/task7/cli-help.txt`.
- Scenario: missing private config and plist.
- Invocation: `uv run --offline python run_research_agent_runtime.py status --config /tmp/trading-agent-task7-missing-config.json --plist /tmp/trading-agent-task7-missing.plist`.
- Binary observable: exit `2`; no traceback or secret output.
- Artifact: `.omo/evidence/task7/cli-bad.txt`.
- Scenario: query-only status happy path with generated private config/plist.
- Invocation: actual `status --config <private runtime.json> --plist <private runtime.plist>`.
- Binary observable: exit `0`; canonical JSON reports `status=unavailable`, six zero cursors, and `broker_mutation=0`.
- Artifacts: `.omo/evidence/task7/cli-status-happy.txt`, `.omo/evidence/task7/cli-exits.txt`.
