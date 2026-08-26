from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, assert_never

from trading_agent._autonomous_supervisor_steps import (
    ArtifactPayload,
    CompletionPayload,
    DecisionPayload,
    DelegatePayload,
    MemoryPayload,
    ObservationPayload,
    WaitPayload,
    parsed_response,
    payload_json,
)
from trading_agent.autonomous_memory_models import AutonomousMemoryRecord
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AutonomousComplete,
    AutonomousDefer,
    AutonomousDelegate,
    AutonomousRecordMemory,
    AutonomousSubmitArtifact,
    AutonomousToolCall,
)
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousRunBudget,
    AutonomousTaskState,
    AutonomousTaskStep,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime


@dataclass(frozen=True, slots=True)
class StepProjection:
    role: AutonomousAgentRole
    state: AutonomousTaskState
    payload: (
        DecisionPayload
        | ObservationPayload
        | DelegatePayload
        | MemoryPayload
        | ArtifactPayload
        | WaitPayload
        | CompletionPayload
    )
    evidence_refs: tuple[str, ...]
    working_memory_ids: tuple[str, ...]
    next_wake_at: dt.datetime | None = None
    next_wake_event: str | None = None
    terminal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyContext:
    task: AutonomousResearchTask
    decision_step: AutonomousTaskStep
    decision: DecisionPayload
    budget: AutonomousRunBudget
    now: dt.datetime


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    status: Literal["continue", "waiting", "completed"]
    tool_calls: int = 0


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorReducer:
    tasks: AutonomousTaskStore
    memories: AutonomousMemoryStore
    tools: AutonomousToolRuntime

    def apply(self, context: ApplyContext) -> ApplyOutcome:
        response = parsed_response(context.decision)
        task = context.task
        decision_hash = context.decision.decision_hash
        match response:
            case AutonomousToolCall() as call:
                observation = self.tools.dispatch(task.owner_role, call)
                projection = StepProjection(
                    role=task.owner_role,
                    state=_active_state(task.owner_role),
                    payload=ObservationPayload(decision_hash=decision_hash, observation=observation),
                    evidence_refs=tuple(sorted(set(task.evidence_refs) | set(observation.evidence_refs))),
                    working_memory_ids=task.working_memory_ids,
                )
                self._append(context, projection)
                return ApplyOutcome(status="continue", tool_calls=1)
            case AutonomousDelegate(role=role, objective=objective):
                projection = StepProjection(
                    role=role,
                    state=_active_state(role),
                    payload=DelegatePayload(decision_hash=decision_hash, role=role, objective=objective),
                    evidence_refs=task.evidence_refs,
                    working_memory_ids=task.working_memory_ids,
                )
                self._append(context, projection)
                return ApplyOutcome(status="continue")
            case AutonomousRecordMemory() as request:
                record = self._memory(context, request)
                projection = StepProjection(
                    role=task.owner_role,
                    state=AutonomousTaskState.LEARNING,
                    payload=MemoryPayload(
                        decision_hash=decision_hash,
                        memory_id=record.memory_id,
                        memory_key=record.memory_key,
                        version=record.version,
                    ),
                    evidence_refs=tuple(sorted(set(task.evidence_refs) | set(record.evidence_refs))),
                    working_memory_ids=tuple(sorted(set(task.working_memory_ids) | {record.memory_id})),
                )
                self._append(context, projection)
                return ApplyOutcome(status="continue")
            case AutonomousSubmitArtifact() as artifact:
                projection = _artifact_projection(task, decision_hash, artifact)
                self._append(context, projection)
                return ApplyOutcome(status="waiting" if artifact.artifact_kind == "no_trade" else "continue")
            case AutonomousDefer() as defer:
                state = AutonomousTaskState.WAITING_TIME if defer.next_wake_at else AutonomousTaskState.WAITING_EVENT
                projection = StepProjection(
                    role=task.owner_role,
                    state=state,
                    payload=WaitPayload(
                        decision_hash=decision_hash,
                        cause="defer",
                        resume_condition=defer.resume_condition,
                    ),
                    evidence_refs=task.evidence_refs,
                    working_memory_ids=task.working_memory_ids,
                    next_wake_at=defer.next_wake_at,
                    next_wake_event=defer.next_wake_event,
                )
                self._append(context, projection)
                return ApplyOutcome(status="waiting")
            case AutonomousComplete(summary=summary, completion_evidence_refs=refs):
                projection = StepProjection(
                    role=task.owner_role,
                    state=AutonomousTaskState.COMPLETED,
                    payload=CompletionPayload(
                        decision_hash=decision_hash,
                        summary=summary,
                        completion_evidence_refs=refs,
                    ),
                    evidence_refs=tuple(sorted(set(task.evidence_refs) | set(refs))),
                    working_memory_ids=task.working_memory_ids,
                    terminal_reason="autonomous_task_completed",
                )
                self._append(context, projection)
                return ApplyOutcome(status="completed")
            case unreachable:
                assert_never(unreachable)

    def _append(self, context: ApplyContext, projection: StepProjection) -> None:
        sequence = len(self.tasks.reader().steps(context.task.task_id)) + 1
        with self.tasks.writer() as writer:
            _ = writer.append_step(_step(context, projection, sequence))

    def _memory(self, context: ApplyContext, request: AutonomousRecordMemory) -> AutonomousMemoryRecord:
        history = self.memories.reader().history(request.memory_key)
        latest = history[-1] if history else None
        if latest is not None and _same_memory(latest, context, request):
            record = latest
        else:
            record = AutonomousMemoryRecord.model_validate(
                {
                    "memory_key": request.memory_key,
                    "version": 1 if latest is None else latest.version + 1,
                    "scope": request.scope,
                    "summary": request.summary,
                    "fact_refs": request.fact_refs,
                    "inference_refs": request.inference_refs,
                    "subject_refs": request.subject_refs,
                    "evidence_refs": request.evidence_refs,
                    "source_task_ids": (context.task.task_id,),
                    "recorded_at": context.decision_step.occurred_at,
                }
            )
        with self.memories.writer() as writer:
            _ = writer.append(record)
        return record


def _step(context: ApplyContext, projection: StepProjection, sequence: int) -> AutonomousTaskStep:
    task = context.task
    return AutonomousTaskStep(
        task_id=task.task_id,
        sequence=sequence,
        role=projection.role,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
        root_source_evidence_id=task.root_source_evidence_id,
        agent_version=task.agent_version,
        state=projection.state,
        payload_json=payload_json(projection.payload),
        source_evidence_ids=task.source_evidence_ids,
        evidence_refs=projection.evidence_refs,
        working_memory_ids=projection.working_memory_ids,
        budget=context.budget,
        occurred_at=context.now,
        next_wake_at=projection.next_wake_at,
        next_wake_event=projection.next_wake_event,
        terminal_reason=projection.terminal_reason,
    )


def _active_state(role: AutonomousAgentRole) -> AutonomousTaskState:
    match role:
        case AutonomousAgentRole.MARKET_OBSERVER:
            return AutonomousTaskState.OBSERVING
        case AutonomousAgentRole.RESEARCH:
            return AutonomousTaskState.RESEARCHING
        case AutonomousAgentRole.TRADING | AutonomousAgentRole.POSITION:
            return AutonomousTaskState.ACTING
        case (
            AutonomousAgentRole.SUPERVISOR
            | AutonomousAgentRole.OPPORTUNITY
            | AutonomousAgentRole.CRITIC
            | AutonomousAgentRole.LOOP_ENGINEER
        ):
            return AutonomousTaskState.DELIBERATING
        case unreachable:
            assert_never(unreachable)


def _artifact_projection(
    task: AutonomousResearchTask, decision_hash: str, artifact: AutonomousSubmitArtifact
) -> StepProjection:
    waiting = artifact.artifact_kind == "no_trade"
    state = AutonomousTaskState.WAITING_TIME if artifact.next_wake_at else AutonomousTaskState.WAITING_EVENT
    if not waiting:
        state = AutonomousTaskState.LEARNING if artifact.artifact_kind == "review" else AutonomousTaskState.EVALUATING
    return StepProjection(
        role=task.owner_role,
        state=state,
        payload=ArtifactPayload(
            decision_hash=decision_hash,
            artifact_kind=artifact.artifact_kind,
            artifact_json=artifact.artifact_json,
            evidence_refs=artifact.evidence_refs,
        ),
        evidence_refs=tuple(sorted(set(task.evidence_refs) | set(artifact.evidence_refs))),
        working_memory_ids=task.working_memory_ids,
        next_wake_at=artifact.next_wake_at,
        next_wake_event=artifact.next_wake_event,
    )


def _same_memory(record: AutonomousMemoryRecord, context: ApplyContext, request: AutonomousRecordMemory) -> bool:
    return (
        record.scope == request.scope
        and record.summary == request.summary
        and record.fact_refs == request.fact_refs
        and record.inference_refs == request.inference_refs
        and record.subject_refs == request.subject_refs
        and record.evidence_refs == request.evidence_refs
        and record.source_task_ids == (context.task.task_id,)
        and record.recorded_at == context.decision_step.occurred_at
    )
