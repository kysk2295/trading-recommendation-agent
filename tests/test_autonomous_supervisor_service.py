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
from trading_agent.autonomous_kr_tool_runtime import KrAutonomousToolServices
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import AutonomousAgentRole, AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_supervisor_service import (
    autonomous_supervisor_paths,
    autonomous_supervisor_status,
    build_autonomous_supervisor,
    build_foundation_tool_runtime,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore, AutonomousTaskStoreError
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolExecutionContext,
)
from trading_agent.researcher_llm import FixtureLlmProposalClient

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


def test_foundation_runtime_adds_kr_tools_only_when_explicitly_bound(tmp_path: Path) -> None:
    # Given: durable foundation stores and an explicit private KR service binding.
    tasks = AutonomousTaskStore(tmp_path / "tasks.sqlite3")
    memories = AutonomousMemoryStore(tmp_path / "memory.sqlite3")
    kr = KrAutonomousToolServices(
        browser_evidence_database=tmp_path / "browser.sqlite3",
        social_signal_database=tmp_path / "signals.sqlite3",
        task_database=tasks.path,
        service_config_json="{}",
    )

    # When: the foundation runtime is built with and without the optional KR binding.
    baseline = build_foundation_tool_runtime(tasks, memories)
    enabled = build_foundation_tool_runtime(tasks, memories, kr=kr)

    # Then: the unbound foundation tuple is byte-for-byte unchanged and KR is wire-safe when enabled.
    assert baseline.allowed_tool_names == ("evidence.read", "memory.search", "task.history")
    assert enabled.allowed_tool_names == (
        "critic.request",
        "evidence.read",
        "kr.market.corroborate",
        "kr.position.reconcile",
        "kr.trade.plan",
        "kr.virtual.execute",
        "memory.search",
        "social.signal.normalize",
        "task.history",
    )
    assert tools_wire(enabled).worker_modules == frozenset(
        {"trading_agent.autonomous_kr_tools", "trading_agent.autonomous_supervisor_service"}
    )


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
