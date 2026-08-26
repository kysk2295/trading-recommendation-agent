from __future__ import annotations

import datetime as dt

from trading_agent.autonomous_supervisor_due_adapter import AutonomousSupervisorProjection
from trading_agent.research_agent_cycle_models import (
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkV1,
)
from trading_agent.research_agent_cycle_persistence import persist_cycle_outcome, supervisor_audit_work
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore, StoredResearchAgentEvidence
from trading_agent.research_agent_runtime_models import (
    DueResearchSupervisor,
    InvalidResearchAgentRuntimeError,
    ResearchAgentTickResult,
    RuntimeCycleOutcome,
)
from trading_agent.research_agent_runtime_selection import tick_result
from trading_agent.research_agent_supervisor_cycle_identity import research_agent_supervisor_cycle_id
from trading_agent.research_agent_supervisor_cycle_store import SupervisorCycleStart


def resume_due_supervisor(
    store: ResearchAgentCycleStore,
    supervisor: DueResearchSupervisor,
    collected: tuple[ResearchAgentEvidenceV1, ...],
    now: dt.datetime,
    recovered_cycles: int,
) -> ResearchAgentTickResult | None:
    for projection in supervisor.recoverable_projections():
        stored = _root_evidence(store, projection)
        cycle_id = research_agent_supervisor_cycle_id(stored.evidence, projection.checkpoint_ref)
        result = next((item for item in store.results() if item.cycle_id == cycle_id), None)
        if result is None:
            return project_supervisor(
                store,
                supervisor,
                projection,
                now,
                recovered_cycles,
                stored=stored,
            )
        audit_work = supervisor_audit_work(result)
        if not any(item.work_id == audit_work.work_id for item in store.open_work(result.agent_family_id)):
            store.upsert_open_work(audit_work)
    for evidence in collected:
        _ = supervisor.admit_matching_evidence(evidence, now)
    due = supervisor.run_due(now)
    if not due:
        return None
    return project_supervisor(store, supervisor, due[0], now, recovered_cycles)


def project_supervisor(
    store: ResearchAgentCycleStore,
    supervisor: DueResearchSupervisor,
    projection: AutonomousSupervisorProjection,
    now: dt.datetime,
    recovered_cycles: int,
    *,
    stored: StoredResearchAgentEvidence | None = None,
    legacy_work: ResearchAgentOpenWorkV1 | None = None,
) -> ResearchAgentTickResult:
    original = _root_evidence(store, projection) if stored is None else stored
    cycle = store.start_supervisor_cycle(SupervisorCycleStart(original, now, projection.checkpoint_ref))
    result = supervisor.project_tick(cycle, projection.result, now)
    outcome = RuntimeCycleOutcome(
        cycle,
        original.evidence,
        result,
        0,
        projection.result.model_calls,
        recovered_cycles,
        supervisor_owned=True,
    )
    persist_cycle_outcome(store, outcome, legacy_work)
    return tick_result(outcome)


def _root_evidence(
    store: ResearchAgentCycleStore,
    projection: AutonomousSupervisorProjection,
) -> StoredResearchAgentEvidence:
    stored = store.evidence(projection.root_evidence_id)
    if stored is None:
        raise InvalidResearchAgentRuntimeError(reason="autonomous_root_evidence_missing")
    return stored


__all__ = ("project_supervisor", "resume_due_supervisor")
