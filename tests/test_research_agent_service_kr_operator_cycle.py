from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import cast

import pytest

from tests.test_research_agent_service_cli import _config
from trading_agent.hermes_delivery_projection import HermesProjectionResult
from trading_agent.hermes_delivery_store import HermesDeliveryWriter
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths
from trading_agent.kr_autonomous_outcome_learning import KrOutcomeLearningResult
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_runtime import ResearchAgentRuntime
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_service_operations import _project_results

NOW = dt.datetime(2026, 8, 27, 4, 5, tzinfo=dt.UTC)


class _RuntimeView:
    def __init__(self, store: ResearchAgentCycleStore) -> None:
        self.store = store


def test_schema_v4_observes_outcomes_before_kr_projection_and_v2_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _config(tmp_path)
    v4 = ResearchAgentServiceConfig.model_validate(
        base.model_dump(mode="python")
        | {
            "schema_version": 4,
            "browser_gateway_config": (tmp_path / "browser.json").absolute(),
            "kr_market_receipt_root": (tmp_path / "market-receipts").absolute(),
            "kr_social_signal_database": (tmp_path / "signals.sqlite3").absolute(),
        }
    )
    runtime = cast(ResearchAgentRuntime, _RuntimeView(ResearchAgentCycleStore(base.cycle_database)))
    calls: list[str] = []

    def observe(_paths: KrAutonomousOperatorPaths, *, now: dt.datetime) -> KrOutcomeLearningResult:
        del now
        calls.append("observe")
        return KrOutcomeLearningResult(0, 0, (), ())

    def project(
        _paths: KrAutonomousOperatorPaths,
        _writer: HermesDeliveryWriter,
        *,
        projected_source_ids: frozenset[str],
    ) -> HermesProjectionResult:
        del projected_source_ids
        calls.append("project")
        return HermesProjectionResult(examined=0, inserted=0, replayed=0)

    monkeypatch.setattr("trading_agent.research_agent_service_projection.observe_kr_autonomous_outcomes", observe)
    monkeypatch.setattr("trading_agent.research_agent_service_projection.project_kr_autonomous_state", project)

    assert _project_results(v4, runtime, NOW) == 0
    assert calls == ["observe", "project"]
    calls.clear()
    assert _project_results(base, runtime, NOW) == 0
    assert calls == []
    runtime.store.close()
