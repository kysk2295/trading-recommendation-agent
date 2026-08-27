from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trading_agent.autonomous_memory_models import AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore, AutonomousMemoryStoreError
from trading_agent.kr_autonomous_outcome_models import (
    InvalidKrAutonomousOutcomeError,
    KrAutonomousOutcomeMemory,
    KrOutcomeExecutionState,
    KrOutcomeMarketEvidenceState,
)
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_models import KrLoopCandidateSnapshot, KrLoopCandidateState, KrLoopHealthReceipt
from trading_agent.kr_loop_release_artifacts import KrLoopReleaseArtifactStore
from trading_agent.kr_loop_release_reconciler import LaunchctlRunner, reconcile_active_release
from trading_agent.research_agent_service_config import (
    ResearchAgentServiceConfig,
    canonical_research_agent_service_config_sha256,
)
from trading_agent.research_agent_service_health import (
    InvalidResearchAgentServiceHealthError,
    ResearchAgentServiceHealth,
    read_persisted_research_agent_service_health,
)

_MAX_HEALTH_AGE = dt.timedelta(minutes=2)
_VIRTUAL_POSITION_STATES = frozenset(
    {
        KrOutcomeExecutionState.VIRTUAL_ARMED,
        KrOutcomeExecutionState.VIRTUAL_ACTIVE,
        KrOutcomeExecutionState.VIRTUAL_STOPPED,
        KrOutcomeExecutionState.VIRTUAL_TARGETED,
        KrOutcomeExecutionState.VIRTUAL_EXPIRED,
        KrOutcomeExecutionState.VIRTUAL_CENSORED,
    }
)


class InvalidKrLoopHealthMonitorError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop health monitoring failed"


@dataclass(frozen=True, slots=True)
class KrLoopHealthMonitorResult:
    receipt: KrLoopHealthReceipt
    candidate: KrLoopCandidateSnapshot
    reconciled: bool


def build_active_health_receipt(
    *,
    release_id: str,
    promoted_at: dt.datetime,
    config: ResearchAgentServiceConfig,
    observed_at: dt.datetime,
) -> KrLoopHealthReceipt:
    health = _read_health(config)
    unhealthy = not _healthy(health, config, promoted_at, observed_at)
    outcomes = _recent_outcomes(config, promoted_at, observed_at)
    evidence = [
        "service-health:missing"
        if health is None
        else f"service-health:{health.config_sha256}:{health.observed_at.isoformat()}"
    ]
    evidence.extend(f"outcome:{item.outcome_id}" for item in outcomes)
    return KrLoopHealthReceipt(
        release_id=release_id,
        observed_at=observed_at,
        error_rate=Decimal(1 if unhealthy else 0),
        data_eligibility_failures=sum(
            item.market_evidence_state is not KrOutcomeMarketEvidenceState.CURRENT for item in outcomes
        ),
        order_mismatches=sum(
            item.execution_state in _VIRTUAL_POSITION_STATES and item.position_event_id is None for item in outcomes
        ),
        research_task_losses=sum(item.execution_state is KrOutcomeExecutionState.REJECTED for item in outcomes),
        evidence_refs=tuple(sorted(set(evidence)))[:32],
    )


def monitor_active_release(
    *,
    controller: KrLoopEngineerController,
    config: ResearchAgentServiceConfig,
    artifacts: KrLoopReleaseArtifactStore,
    repository: Path,
    active_path: Path,
    observed_at: dt.datetime,
    runner: LaunchctlRunner | None = None,
) -> KrLoopHealthMonitorResult:
    releases = controller.store.releases()
    if not releases:
        raise InvalidKrLoopHealthMonitorError
    active = releases[-1]
    receipt = build_active_health_receipt(
        release_id=active.release_id,
        promoted_at=active.recorded_at,
        config=config,
        observed_at=observed_at,
    )
    candidate = controller.record_health(receipt)
    if candidate.state is not KrLoopCandidateState.ROLLED_BACK:
        return KrLoopHealthMonitorResult(receipt, candidate, False)
    _ = reconcile_active_release(
        store=controller.store,
        artifacts=artifacts,
        repository=repository,
        active_path=active_path,
        now=observed_at,
        runner=runner,
    )
    return KrLoopHealthMonitorResult(receipt, candidate, True)


def _read_health(config: ResearchAgentServiceConfig) -> ResearchAgentServiceHealth | None:
    try:
        return read_persisted_research_agent_service_health(config.output_root)
    except InvalidResearchAgentServiceHealthError:
        return None


def _healthy(
    health: ResearchAgentServiceHealth | None,
    config: ResearchAgentServiceConfig,
    promoted_at: dt.datetime,
    observed_at: dt.datetime,
) -> bool:
    return bool(
        health is not None
        and health.config_sha256 == canonical_research_agent_service_config_sha256(config)
        and health.state == "ready"
        and promoted_at <= health.observed_at <= observed_at
        and observed_at - health.observed_at <= _MAX_HEALTH_AGE
    )


def _recent_outcomes(
    config: ResearchAgentServiceConfig,
    promoted_at: dt.datetime,
    observed_at: dt.datetime,
) -> tuple[KrAutonomousOutcomeMemory, ...]:
    memory = AutonomousMemoryStore(config.output_root / "autonomous-supervisor" / "memory.sqlite3")
    try:
        records = memory.reader().recent(AutonomousMemoryScope.MARKET, limit=128)
    except AutonomousMemoryStoreError:
        return ()
    outcomes: list[KrAutonomousOutcomeMemory] = []
    for record in records:
        try:
            outcome = KrAutonomousOutcomeMemory.model_validate_json(record.summary)
        except (InvalidKrAutonomousOutcomeError, ValidationError, ValueError):
            continue
        if promoted_at <= outcome.observed_at <= observed_at:
            outcomes.append(outcome)
    return tuple(outcomes)


__all__ = (
    "InvalidKrLoopHealthMonitorError",
    "KrLoopHealthMonitorResult",
    "build_active_health_receipt",
    "monitor_active_release",
)
