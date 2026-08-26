from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, Literal, assert_never

from trading_agent._autonomous_supervisor_steps import (
    ArtifactPayload,
    CompletionPayload,
    FailurePayload,
    SourceAdmissionPayload,
    canonical_json,
    payload_json,
    plain_step,
    safe_payload,
)
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousSupervisorTickResult,
    AutonomousTaskState,
    autonomous_task_id,
)
from trading_agent.research_agent_cycle_models import (
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentWakeKind,
    research_agent_result_id,
)

_AGENT_VERSION: Final = "autonomous-supervisor-adapter-v1"
_PLAN: Final = (
    "ask critic",
    "delegate specialist analysis",
    "inspect root evidence",
    "schedule continuation",
)


class InvalidAutonomousSupervisorProjectionError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorAdapter:
    runtime: AutonomousSupervisorRuntime

    def tick(
        self,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousSupervisorTickResult:
        reader = self.runtime.tasks.reader()
        exact_id = autonomous_task_id(evidence.agent_family_id, evidence.market_id, evidence.evidence_id)
        exact = reader.task(exact_id)
        matching = reader.matching_open_tasks(
            evidence.agent_family_id,
            evidence.market_id,
            evidence.subject_refs,
        )
        task = exact or (matching[0] if matching else _task_from_evidence(evidence, now))
        if exact is None and not matching:
            _create_with_root_admission(self.runtime, task, evidence, now)
        else:
            _ = self.runtime.admit_evidence(task.task_id, evidence, now)
        if exact is not None:
            match exact.state:
                case AutonomousTaskState.COMPLETED:
                    return _terminal_replay(exact, "completed")
                case AutonomousTaskState.ABANDONED:
                    return _terminal_replay(exact, "failed")
                case (
                    AutonomousTaskState.QUEUED
                    | AutonomousTaskState.OBSERVING
                    | AutonomousTaskState.RESEARCHING
                    | AutonomousTaskState.DELIBERATING
                    | AutonomousTaskState.ACTING
                    | AutonomousTaskState.WAITING_EVENT
                    | AutonomousTaskState.WAITING_TIME
                    | AutonomousTaskState.BLOCKED
                    | AutonomousTaskState.EVALUATING
                    | AutonomousTaskState.LEARNING
                ):
                    pass
                case unreachable:
                    assert_never(unreachable)
        return self.runtime.tick(task, now)

    def project_tick(
        self,
        cycle: ResearchAgentCycleV1,
        result: AutonomousSupervisorTickResult,
        now: dt.datetime,
    ) -> ResearchAgentResultV1:
        task = self.runtime.tasks.reader().task(result.task_id or "")
        if task is None:
            raise InvalidAutonomousSupervisorProjectionError(reason="autonomous_projection_task_missing")
        steps = self.runtime.tasks.reader().steps(task.task_id)
        refs = tuple(sorted(task.evidence_refs))[:32]
        common = {
            "result_id": research_agent_result_id(cycle.cycle_id),
            "cycle_id": cycle.cycle_id,
            "agent_family_id": cycle.agent_family_id,
            "market_id": cycle.market_id,
            "question": task.goal[:500],
            "evidence_refs": refs,
            "occurred_at": now,
            "open_work_ref": str(task.task_id),
        }
        match result.status:
            case "waiting":
                return ResearchAgentResultV1(
                    **common,
                    status=ResearchAgentResultStatus.NO_ACTION,
                    summary="The autonomous research task remains open at a durable wake boundary.",
                    reason="autonomous_task_waiting",
                    continuation="Resume the durable autonomous task at its exact wake boundary.",
                    artifact_refs=(),
                    next_wake_kind=_wake_kind(result),
                    next_wake_at=result.next_wake_at,
                )
            case "blocked" | "failed" as status:
                failure = next(
                    (
                        payload.stable_reason
                        for step in reversed(steps)
                        if isinstance(payload := safe_payload(step), FailurePayload)
                    ),
                    task.blocked_reason or f"autonomous_task_{status}",
                )
                return ResearchAgentResultV1(
                    **common,
                    status=(
                        ResearchAgentResultStatus.BLOCKED
                        if status == "blocked"
                        else ResearchAgentResultStatus.FAILED
                    ),
                    summary="The autonomous research task stopped at a durable failure boundary.",
                    reason=failure,
                    continuation="Resume from the durable task state after its retry boundary.",
                    artifact_refs=(),
                    next_wake_kind=_wake_kind(result),
                    next_wake_at=result.next_wake_at,
                )
            case "completed":
                completion = next(
                    (
                        payload
                        for step in reversed(steps)
                        if isinstance(payload := safe_payload(step), CompletionPayload)
                    ),
                    None,
                )
                artifact = next(
                    (
                        (step, payload)
                        for step in reversed(steps)
                        if isinstance(payload := safe_payload(step), ArtifactPayload)
                        and payload.artifact_kind != "no_trade"
                        and completion is not None
                        and set(completion.completion_evidence_refs).issubset(payload.evidence_refs)
                    ),
                    None,
                )
                if completion is None or artifact is None:
                    return ResearchAgentResultV1(
                        **common,
                        status=ResearchAgentResultStatus.BLOCKED,
                        summary="The autonomous task completed without a durable evidence-linked artifact.",
                        reason="autonomous_task_completed_shape_invalid",
                        continuation="Preserve the task and submit an evidence-linked artifact before completion.",
                        artifact_refs=(),
                        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE,
                        next_wake_at=None,
                    )
                return ResearchAgentResultV1(
                    **common,
                    status=ResearchAgentResultStatus.COMPLETED,
                    summary=completion.summary[:1_000],
                    artifact_refs=(artifact[0].step_id,),
                    next_wake_kind=ResearchAgentWakeKind.TERMINAL,
                    next_wake_at=None,
                )
            case "idle":
                raise InvalidAutonomousSupervisorProjectionError(
                    reason="autonomous_supervisor_idle_after_cycle_start"
                )
            case unreachable:
                assert_never(unreachable)


def _wake_kind(result: AutonomousSupervisorTickResult) -> ResearchAgentWakeKind:
    if result.next_wake_at is not None:
        return ResearchAgentWakeKind.SCHEDULED
    if result.next_wake_event is not None:
        return ResearchAgentWakeKind.NEW_EVIDENCE
    return ResearchAgentWakeKind.TERMINAL


def _terminal_replay(
    task: AutonomousResearchTask,
    status: Literal["completed", "failed"],
) -> AutonomousSupervisorTickResult:
    return AutonomousSupervisorTickResult(
        status=status,
        task_id=task.task_id,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
    )


def _task_from_evidence(
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> AutonomousResearchTask:
    references = tuple(sorted(set(evidence.evidence_refs) | {evidence.payload_sha256}))
    return AutonomousResearchTask(
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
        created_at=now,
        updated_at=now,
    )


def _create_with_root_admission(
    runtime: AutonomousSupervisorRuntime,
    task: AutonomousResearchTask,
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> None:
    admission = SourceAdmissionPayload(
        evidence_id=evidence.evidence_id,
        evidence_json=canonical_json(evidence.model_dump(mode="json")),
    )
    step = plain_step(
        task,
        1,
        now,
        AutonomousTaskState.QUEUED,
        payload_json(admission),
        task.source_evidence_ids,
        task.evidence_refs,
    )
    with runtime.tasks.writer() as writer:
        _ = writer.create_task_with_initial_step(task, step)


__all__ = ("AutonomousSupervisorAdapter", "InvalidAutonomousSupervisorProjectionError")
