from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from trading_agent.dashboard_agent_runtime import append_agent_runtime_readiness
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1, trigger_fixture
from trading_agent.dashboard_execution_catalog import ProductionExecutionId
from trading_agent.dashboard_trigger_authority import TriggerAuthorityStore


class TriggerBoundary(Protocol):
    def blocker(self, trigger: AutonomousTriggerV1) -> str | None: ...


class TriggerBoundaryFactory(Protocol):
    def __call__(
        self,
        *,
        repository: Path,
        source_evidence_root: Path,
        execution_id: ProductionExecutionId,
    ) -> TriggerBoundary: ...


@dataclass(frozen=True, slots=True)
class AgentReadinessRequest:
    outputs: Path
    observed_at: dt.datetime
    code_sha: str
    source_root: Path
    repository: Path


def record_agent_readiness(
    request: AgentReadinessRequest,
    factory: TriggerBoundaryFactory,
) -> None:
    _ = TriggerAuthorityStore(request.source_root)
    payload = trigger_fixture(now=request.observed_at)
    environment = payload["environment_spec"]
    assert isinstance(environment, dict)
    environment["pinned_code_sha"] = request.code_sha
    trigger = AutonomousTriggerV1.model_validate(payload)
    model = factory(
        repository=request.repository,
        source_evidence_root=request.source_root,
        execution_id=ProductionExecutionId.HERMES_MODEL,
    )
    broker = factory(
        repository=request.repository,
        source_evidence_root=request.source_root,
        execution_id=ProductionExecutionId.RESEARCH_BROKER,
    )
    blocker = model.blocker(trigger) or broker.blocker(trigger)
    _ = append_agent_runtime_readiness(
        request.outputs,
        observed_at=request.observed_at,
        code_sha256=request.code_sha,
        state="armed" if blocker is None else "unavailable",
        reason=blocker,
    )


__all__ = (
    "AgentReadinessRequest",
    "TriggerBoundary",
    "TriggerBoundaryFactory",
    "record_agent_readiness",
)
