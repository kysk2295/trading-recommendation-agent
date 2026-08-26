from __future__ import annotations

import hashlib

from trading_agent.research_agent_cycle_models import CycleId, ResearchAgentEvidenceV1


def research_agent_supervisor_cycle_id(
    evidence: ResearchAgentEvidenceV1,
    checkpoint_ref: str,
) -> CycleId:
    material = (
        f"{evidence.agent_family_id}:{evidence.trigger_kind}:{evidence.evidence_id}:"
        f"{checkpoint_ref}:supervisor-cycle-v1"
    )
    return CycleId(hashlib.sha256(material.encode()).hexdigest())


__all__ = ("research_agent_supervisor_cycle_id",)
