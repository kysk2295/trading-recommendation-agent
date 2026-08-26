from __future__ import annotations

import ast
import datetime as dt
import json
import stat
from pathlib import Path

import pytest

import trading_agent.autonomous_supervisor_service as service_module
from tests.test_autonomous_supervisor_adapter import _evidence
from tests.test_research_agent_service_cli import _config
from trading_agent._autonomous_supervisor_wire import tools_wire
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import AutonomousAgentRole, AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_service import (
    autonomous_supervisor_paths,
    autonomous_supervisor_status,
    build_autonomous_supervisor,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore, AutonomousTaskStoreError
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolExecutionContext,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_service_runtime import build_service_runtime
from trading_agent.research_os_runtime import ResearchOsRuntimeReport, run_research_os_tick
from trading_agent.researcher_llm import FixtureLlmProposalClient, HermesCliProposalClient

NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)


class _FixtureCloseError(RuntimeError):
    pass


def _defer_client() -> FixtureLlmProposalClient:
    payload = {
        "kind": "defer",
        "next_wake_at": "2026-08-26T12:05:00Z",
        "next_wake_event": None,
        "reason": "Wait for the next bounded review interval.",
        "resume_condition": "Resume when the scheduled review interval opens.",
    }
    return FixtureLlmProposalClient(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())


def test_builder_creates_private_restart_safe_stores_and_safe_wire(tmp_path: Path) -> None:
    # Given: a validated service configuration and an autonomous fixture provider.
    config = _config(tmp_path)

    # When: the production supervisor is built and receives source evidence.
    adapter = build_autonomous_supervisor(config, client=_defer_client(), clock=lambda: NOW)
    result = adapter.tick(_evidence("day_trading", "a"), NOW)
    wire = tools_wire(adapter.runtime.tools)

    # Then: only private durable stores and the exact safe read-only tool wire exist.
    paths = autonomous_supervisor_paths(config)
    assert result.status == "waiting"
    assert paths.task_database.name == "tasks.sqlite3"
    assert paths.memory_database.name == "memory.sqlite3"
    assert stat.S_IMODE(paths.task_database.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.task_database.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.memory_database.stat().st_mode) == 0o600
    assert adapter.runtime.tools.allowed_tool_names == ("evidence.read", "memory.search", "task.history")
    assert adapter.runtime.tools.allowed_tools(AutonomousAgentRole.TRADING) == (
        "evidence.read",
        "memory.search",
        "task.history",
    )
    assert wire.worker_modules == frozenset({"trading_agent.autonomous_supervisor_service"})


def test_builder_rejects_symlinked_task_database(tmp_path: Path) -> None:
    # Given: a prior private database is replaced by a symlink alias.
    config = _config(tmp_path)
    _ = build_autonomous_supervisor(config, client=_defer_client(), clock=lambda: NOW)
    paths = autonomous_supervisor_paths(config)
    target = paths.task_database.with_name("tasks-target.sqlite3")
    paths.task_database.rename(target)
    paths.task_database.symlink_to(target)

    # When/Then: restart rejects the alias before opening a writer.
    with pytest.raises(AutonomousTaskStoreError):
        build_autonomous_supervisor(config, client=_defer_client(), clock=lambda: NOW)


def test_service_module_has_no_trading_or_credential_provider_imports() -> None:
    # Given: the production autonomous service source module.
    source = Path(__file__).parents[1] / "trading_agent" / "autonomous_supervisor_service.py"

    # When: its direct import authority is inspected structurally.
    imported = tuple(
        node.module or ""
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    )

    # Then: no trading, credential, shell, or browser-provider authority is imported directly.
    forbidden = ("alpaca", "broker", "order", "account", "credential", "kis", "ls_", "shell")
    assert not any(fragment in module.lower() for module in imported for fragment in forbidden)
    assert {module for module in imported if "browser" in module} <= {"trading_agent.autonomous_browser_tools"}


def test_repeated_status_and_tools_close_owned_handles_and_reopen(tmp_path: Path) -> None:
    # Given: one durable task and a baseline process descriptor count.
    config = _config(tmp_path)
    adapter = build_autonomous_supervisor(config, client=_defer_client(), clock=lambda: NOW)
    result = adapter.tick(_evidence("day_trading", "a"), NOW)
    assert result.task_id is not None
    context = AutonomousToolExecutionContext(
        task_id=result.task_id,
        agent_family_id="day_trading",
        market_scope="kr_equities",
    )
    descriptors = Path("/dev/fd")
    before = len(tuple(descriptors.iterdir()))

    # When: query-owned stores and callback-owned stores are repeatedly opened and closed.
    for _ in range(20):
        status = service_module.autonomous_supervisor_status_for_config(config, NOW)
        _ = adapter.runtime.tools.dispatch(
            AutonomousAgentRole.SUPERVISOR,
            AutonomousToolCall(
                tool_name="task.history",
                args=AutonomousToolArguments({}),
                reason="Read bounded history while checking owned resource closure.",
            ),
            context,
        )
        assert status.total_tasks == 1
    adapter.close()
    adapter.close()
    reopened = build_autonomous_supervisor(config, client=_defer_client(), clock=lambda: NOW)

    # Then: descriptors stay bounded and the durable database reopens without duplication.
    assert len(tuple(descriptors.iterdir())) <= before + 2
    assert autonomous_supervisor_status(reopened.runtime.tasks, NOW).total_tasks == 1


def test_foundation_tools_read_bounded_durable_state(tmp_path: Path) -> None:
    # Given: one deferred task with its bounded source evidence and history.
    adapter = build_autonomous_supervisor(_config(tmp_path), client=_defer_client(), clock=lambda: NOW)
    result = adapter.tick(_evidence("day_trading", "a"), NOW)
    assert result.task_id is not None
    context = AutonomousToolExecutionContext(
        task_id=result.task_id,
        agent_family_id="day_trading",
        market_scope="kr_equities",
    )

    # When: the supervisor invokes each foundation read tool.
    role = AutonomousAgentRole.SUPERVISOR
    evidence = adapter.runtime.tools.dispatch(
        role,
        AutonomousToolCall(
            tool_name="evidence.read",
            args=AutonomousToolArguments({}),
            reason="Read the bounded source evidence for this durable task.",
        ),
        context,
    )
    history = adapter.runtime.tools.dispatch(
        role,
        AutonomousToolCall(
            tool_name="task.history",
            args=AutonomousToolArguments({}),
            reason="Read the bounded durable step history for this task.",
        ),
        context,
    )
    memories = adapter.runtime.tools.dispatch(
        role,
        AutonomousToolCall(
            tool_name="memory.search",
            args=AutonomousToolArguments({"scope": "market", "subject_ref": "005930"}),
            reason="Search bounded durable memories for this market subject.",
        ),
        context,
    )

    # Then: callbacks return canonical JSON without mutation authority.
    assert len(json.loads(evidence.bounded_json)["evidence"]) == 1
    assert len(json.loads(history.bounded_json)["steps"]) <= 32
    assert json.loads(memories.bounded_json) == {"memories": []}


def test_status_is_read_only_and_reports_open_task(tmp_path: Path) -> None:
    # Given: an existing waiting task database.
    adapter = build_autonomous_supervisor(_config(tmp_path), client=_defer_client(), clock=lambda: NOW)
    result = adapter.tick(_evidence("day_trading", "a"), NOW)

    # When: status is projected from the durable task store.
    status = autonomous_supervisor_status(adapter.runtime.tasks, NOW)

    # Then: the open task and wake boundary are visible without another model call.
    assert status.enabled is True
    assert status.total_tasks == status.nonterminal_tasks == 1
    assert status.blocked_tasks == 0
    assert status.next_wake_at == NOW + dt.timedelta(minutes=5)
    assert status.last_task_id == result.task_id


def test_production_builder_always_installs_supervisor(tmp_path: Path) -> None:
    # Given: a valid production-shaped service configuration.
    config = _config(tmp_path)

    # When: the service runtime is built.
    runtime = build_service_runtime(config)
    try:
        # Then: autonomous delegation is installed before any cycle can run.
        assert runtime.supervisor_enabled is True
    finally:
        runtime.close()


def test_research_os_report_persists_supervisor_status(tmp_path: Path) -> None:
    # Given: a production-shaped combined Research OS configuration.
    config = _config(tmp_path)

    # When: one real combined service tick is completed and persisted.
    report = run_research_os_tick(config, NOW)
    persisted = ResearchOsRuntimeReport.model_validate_json(
        (config.output_root / "research-os-runtime-status.json").read_text(encoding="utf-8")
    )

    # Then: durable supervisor status is part of both report surfaces.
    assert report.autonomous_supervisor.enabled is True
    assert persisted.autonomous_supervisor == report.autonomous_supervisor


def test_missing_configured_supervisor_executable_preserves_durable_task(tmp_path: Path) -> None:
    # Given: a validated provider binding whose executable disappeared after configuration.
    config = _config(tmp_path)
    missing = tmp_path / "missing-hermes"
    client = HermesCliProposalClient(missing, "fixture-service-v1", "fixture-provider")
    adapter = build_autonomous_supervisor(config, client=client, clock=lambda: NOW)

    # When: the admitted task reaches the unavailable executable boundary.
    result = adapter.tick(_evidence("day_trading", "a"), NOW)

    # Then: failure is stable and the task remains durable for restart recovery.
    status = autonomous_supervisor_status(adapter.runtime.tasks, NOW)
    assert result.status == "blocked"
    assert status.total_tasks == status.nonterminal_tasks == 1
    assert status.blocked_tasks == 1
    assert not any("credential" in path.name.lower() for path in tmp_path.rglob("*"))


def test_runtime_close_releases_supervisor_when_cycle_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the cycle store will fail while the installed supervisor is observable.
    runtime = build_service_runtime(_config(tmp_path))
    supervisor_closed: list[bool] = []

    def fail_cycle_close(_store: ResearchAgentCycleStore) -> None:
        raise _FixtureCloseError("fixture cycle close failure")

    def record_supervisor_close(_adapter: AutonomousSupervisorAdapter) -> None:
        supervisor_closed.append(True)

    monkeypatch.setattr(ResearchAgentCycleStore, "close", fail_cycle_close)
    monkeypatch.setattr(AutonomousSupervisorAdapter, "close", record_supervisor_close)

    # When: the aggregate runtime closes across the partial failure.
    with pytest.raises(_FixtureCloseError, match="fixture cycle close failure"):
        runtime.close()

    # Then: supervisor ownership is still released exactly once.
    assert supervisor_closed == [True]


def test_adapter_close_releases_memory_when_task_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: task-store close will fail while memory-store closure is observable.
    adapter = build_autonomous_supervisor(_config(tmp_path), client=_defer_client(), clock=lambda: NOW)
    memories_closed: list[bool] = []

    def fail_task_close(_store: AutonomousTaskStore) -> None:
        raise _FixtureCloseError("fixture task close failure")

    def record_memory_close(_store: AutonomousMemoryStore) -> None:
        memories_closed.append(True)

    monkeypatch.setattr(AutonomousTaskStore, "close", fail_task_close)
    monkeypatch.setattr(AutonomousMemoryStore, "close", record_memory_close)

    # When: adapter close crosses the partial task-store failure.
    with pytest.raises(_FixtureCloseError, match="fixture task close failure"):
        adapter.close()

    # Then: memory ownership is still released exactly once.
    assert memories_closed == [True]
