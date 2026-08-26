from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.research_agent_browser_service_fixtures import browser_service_config
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_service_builder import build_service_runtime
from trading_agent.research_agent_service_models import InvalidResearchAgentServiceRuntimeError

NOW = dt.datetime(2026, 8, 27, 1, 0, tzinfo=dt.UTC)


class _FixtureCycleCloseError(RuntimeError):
    pass


def test_v3_builder_runs_continuous_browser_agenda_and_reopens(tmp_path: Path) -> None:
    # Given: a schema-v3 service bound to a verified private browser gateway config.
    config = browser_service_config(tmp_path)

    # When: the production runtime ticks, closes, and reopens the same durable state.
    runtime = build_service_runtime(config)
    first = runtime.tick(NOW)
    runtime.close()
    reopened = build_service_runtime(config)
    try:
        second = reopened.tick(NOW + dt.timedelta(seconds=30))
        episodes = tuple(
            item for item in reopened.store.all_evidence() if item.source_key == "browser_research_agenda.episode"
        )
    finally:
        reopened.close()

    # Then: the continuing KR agenda owns both ticks without leaking the cycle writer lease.
    assert first.agent_family_id == "market_context"
    assert second.status in {"blocked", "completed", "failed", "idle", "no_action"}
    assert len(episodes) == 1
    assert (config.output_root / "autonomous-supervisor" / "browser-social-evidence.sqlite3").parent.is_dir()


def test_v3_builder_releases_cycle_writer_when_gateway_verification_fails(tmp_path: Path) -> None:
    # Given: a v3 service whose private gateway config is weakened after binding.
    config = browser_service_config(tmp_path)
    assert config.browser_gateway_config is not None
    config.browser_gateway_config.chmod(0o644)

    # When: runtime construction fails at the gateway verification boundary.
    with pytest.raises(InvalidResearchAgentServiceRuntimeError):
        build_service_runtime(config)

    # Then: a new cycle writer can open immediately without a leaked lease.
    reopened = ResearchAgentCycleStore(config.cycle_database)
    reopened.close()


def test_v3_service_runtime_closes_cycle_owner_once_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the service-owned cycle close releases its lease and then reports a failure.
    config = browser_service_config(tmp_path)
    runtime = build_service_runtime(config)
    cycle_closes: list[Path] = []
    supervisor_closes: list[bool] = []
    close_cycle = ResearchAgentCycleStore.close
    close_supervisor = AutonomousSupervisorAdapter.close

    def release_cycle_then_fail(store: ResearchAgentCycleStore) -> None:
        cycle_closes.append(store.path)
        close_cycle(store)
        raise _FixtureCycleCloseError("cycle_close_failed")

    def record_supervisor_close(supervisor: AutonomousSupervisorAdapter) -> None:
        supervisor_closes.append(True)
        close_supervisor(supervisor)

    # When: aggregate runtime shutdown crosses that failing owner boundary.
    with monkeypatch.context() as patch:
        patch.setattr(ResearchAgentCycleStore, "close", release_cycle_then_fail)
        patch.setattr(AutonomousSupervisorAdapter, "close", record_supervisor_close)
        with pytest.raises(_FixtureCycleCloseError, match="cycle_close_failed"):
            runtime.close()

    # Then: the sole owner closed once, the supervisor cleaned up, and the writer reopens.
    assert cycle_closes == [config.cycle_database]
    assert supervisor_closes == [True]
    with ResearchAgentCycleStore(config.cycle_database):
        pass
