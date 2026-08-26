from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.research_agent_browser_service_fixtures import browser_service_config
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_service_builder import build_service_runtime
from trading_agent.research_agent_service_models import InvalidResearchAgentServiceRuntimeError

NOW = dt.datetime(2026, 8, 27, 1, 0, tzinfo=dt.UTC)


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
