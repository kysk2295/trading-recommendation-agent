from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.autonomous_task_models import AutonomousSupervisorTickResult
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.research_agent_actions import ResearchAgentActionClient
from trading_agent.research_agent_cycle_models import (
    CycleId,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultV1,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_decision import ResearchAgentDecisionClient
from trading_agent.research_agent_sources import ResearchAgentSourceCollectionBatch


class ResearchAgentEvidenceCollector(Protocol):
    def collect(self, now: dt.datetime) -> ResearchAgentSourceCollectionBatch: ...


@runtime_checkable
class PersistentResearchSupervisor(Protocol):
    def close(self) -> None: ...

    def tick(
        self,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousSupervisorTickResult: ...

    def project_tick(
        self,
        cycle: ResearchAgentCycleV1,
        result: AutonomousSupervisorTickResult,
        now: dt.datetime,
    ) -> ResearchAgentResultV1: ...


@dataclass(frozen=True, slots=True)
class ResearchAgentRuntimeServices:
    store: ResearchAgentCycleStore
    collector: ResearchAgentEvidenceCollector
    decisions: ResearchAgentDecisionClient
    actions: ResearchAgentActionClient
    supervisor_runtime: PersistentResearchSupervisor | None = None


@dataclass(frozen=True, slots=True)
class RuntimeCycleOutcome:
    cycle: ResearchAgentCycleV1
    evidence: ResearchAgentEvidenceV1
    result: ResearchAgentResultV1
    prior_failures: int
    model_calls: int
    recovered_cycles: int
    supervisor_owned: bool = False


class InvalidResearchAgentRuntimeError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ResearchAgentTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["idle", "completed", "failed", "blocked", "no_action"]
    agent_family_id: AgentFamilyId | None
    cycle_id: CycleId | None = Field(pattern=r"^[a-f0-9]{64}$")
    model_calls: int = Field(ge=0, le=12)
    recovered_cycles: int = Field(ge=0)

    @model_validator(mode="after")
    def require_idle_identity(self) -> Self:
        idle = self.status == "idle"
        if idle != (self.agent_family_id is None and self.cycle_id is None and self.model_calls == 0):
            raise InvalidResearchAgentRuntimeError(reason="tick_result_identity_invalid")
        return self


class ResearchAgentBoundedCycleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["idle", "partial", "complete"]
    outcomes: tuple[ResearchAgentTickResult, ...] = Field(max_length=6)
    model_calls: int = Field(ge=0, le=72)
    recovered_cycles: int = Field(ge=0)

    @model_validator(mode="after")
    def require_canonical_family_pass(self) -> Self:
        families = tuple(item.agent_family_id for item in self.outcomes)
        canonical = tuple(family for family in PRIMARY_AGENT_FAMILIES if family in families)
        if families != canonical or self.model_calls != sum(item.model_calls for item in self.outcomes):
            raise InvalidResearchAgentRuntimeError(reason="bounded_cycle_identity_invalid")
        expected_status = "idle" if not families else "complete" if families == PRIMARY_AGENT_FAMILIES else "partial"
        if self.status != expected_status:
            raise InvalidResearchAgentRuntimeError(reason="bounded_cycle_status_invalid")
        return self


__all__ = (
    "InvalidResearchAgentRuntimeError",
    "PersistentResearchSupervisor",
    "ResearchAgentBoundedCycleResult",
    "ResearchAgentEvidenceCollector",
    "ResearchAgentRuntimeServices",
    "ResearchAgentTickResult",
    "RuntimeCycleOutcome",
)
