from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.research_agent_cycle_models import (
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore, StoredResearchAgentEvidence
from trading_agent.research_agent_runtime_support import retry_evidence, scheduled_evidence
from trading_agent.research_agent_wake_policy import ACTOR_WAKE_POLICIES, RunnableResearchActor


class InvalidSupervisorCycleResolutionError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ResolvedCycleEvidence:
    stored: StoredResearchAgentEvidence
    legacy_work: ResearchAgentOpenWorkV1 | None


@dataclass(frozen=True, slots=True)
class ResearchCycleEvidenceResolver:
    store: ResearchAgentCycleStore
    now: dt.datetime
    supervisor_enabled: bool

    def resolve(self, actor: RunnableResearchActor) -> ResolvedCycleEvidence:
        legacy_work = actor.open_work if self.supervisor_enabled and actor.evidence is None else None
        if legacy_work is not None:
            return ResolvedCycleEvidence(self._original(legacy_work), legacy_work)
        if actor.evidence is not None:
            return ResolvedCycleEvidence(actor.evidence, None)
        evidence = self._synthetic(actor)
        _ = self.store.append_evidence(evidence)
        candidates = self.store.runnable_evidence(actor.agent_family_id, self.now)
        stored = next(item for item in reversed(candidates) if item.evidence.evidence_id == evidence.evidence_id)
        return ResolvedCycleEvidence(stored, None)

    def close_legacy_work(self, work: ResearchAgentOpenWorkV1) -> None:
        self.store.upsert_open_work(
            work.model_copy(
                update={
                    "state": ResearchAgentOpenWorkState.TERMINAL,
                    "next_wake_at": None,
                    "updated_at": self.now,
                    "source_evidence_id": None,
                    "failure_count": 0,
                }
            )
        )

    def _original(self, work: ResearchAgentOpenWorkV1) -> StoredResearchAgentEvidence:
        if work.source_evidence_id is None:
            raise InvalidSupervisorCycleResolutionError(reason="supervisor_open_work_source_missing")
        stored = self.store.evidence(work.source_evidence_id)
        if stored is None or stored.evidence.agent_family_id != work.agent_family_id:
            raise InvalidSupervisorCycleResolutionError(reason="supervisor_open_work_evidence_missing")
        return stored

    def _synthetic(self, actor: RunnableResearchActor) -> ResearchAgentEvidenceV1:
        if actor.open_work is not None:
            return retry_evidence(actor.open_work, self.now)
        policy = next(item for item in ACTOR_WAKE_POLICIES if item.family_id == actor.agent_family_id)
        if policy.scheduled_interval is None:
            raise InvalidSupervisorCycleResolutionError(reason="scheduled_policy_interval_missing")
        return scheduled_evidence(
            actor.agent_family_id,
            self.now,
            int(policy.scheduled_interval.total_seconds() // 60),
        )


__all__ = ("ResearchCycleEvidenceResolver", "ResolvedCycleEvidence")
