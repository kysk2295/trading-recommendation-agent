from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, assert_never

from trading_agent._autonomous_supervisor_steps import (
    ArtifactPayload,
    CompletionPayload,
    FailurePayload,
    safe_payload,
)
from trading_agent.autonomous_evidence_admission import (
    AutonomousEvidenceAdmission,
    admit_evidence_with_writer,
    create_root_evidence_task,
)
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import (
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


class InvalidAutonomousSupervisorProjectionError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorAdapter:
    runtime: AutonomousSupervisorRuntime

    def close(self) -> None:
        try:
            self.runtime.tasks.close()
        finally:
            self.runtime.memories.close()

    def tick(
        self,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousSupervisorTickResult:
        exact_id = autonomous_task_id(evidence.agent_family_id, evidence.market_id, evidence.evidence_id)
        task = self.admit_evidence(evidence, now)
        exact = task if task.task_id == exact_id else None
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

    def admit_evidence(
        self,
        evidence: ResearchAgentEvidenceV1,
        now: dt.datetime,
    ) -> AutonomousResearchTask:
        exact_id = autonomous_task_id(evidence.agent_family_id, evidence.market_id, evidence.evidence_id)
        with self.runtime.tasks.admission_writer() as writer:
            exact = writer.task(exact_id)
            matching = writer.matching_open_tasks(
                evidence.agent_family_id,
                evidence.market_id,
                evidence.subject_refs,
            )
            if exact is None and not matching:
                task = create_root_evidence_task(writer, evidence, now)
            else:
                task = exact or matching[0]
                _ = admit_evidence_with_writer(writer, AutonomousEvidenceAdmission(task, evidence, now))
            durable = writer.task(task.task_id)
            if durable is None:
                raise InvalidAutonomousSupervisorProjectionError(reason="autonomous_admission_task_missing")
            return durable

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


__all__ = ("AutonomousSupervisorAdapter", "InvalidAutonomousSupervisorProjectionError")
