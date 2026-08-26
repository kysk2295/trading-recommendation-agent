from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Never, assert_never, cast

from trading_agent.autonomous_evidence_admission import AutonomousEvidenceAdmission, admit_evidence_with_writer
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousSupervisorTickResult,
    AutonomousTaskState,
)
from trading_agent.research_agent_cycle_models import EvidenceId, ResearchAgentEvidenceV1

_BOUNDARIES = frozenset(
    {
        AutonomousTaskState.WAITING_EVENT,
        AutonomousTaskState.WAITING_TIME,
        AutonomousTaskState.BLOCKED,
        AutonomousTaskState.COMPLETED,
        AutonomousTaskState.ABANDONED,
    }
)


class InvalidAutonomousSupervisorDueError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorProjection:
    result: AutonomousSupervisorTickResult
    root_evidence_id: EvidenceId
    checkpoint_ref: str


def projection_for_result(
    runtime: AutonomousSupervisorRuntime,
    result: AutonomousSupervisorTickResult,
) -> AutonomousSupervisorProjection:
    task = runtime.tasks.reader().task(result.task_id or "")
    if task is None:
        raise InvalidAutonomousSupervisorDueError(reason="autonomous_projection_task_missing")
    steps = runtime.tasks.reader().steps(task.task_id)
    if not steps or task.state not in _BOUNDARIES:
        raise InvalidAutonomousSupervisorDueError(reason="autonomous_projection_checkpoint_missing")
    return AutonomousSupervisorProjection(result, task.root_source_evidence_id, steps[-1].step_id)


def recoverable_projections(
    runtime: AutonomousSupervisorRuntime,
) -> tuple[AutonomousSupervisorProjection, ...]:
    tasks = tuple(task for task in runtime.tasks.reader().tasks() if task.state in _BOUNDARIES)
    ordered = tuple(sorted(tasks, key=lambda task: (-task.priority, task.updated_at, task.task_id)))
    return tuple(projection_for_result(runtime, _result_for_task(task)) for task in ordered)


def admitted_evidence_ids(runtime: AutonomousSupervisorRuntime) -> frozenset[EvidenceId]:
    return frozenset(
        evidence_id
        for task in runtime.tasks.reader().tasks()
        for evidence_id in task.source_evidence_ids
    )


def admit_matching_evidence(
    runtime: AutonomousSupervisorRuntime,
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> bool:
    with runtime.tasks.admission_writer() as writer:
        matches = writer.matching_open_tasks(
            evidence.agent_family_id,
            evidence.market_id,
            evidence.subject_refs,
        )
        waiting = tuple(task for task in matches if task.state is AutonomousTaskState.WAITING_EVENT)
        if not waiting:
            return False
        return admit_evidence_with_writer(writer, AutonomousEvidenceAdmission(waiting[0], evidence, now))


def _result_for_task(task: AutonomousResearchTask) -> AutonomousSupervisorTickResult:
    match task.state:
        case AutonomousTaskState.WAITING_EVENT | AutonomousTaskState.WAITING_TIME:
            status = "waiting"
        case AutonomousTaskState.BLOCKED:
            status = "blocked"
        case AutonomousTaskState.COMPLETED:
            status = "completed"
        case AutonomousTaskState.ABANDONED:
            status = "failed"
        case _ as unreachable:
            assert_never(cast(Never, unreachable))
    return AutonomousSupervisorTickResult(
        status=status,
        task_id=task.task_id,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
        next_wake_at=task.next_wake_at,
        next_wake_event=task.next_wake_event,
    )


__all__ = (
    "AutonomousSupervisorProjection",
    "InvalidAutonomousSupervisorDueError",
    "admit_matching_evidence",
    "admitted_evidence_ids",
    "projection_for_result",
    "recoverable_projections",
)
