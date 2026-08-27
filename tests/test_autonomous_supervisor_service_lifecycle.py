from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_autonomous_supervisor_adapter import _evidence
from tests.test_autonomous_supervisor_service import NOW, _defer_client, _FixtureCloseError
from tests.test_research_agent_service_cli import _config
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_service import autonomous_supervisor_status, build_autonomous_supervisor
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_service_runtime import build_service_runtime
from trading_agent.research_os_runtime import ResearchOsRuntimeReport, run_research_os_tick
from trading_agent.researcher_llm import HermesCliProposalClient


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
