from __future__ import annotations

import datetime as dt

from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.kr_autonomous_hermes import project_kr_autonomous_state
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths, kr_autonomous_operator_paths
from trading_agent.kr_autonomous_outcome_learning import observe_kr_autonomous_outcomes
from trading_agent.kr_loop_engineer_sync import pending_kr_loop_bundles, sync_kr_loop_bundles
from trading_agent.repository_current_main import current_main_commit
from trading_agent.research_agent_hermes import project_research_agent_results
from trading_agent.research_agent_runtime import ResearchAgentRuntime
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig


def project_service_results(
    config: ResearchAgentServiceConfig,
    runtime: ResearchAgentRuntime,
    now: dt.datetime,
) -> int:
    operator_paths = kr_autonomous_operator_paths(config)
    if operator_paths is not None:
        _ = observe_kr_autonomous_outcomes(operator_paths, now=now)
        _ = sync_service_kr_loop_bundles(config, operator_paths, now)
    projected_ids = frozenset(event.source_event_id for event in HermesDeliveryReader(config.hermes_database).events())
    with HermesDeliveryStore(config.hermes_database).writer() as writer:
        result = project_research_agent_results(
            runtime.store.results(),
            writer,
            evidence=runtime.store.all_evidence(),
            projected_result_ids=projected_ids,
        )
        kr_result = (
            None
            if operator_paths is None
            else project_kr_autonomous_state(operator_paths, writer, projected_source_ids=projected_ids)
        )
    return result.inserted + (0 if kr_result is None else kr_result.inserted)


def sync_service_kr_loop_bundles(
    config: ResearchAgentServiceConfig,
    paths: KrAutonomousOperatorPaths,
    now: dt.datetime,
) -> int:
    if not pending_kr_loop_bundles(paths):
        return 0
    commit = current_main_commit(config.project_root)
    return sync_kr_loop_bundles(paths, base_commit=commit, now=now).inserted


__all__ = ("project_service_results", "sync_service_kr_loop_bundles")
