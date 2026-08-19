from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Literal

import anyio
from pydantic import BaseModel, ConfigDict, Field

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.private_stable_report import write_private_stable_report
from trading_agent.research_agent_runtime_lease import research_agent_runtime_lease
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig
from trading_agent.research_agent_service_runtime import (
    InvalidResearchAgentServiceRuntimeError,
    ResearchAgentServiceReport,
    run_service_tick,
)
from trading_agent.strategy_research_close_report import project_strategy_research_close_report
from trading_agent.strategy_research_runtime import StrategyResearchRuntime, StrategyResearchRuntimeStatus
from trading_agent.strategy_research_runtime_source import (
    PrivateStrategyResearchWorkSource,
    ScienceKernelCycleRunner,
)


class ResearchOsRuntimeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2] = 2
    operation: Literal["tick", "run"]
    role_agents: ResearchAgentServiceReport
    strategy_research: StrategyResearchRuntimeStatus
    daily_reports_projected: int = Field(default=0, ge=0)
    daily_reports_replayed: int = Field(default=0, ge=0)
    observed_at: dt.datetime
    broker_mutation: Literal[0] = 0
    trading_mutation: Literal[0] = 0


def strategy_research_work_root(config: ResearchAgentServiceConfig) -> Path:
    return config.source_paths.outputs_root / "strategy-research" / "work"


def run_research_os_tick(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
    *,
    operation: Literal["tick", "run"] = "tick",
) -> ResearchOsRuntimeReport:
    _prepare_private_paths(config)
    ledger = ExperimentLedgerStore(config.source_paths.experiment_ledger)
    research = StrategyResearchRuntime(
        ledger,
        PrivateStrategyResearchWorkSource(strategy_research_work_root(config)),
        ScienceKernelCycleRunner(ledger),
    ).tick(now)
    with HermesDeliveryStore(config.hermes_database).writer() as writer:
        daily = project_strategy_research_close_report(ledger, writer, now)
    report = ResearchOsRuntimeReport(
        operation=operation,
        role_agents=run_service_tick(config, now),
        strategy_research=research,
        daily_reports_projected=daily.inserted,
        daily_reports_replayed=daily.replayed,
        observed_at=now,
    )
    write_private_stable_report(
        config.output_root / "research-os-runtime-status.json",
        report.model_dump_json() + "\n",
    )
    return report


async def run_research_os_forever(
    config: ResearchAgentServiceConfig,
    *,
    tick_seconds: float = 30.0,
) -> None:
    if tick_seconds <= 0:
        raise InvalidResearchAgentServiceRuntimeError
    _prepare_private_paths(config)
    with research_agent_runtime_lease(config.output_root / "research-agent-runtime.lock"):
        while True:
            _ = run_research_os_tick(config, dt.datetime.now(dt.UTC), operation="run")
            await anyio.sleep(tick_seconds)


def _prepare_private_paths(config: ResearchAgentServiceConfig) -> None:
    directories = (
        config.output_root,
        config.source_paths.experiment_ledger.parent,
        strategy_research_work_root(config),
    )
    for directory in directories:
        descriptor = open_private_parent(directory, create=True)
        try:
            require_private_directory(descriptor)
        finally:
            os.close(descriptor)


__all__ = (
    "ResearchOsRuntimeReport",
    "run_research_os_forever",
    "run_research_os_tick",
    "strategy_research_work_root",
)
