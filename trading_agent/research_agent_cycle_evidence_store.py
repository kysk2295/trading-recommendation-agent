from __future__ import annotations

import datetime as dt

from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1
from trading_agent.research_agent_cycle_store_codec import canonical_cycle_json, stored_evidence
from trading_agent.research_agent_cycle_store_support import (
    InvalidResearchAgentCycleStoreError,
    ResearchAgentCycleDatabaseLease,
)


def append_evidence(database: ResearchAgentCycleDatabaseLease, evidence: ResearchAgentEvidenceV1) -> bool:
    payload = canonical_cycle_json(evidence)
    with database.writer() as connection:
        existing = connection.execute(
            "SELECT sequence,evidence_id,agent_family_id,payload_json FROM evidence WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone()
        if existing is not None:
            stored = stored_evidence(existing).evidence
            shipped_projection = evidence.model_copy(
                update={
                    "bounded_payload_json": None,
                    "payload_truncated": False,
                    "subject_refs": (),
                }
            )
            if stored == evidence or (
                stored.bounded_payload_json is None
                and not stored.payload_truncated
                and not stored.subject_refs
                and stored == shipped_projection
            ):
                return False
            raise InvalidResearchAgentCycleStoreError(reason="evidence_identity_conflict")
        with connection:
            _ = connection.execute(
                "INSERT INTO evidence(evidence_id,agent_family_id,available_at,payload_json) VALUES(?,?,?,?)",
                (
                    evidence.evidence_id,
                    evidence.agent_family_id,
                    evidence.available_at.astimezone(dt.UTC).isoformat(),
                    payload,
                ),
            )
    return True


__all__ = ("append_evidence",)
