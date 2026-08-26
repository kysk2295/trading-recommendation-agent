from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from trading_agent._autonomous_supervisor_steps import (
    InvalidAutonomousSupervisorError,
    SourceAdmissionPayload,
    canonical_json,
    payload_json,
    plain_step,
    safe_payload,
    utc_time,
)
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousTaskState,
    autonomous_task_id,
)
from trading_agent.autonomous_task_store import AutonomousTaskWriter
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1

_AGENT_VERSION: Final = "autonomous-supervisor-adapter-v1"
_PLAN: Final = (
    "ask critic",
    "delegate specialist analysis",
    "inspect root evidence",
    "schedule continuation",
)


@dataclass(frozen=True, slots=True)
class AutonomousEvidenceAdmission:
    task: AutonomousResearchTask
    evidence: ResearchAgentEvidenceV1
    occurred_at: dt.datetime


def create_root_evidence_task(
    writer: AutonomousTaskWriter,
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> AutonomousResearchTask:
    occurred_at = utc_time(now)
    references = tuple(sorted(set(evidence.evidence_refs) | {evidence.payload_sha256}))
    task = AutonomousResearchTask(
        task_id=autonomous_task_id(evidence.agent_family_id, evidence.market_id, evidence.evidence_id),
        goal=f"Investigate durable {evidence.agent_family_id} evidence for {evidence.market_id}.",
        owner_role=AutonomousAgentRole.SUPERVISOR,
        agent_family_id=evidence.agent_family_id,
        market_scope=evidence.market_id,
        state=AutonomousTaskState.QUEUED,
        priority=50,
        root_source_evidence_id=evidence.evidence_id,
        source_evidence_ids=(evidence.evidence_id,),
        evidence_refs=references,
        subject_refs=tuple(sorted(set(evidence.subject_refs))),
        current_plan=_PLAN,
        agent_version=_AGENT_VERSION,
        created_at=occurred_at,
        updated_at=occurred_at,
    )
    step = plain_step(
        task,
        1,
        occurred_at,
        AutonomousTaskState.QUEUED,
        payload_json(
            SourceAdmissionPayload(
                evidence_id=evidence.evidence_id,
                evidence_json=canonical_json(evidence.model_dump(mode="json")),
            )
        ),
        task.source_evidence_ids,
        task.evidence_refs,
    )
    _ = writer.create_task_with_initial_step(task, step)
    return task


def admit_evidence_with_writer(
    writer: AutonomousTaskWriter,
    admission: AutonomousEvidenceAdmission,
) -> bool:
    task = admission.task
    evidence = admission.evidence
    if evidence.agent_family_id != task.agent_family_id:
        raise InvalidAutonomousSupervisorError(reason="autonomous_evidence_family_mismatch")
    if evidence.market_id != task.market_scope:
        raise InvalidAutonomousSupervisorError(reason="autonomous_evidence_market_mismatch")
    evidence_json = canonical_json(evidence.model_dump(mode="json"))
    if evidence.evidence_id in task.source_evidence_ids:
        admissions = tuple(
            payload.evidence_json
            for step in writer.steps(task.task_id)
            if isinstance(payload := safe_payload(step), SourceAdmissionPayload)
            and payload.evidence_id == evidence.evidence_id
        )
        if admissions == (evidence_json,):
            return False
        raise InvalidAutonomousSupervisorError(reason="autonomous_evidence_replay_conflict")
    refs = tuple(
        sorted(
            set(task.evidence_refs)
            | set(evidence.evidence_refs)
            | set(evidence.subject_refs)
            | {evidence.payload_sha256}
        )
    )
    step = plain_step(
        task,
        len(writer.steps(task.task_id)) + 1,
        utc_time(admission.occurred_at),
        AutonomousTaskState.QUEUED,
        payload_json(SourceAdmissionPayload(evidence_id=evidence.evidence_id, evidence_json=evidence_json)),
        tuple(sorted(set(task.source_evidence_ids) | {evidence.evidence_id})),
        refs,
    )
    return writer.append_step(step)


__all__ = (
    "AutonomousEvidenceAdmission",
    "admit_evidence_with_writer",
    "create_root_evidence_task",
)
