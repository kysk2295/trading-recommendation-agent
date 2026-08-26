from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleState,
    ResearchAgentResultStatus,
    ResearchAgentWakeKind,
)
from trading_agent.research_agent_runtime import ResearchAgentTickResult
from trading_agent.research_identity_models import MarketId


class ResearchAgentFamilyRuntimeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    agent_family_id: AgentFamilyId
    cursor: int = Field(ge=0)
    cycle_id: str | None
    cycle_state: ResearchAgentCycleState | None
    result_status: ResearchAgentResultStatus | None
    next_wake_kind: ResearchAgentWakeKind | None
    next_wake_at: dt.datetime | None


class DayDiscoveryMarketRuntimeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    market_id: MarketId
    cursor: str | None
    terminal_failure: str | None


class ResearchAgentServiceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["tick", "run", "status"]
    status: str
    agent_family_id: str | None
    cycle_id: str | None
    result_status: str | None
    model_calls: int = Field(ge=0, le=12)
    recovered_cycles: int
    projected_results: int
    systematic_input_status: Literal["ready", "blocked"]
    systematic_input_sha256: str | None
    systematic_foundation_sha256: str | None
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...]
    next_wake_kind: ResearchAgentWakeKind | None
    next_wake_at: dt.datetime | None
    broker_mutation: Literal[0] = 0
    observed_at: dt.datetime


class ResearchAgentServiceCycleReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["cycle"] = "cycle"
    status: Literal["idle", "partial", "complete"]
    outcomes: tuple[ResearchAgentTickResult, ...]
    family_count: int
    model_calls: int
    recovered_cycles: int
    projected_results: int
    systematic_input_status: Literal["ready", "blocked"]
    systematic_input_sha256: str | None
    systematic_foundation_sha256: str | None
    family_runtime: tuple[ResearchAgentFamilyRuntimeReport, ...]
    next_wake_kind: ResearchAgentWakeKind | None
    next_wake_at: dt.datetime | None
    broker_mutation: Literal[0] = 0
    observed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SystematicInputReportBinding:
    status: Literal["ready", "blocked"]
    input_sha256: str | None
    foundation_sha256: str | None


class InvalidResearchAgentServiceRuntimeError(RuntimeError):
    pass


__all__ = (
    "DayDiscoveryMarketRuntimeReport",
    "InvalidResearchAgentServiceRuntimeError",
    "ResearchAgentFamilyRuntimeReport",
    "ResearchAgentServiceCycleReport",
    "ResearchAgentServiceReport",
    "SystematicInputReportBinding",
)
