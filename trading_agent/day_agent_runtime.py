from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, assert_never

from pydantic import TypeAdapter, ValidationError

from trading_agent.day_agent_reasoning import DayAgentReasoningClient
from trading_agent.day_agent_task_models import (
    DayAgentAction,
    DayAgentBudget,
    DayAgentResearchTask,
    DayAgentTaskRecordKind,
    DayAgentTaskState,
    DayAgentTaskStep,
)
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_models import (
    DayAgentDefer,
    DayAgentHypothesisSubmission,
    DayAgentReasoningRequest,
    DayAgentReasoningResponse,
    DayAgentThesisSubmission,
    DayAgentToolCall,
    DayAgentToolObservation,
)
from trading_agent.day_agent_tool_runtime import DayAgentToolRuntime, DayAgentToolRuntimeError

# SIZE_OK — one bounded restart state machine owns every durable decision-to-observation transition.
_RESPONSE_ADAPTER: Final = TypeAdapter(DayAgentReasoningResponse)


class DayAgentRuntimeError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class DayAgentRuntime:
    store: DayAgentTaskStore
    reasoner: DayAgentReasoningClient
    tools: DayAgentToolRuntime
    max_steps: int
    clock: Callable[[], dt.datetime]

    def __post_init__(self) -> None:
        if self.max_steps < 1 or self.max_steps > 12:
            raise DayAgentRuntimeError(reason="day_agent_max_steps_invalid")


@dataclass(frozen=True, slots=True)
class DayAgentTaskResult:
    task: DayAgentResearchTask
    steps: tuple[DayAgentTaskStep, ...]
    observations: tuple[DayAgentToolObservation, ...]
    model_calls: int = 0

    @property
    def state(self) -> DayAgentTaskState:
        return self.task.state


def run_day_agent_task(runtime: DayAgentRuntime, task: DayAgentResearchTask) -> DayAgentTaskResult:
    with runtime.store.writer() as writer:
        if runtime.store.reader().task(task.task_id) is None:
            _ = writer.create_task(task)
    model_calls = 0
    processed_steps = 0
    while processed_steps < runtime.max_steps:
        current = _require_task(runtime.store, task.task_id)
        if current.state in {DayAgentTaskState.COMPLETED, DayAgentTaskState.BLOCKED}:
            break
        stored_steps = runtime.store.reader().steps(task.task_id)
        role = _client_role(runtime.reasoner)
        if role is None:
            _append_blocked(runtime, current, stored_steps, "day_agent_role_invalid")
            break
        pending = _pending_decision(stored_steps)
        if pending is not None:
            try:
                persisted_response = _parse_decision(pending.payload_json)
            except DayAgentRuntimeError as error:
                _append_blocked(runtime, current, stored_steps, error.reason)
                break
            _apply_response(runtime, current, stored_steps, persisted_response, role)
            processed_steps += 1
            continue
        if current.budget.remaining_model_calls == 0:
            _append_blocked(runtime, current, stored_steps, "day_agent_model_budget_exhausted")
            break
        if current.budget.remaining_runtime_seconds == 0:
            _append_blocked(runtime, current, stored_steps, "day_agent_runtime_budget_exhausted")
            break
        request = DayAgentReasoningRequest(
            task=current,
            prior_steps=stored_steps,
            observations=_observations(stored_steps),
            allowed_tool_names=runtime.tools.allowed_tool_names,
            remaining_budget=current.budget,
        )
        call_started_at = runtime.clock()
        try:
            raw_response = runtime.reasoner.next_step(request)
        except Exception:
            failed_at = runtime.clock()
            model_calls += 1
            _append_blocked(
                runtime,
                current,
                stored_steps,
                "day_agent_model_call_failed",
                budget=_consume_model(current.budget, _elapsed_seconds(call_started_at, failed_at)),
            )
            break
        decided_at = runtime.clock()
        model_calls += 1
        consumed_budget = _consume_model(current.budget, _elapsed_seconds(call_started_at, decided_at))
        try:
            response = _RESPONSE_ADAPTER.validate_python(raw_response)
        except (TypeError, ValidationError):
            _append_blocked(
                runtime,
                current,
                stored_steps,
                "day_agent_model_response_invalid",
                budget=consumed_budget,
            )
            break
        if consumed_budget.remaining_runtime_seconds == 0:
            _append_blocked(
                runtime,
                current,
                stored_steps,
                "day_agent_runtime_budget_exhausted",
                budget=consumed_budget,
            )
            break
        decision = DayAgentTaskStep(
            task_id=current.task_id,
            sequence=len(stored_steps) + 1,
            record_kind=DayAgentTaskRecordKind.DECISION,
            payload_json=_canonical_payload(response),
            action=response.action,
            reason=response.reason,
            evidence_refs=current.evidence_refs,
            budget=consumed_budget,
            state=DayAgentTaskState.WAITING,
            occurred_at=decided_at,
            scheduled_wake_at=decided_at + dt.timedelta(seconds=1),
        )
        with runtime.store.writer() as writer:
            _ = writer.append_step(decision)
        _apply_response(
            runtime,
            _require_task(runtime.store, task.task_id),
            (*stored_steps, decision),
            response,
            role,
        )
        processed_steps += 1
    return _result(runtime.store, task.task_id, model_calls)


def _apply_response(
    runtime: DayAgentRuntime,
    task: DayAgentResearchTask,
    steps: tuple[DayAgentTaskStep, ...],
    response: DayAgentReasoningResponse,
    role: Literal["reasoning", "coding"],
) -> None:
    match response:
        case DayAgentToolCall():
            if task.budget.remaining_runtime_seconds == 0:
                _append_blocked(runtime, task, steps, "day_agent_runtime_budget_exhausted")
                return
            if task.budget.remaining_tool_calls == 0:
                _append_blocked(runtime, task, steps, "day_agent_tool_budget_exhausted")
                return
            call_started_at = runtime.clock()
            try:
                observation = runtime.tools.dispatch(response)
            except DayAgentToolRuntimeError as error:
                failed_at = runtime.clock()
                _append_blocked(
                    runtime,
                    task,
                    steps,
                    error.reason,
                    budget=_consume_tool(task.budget, _elapsed_seconds(call_started_at, failed_at)),
                )
                return
            observed_at = runtime.clock()
            evidence_refs = tuple(sorted({*task.evidence_refs, *observation.evidence_refs}))
            outcome = DayAgentTaskStep(
                task_id=task.task_id,
                sequence=len(steps) + 1,
                record_kind=DayAgentTaskRecordKind.OBSERVATION,
                payload_json=_canonical_payload(observation),
                action=response.action,
                reason="The allowlisted read-only tool returned a bounded observation.",
                evidence_refs=evidence_refs,
                budget=_consume_tool(task.budget, _elapsed_seconds(call_started_at, observed_at)),
                state=DayAgentTaskState.WAITING,
                occurred_at=observed_at,
                scheduled_wake_at=observed_at + dt.timedelta(seconds=1),
            )
        case DayAgentThesisSubmission():
            if role != "reasoning":
                _append_blocked(runtime, task, steps, "day_agent_role_authority_denied")
                return
            outcome = _terminal_step(
                runtime,
                task,
                steps,
                response.action,
                response.reason,
                "day_agent_trade_thesis_submitted",
                _canonical_payload(response),
                evidence_refs=tuple(sorted({*task.evidence_refs, *response.evidence_refs})),
                current_hypothesis=response.thesis,
            )
        case DayAgentHypothesisSubmission():
            if response.experiment_code is not None and role != "coding":
                _append_blocked(runtime, task, steps, "day_agent_role_authority_denied")
                return
            outcome = _terminal_step(
                runtime,
                task,
                steps,
                response.action,
                response.reason,
                "day_agent_research_hypothesis_submitted",
                _canonical_payload(response),
                evidence_refs=tuple(sorted({*task.evidence_refs, *response.evidence_refs})),
                current_hypothesis=response.hypothesis,
                falsification_conditions=response.falsification_conditions,
            )
        case DayAgentDefer():
            outcome = DayAgentTaskStep(
                task_id=task.task_id,
                sequence=len(steps) + 1,
                record_kind=DayAgentTaskRecordKind.OBSERVATION,
                payload_json=_canonical_payload(response),
                action=response.action,
                reason=response.reason,
                evidence_refs=task.evidence_refs,
                budget=task.budget,
                state=DayAgentTaskState.WAITING,
                occurred_at=runtime.clock(),
                scheduled_wake_at=response.scheduled_wake_at,
                resume_condition=response.resume_condition,
            )
        case unreachable:
            assert_never(unreachable)
    with runtime.store.writer() as writer:
        _ = writer.append_step(outcome)


def _terminal_step(
    runtime: DayAgentRuntime,
    task: DayAgentResearchTask,
    steps: tuple[DayAgentTaskStep, ...],
    action: DayAgentAction,
    reason: str,
    terminal_reason: str,
    payload_json: str,
    *,
    evidence_refs: tuple[str, ...],
    current_hypothesis: str,
    falsification_conditions: tuple[str, ...] | None = None,
) -> DayAgentTaskStep:
    return DayAgentTaskStep(
        task_id=task.task_id,
        sequence=len(steps) + 1,
        record_kind=DayAgentTaskRecordKind.OBSERVATION,
        payload_json=payload_json,
        action=action,
        reason=reason,
        evidence_refs=evidence_refs,
        budget=task.budget,
        state=DayAgentTaskState.COMPLETED,
        occurred_at=runtime.clock(),
        terminal_reason=terminal_reason,
        current_hypothesis=current_hypothesis,
        falsification_conditions=falsification_conditions,
    )


def _append_blocked(
    runtime: DayAgentRuntime,
    task: DayAgentResearchTask,
    steps: tuple[DayAgentTaskStep, ...],
    reason: str,
    *,
    budget: DayAgentBudget | None = None,
) -> None:
    blocked = DayAgentTaskStep(
        task_id=task.task_id,
        sequence=len(steps) + 1,
        record_kind=DayAgentTaskRecordKind.OBSERVATION,
        payload_json=json.dumps({"reason": reason}, separators=(",", ":"), sort_keys=True),
        action=DayAgentAction.DEFER,
        reason=reason,
        evidence_refs=task.evidence_refs,
        budget=task.budget if budget is None else budget,
        state=DayAgentTaskState.BLOCKED,
        occurred_at=runtime.clock(),
        terminal_reason=reason,
    )
    with runtime.store.writer() as writer:
        _ = writer.append_step(blocked)


def _consume_model(budget: DayAgentBudget, elapsed_seconds: int) -> DayAgentBudget:
    return budget.model_copy(
        update={
            "remaining_model_calls": budget.remaining_model_calls - 1,
            "remaining_runtime_seconds": max(0, budget.remaining_runtime_seconds - elapsed_seconds),
        }
    )


def _consume_tool(budget: DayAgentBudget, elapsed_seconds: int) -> DayAgentBudget:
    return budget.model_copy(
        update={
            "remaining_tool_calls": budget.remaining_tool_calls - 1,
            "remaining_runtime_seconds": max(0, budget.remaining_runtime_seconds - elapsed_seconds),
        }
    )


def _elapsed_seconds(started_at: dt.datetime, finished_at: dt.datetime) -> int:
    return max(0, math.ceil((finished_at - started_at).total_seconds()))


def _client_role(client: DayAgentReasoningClient) -> Literal["reasoning", "coding"] | None:
    try:
        role = client.role
    except Exception:
        return None
    if role not in {"reasoning", "coding"}:
        return None
    return role


def _pending_decision(steps: tuple[DayAgentTaskStep, ...]) -> DayAgentTaskStep | None:
    if (
        steps
        and steps[-1].record_kind is DayAgentTaskRecordKind.DECISION
    ):
        return steps[-1]
    return None


def _parse_decision(payload_json: str) -> DayAgentReasoningResponse:
    try:
        return _RESPONSE_ADAPTER.validate_json(payload_json)
    except ValidationError:
        raise DayAgentRuntimeError(reason="day_agent_persisted_decision_invalid") from None


def _observations(steps: tuple[DayAgentTaskStep, ...]) -> tuple[DayAgentToolObservation, ...]:
    observations: list[DayAgentToolObservation] = []
    for step in steps:
        if step.record_kind is DayAgentTaskRecordKind.OBSERVATION:
            try:
                observations.append(DayAgentToolObservation.model_validate_json(step.payload_json))
            except ValidationError:
                continue
    return tuple(observations[-12:])


def _require_task(store: DayAgentTaskStore, task_id: str) -> DayAgentResearchTask:
    task = store.reader().task(task_id)
    if task is None:
        raise DayAgentRuntimeError(reason="day_agent_task_missing")
    return task


def _result(store: DayAgentTaskStore, task_id: str, model_calls: int) -> DayAgentTaskResult:
    stored = store.reader().steps(task_id)
    return DayAgentTaskResult(
        task=_require_task(store, task_id),
        steps=tuple(step for step in stored if step.record_kind is DayAgentTaskRecordKind.DECISION),
        observations=_observations(stored),
        model_calls=model_calls,
    )


def _canonical_payload(
    value: DayAgentReasoningResponse | DayAgentToolObservation,
) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = (
    "DayAgentRuntime",
    "DayAgentRuntimeError",
    "DayAgentTaskResult",
    "run_day_agent_task",
)
