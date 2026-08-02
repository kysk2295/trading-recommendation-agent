from __future__ import annotations

import datetime as dt
import os
from typing import Literal

import anyio
from pydantic import BaseModel, ConfigDict

from trading_agent.dashboard_agent_cycle_runtime import read_cycle_runtime_observations
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_agent_actions import ResearchAgentActionConfig, ResearchAgentActionExecutor
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_decision import HermesCliResearchAgentDecisionClient
from trading_agent.research_agent_hermes import project_research_agent_results
from trading_agent.research_agent_runtime import (
    ConfiguredResearchAgentEvidenceCollector,
    ResearchAgentRuntime,
    ResearchAgentRuntimeServices,
    ResearchAgentTickResult,
)
from trading_agent.research_agent_runtime_lease import research_agent_runtime_lease
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_systematic import SystematicResearchActionExecutor


class ResearchAgentServiceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal["tick", "run", "status"]
    status: str
    agent_family_id: str | None
    cycle_id: str | None
    result_status: str | None
    model_calls: Literal[0, 1]
    recovered_cycles: int
    projected_results: int
    broker_mutation: Literal[0] = 0
    observed_at: dt.datetime


class InvalidResearchAgentServiceRuntimeError(RuntimeError):
    pass


def run_service_tick(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> ResearchAgentServiceReport:
    runtime = build_service_runtime(config)
    try:
        tick = runtime.tick(now)
        projected = _project_results(config, runtime)
        report = _report("tick", tick, projected, now)
        write_service_report(config, report)
        return report
    finally:
        runtime.close()


async def run_service_forever(
    config: ResearchAgentServiceConfig,
    *,
    tick_seconds: float = 30.0,
) -> None:
    if tick_seconds <= 0:
        raise InvalidResearchAgentServiceRuntimeError
    _prepare_private_runtime_paths(config)
    lease_path = config.output_root / "research-agent-runtime.lock"
    with research_agent_runtime_lease(lease_path):
        runtime = build_service_runtime(config)
        try:
            while True:
                now = dt.datetime.now(dt.UTC)
                tick = runtime.tick(now)
                projected = _project_results(config, runtime)
                write_service_report(config, _report("run", tick, projected, now))
                await anyio.sleep(tick_seconds)
        finally:
            runtime.close()


def service_status(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> ResearchAgentServiceReport:
    if not config.cycle_database.exists():
        return ResearchAgentServiceReport(
            operation="status",
            status="unavailable",
            agent_family_id=None,
            cycle_id=None,
            result_status=None,
            model_calls=0,
            recovered_cycles=0,
            projected_results=0,
            observed_at=now,
        )
    observations = read_cycle_runtime_observations(config.cycle_database)
    latest = max(observations, key=lambda item: item.observed_at, default=None)
    projected = (
        sum(
            event.kind is HermesDeliveryKind.RESEARCH for event in HermesDeliveryReader(config.hermes_database).events()
        )
        if config.hermes_database.exists()
        else 0
    )
    return ResearchAgentServiceReport(
        operation="status",
        status="armed" if latest is None else latest.state,
        agent_family_id=None if latest is None else latest.family,
        cycle_id=None,
        result_status=None if latest is None else latest.state,
        model_calls=0,
        recovered_cycles=0,
        projected_results=projected,
        observed_at=now,
    )


def build_service_runtime(config: ResearchAgentServiceConfig) -> ResearchAgentRuntime:
    _prepare_private_runtime_paths(config)
    systematic = SystematicResearchActionExecutor(config.systematic)
    actions = ResearchAgentActionExecutor(
        ResearchAgentActionConfig(systematic=systematic, verified_trade_signal_refs=frozenset())
    )
    services = ResearchAgentRuntimeServices(
        store=ResearchAgentCycleStore(config.cycle_database),
        collector=ConfiguredResearchAgentEvidenceCollector(config.source_paths),
        decisions=HermesCliResearchAgentDecisionClient(config.hermes_executable, config.model_id),
        actions=actions,
    )
    return ResearchAgentRuntime(services)


def write_service_report(config: ResearchAgentServiceConfig, report: ResearchAgentServiceReport) -> None:
    write_private_stable_report(
        config.output_root / "research-agent-runtime-status.json",
        report.model_dump_json() + "\n",
    )


def _project_results(config: ResearchAgentServiceConfig, runtime: ResearchAgentRuntime) -> int:
    with HermesDeliveryStore(config.hermes_database).writer() as writer:
        result = project_research_agent_results(runtime.store.results(), writer)
    return result.inserted


def _report(
    operation: Literal["tick", "run"],
    tick: ResearchAgentTickResult,
    projected: int,
    now: dt.datetime,
) -> ResearchAgentServiceReport:
    return ResearchAgentServiceReport(
        operation=operation,
        status=tick.status,
        agent_family_id=tick.agent_family_id,
        cycle_id=tick.cycle_id,
        result_status=None if tick.status == "idle" else tick.status,
        model_calls=tick.model_calls,
        recovered_cycles=tick.recovered_cycles,
        projected_results=projected,
        observed_at=now,
    )


def _prepare_private_runtime_paths(config: ResearchAgentServiceConfig) -> None:
    for directory in (
        config.output_root,
        config.cycle_database.parent,
        config.hermes_database.parent,
        config.systematic.runs_root,
    ):
        descriptor = open_private_parent(directory, create=True)
        try:
            require_private_directory(descriptor)
        finally:
            os.close(descriptor)


__all__ = (
    "InvalidResearchAgentServiceRuntimeError",
    "ResearchAgentServiceReport",
    "build_service_runtime",
    "run_service_forever",
    "run_service_tick",
    "service_status",
    "write_service_report",
)
