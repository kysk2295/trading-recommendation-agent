from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Final

from trading_agent._autonomous_supervisor_execution import (
    AutonomousExecutionError,
    AutonomousExecutionTimeoutError,
    BoundedAutonomousExecution,
    task_execution_lease,
)
from trading_agent._autonomous_supervisor_outcomes import budget_wait, failure, lease_wait
from trading_agent._autonomous_supervisor_reducer import (
    ApplyContext,
    AutonomousSupervisorReducer,
    _active_state,
)
from trading_agent._autonomous_supervisor_steps import (
    DecisionPayload,
    InvalidAutonomousSupervisorError,
    decision_payload,
    parsed_response,
    payload_json,
    plain_step,
    reasoning_request,
    run_budget,
    tick_result,
    unapplied_decision,
    utc_time,
)
from trading_agent.autonomous_evidence_admission import AutonomousEvidenceAdmission, admit_evidence_with_writer
from trading_agent.autonomous_memory_store import AutonomousMemoryStore, AutonomousMemoryStoreError
from trading_agent.autonomous_reasoning import (
    AutonomousReasoningClient,
    AutonomousReasoningResponse,
    AutonomousToolCall,
    InvalidAutonomousReasoningError,
    validate_reasoning_response,
)
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousRunBudget,
    AutonomousSupervisorTickResult,
    AutonomousTaskId,
    AutonomousTaskState,
    AutonomousTaskStep,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime, AutonomousToolRuntimeError
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1

_MODEL_CALLS: Final = 8
_TOOL_CALLS: Final = 16
_RUNTIME_SECONDS: Final = 120
type Task = AutonomousResearchTask
type Tick = AutonomousSupervisorTickResult
type DecisionRecord = tuple[AutonomousTaskStep, DecisionPayload]


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorRuntime:
    tasks: AutonomousTaskStore
    memories: AutonomousMemoryStore
    reasoner: AutonomousReasoningClient
    tools: AutonomousToolRuntime
    wall_clock: Callable[[], dt.datetime]
    monotonic: Callable[[], float]
    max_steps: int = 12
    execution_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        utc_time(self.wall_clock())
        if not 1 <= self.max_steps <= 12:
            raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_max_steps_invalid")
        if not 0 < self.execution_timeout_seconds <= _RUNTIME_SECONDS:
            raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_timeout_invalid")

    def tick(
        self, task: AutonomousResearchTask, now: dt.datetime, events: Collection[str] = ()
    ) -> AutonomousSupervisorTickResult:
        current_now = utc_time(now)
        durable = self._task(task.task_id)
        if durable.state is AutonomousTaskState.WAITING_EVENT and durable.next_wake_event not in events:
            return tick_result(durable, "waiting", 0, 0)
        if (
            durable.state in {AutonomousTaskState.WAITING_TIME, AutonomousTaskState.BLOCKED}
            and durable.next_wake_at is not None
            and durable.next_wake_at > current_now
        ):
            return tick_result(durable, "blocked" if durable.state is AutonomousTaskState.BLOCKED else "waiting", 0, 0)
        with task_execution_lease(self.tasks.path, task.task_id) as acquired:
            if not acquired:
                return lease_wait(self._task(task.task_id), current_now)
            return self._tick_locked(task, current_now)

    def _tick_locked(self, task: AutonomousResearchTask, current_now: dt.datetime) -> AutonomousSupervisorTickResult:
        started = self.monotonic()
        model_calls = 0
        tool_calls = 0
        iterations = 0
        while True:
            durable = self._task(task.task_id)
            steps = self.tasks.reader().steps(task.task_id)
            decision = unapplied_decision(steps)
            elapsed = self.monotonic() - started
            if decision is None and (
                model_calls >= _MODEL_CALLS or iterations >= self.max_steps or elapsed >= _RUNTIME_SECONDS
            ):
                return budget_wait(self.tasks, durable, current_now, model_calls, tool_calls)
            budget = run_budget(model_calls, tool_calls, elapsed)
            if decision is None:
                try:
                    request = reasoning_request(
                        self.memories,
                        self.tools.allowed_tools(durable.owner_role),
                        durable,
                        steps,
                        current_now,
                        budget,
                    )
                    response = self._execution(elapsed).next_step(request)
                    validate_reasoning_response(request, response)
                    model_calls += 1
                    elapsed = self.monotonic() - started
                    decision = self._persist_decision(
                        durable, response, current_now, run_budget(model_calls, tool_calls, elapsed)
                    )
                except AutonomousExecutionTimeoutError:
                    return budget_wait(self.tasks, durable, current_now, model_calls, tool_calls)
                except (InvalidAutonomousReasoningError, AutonomousExecutionError):
                    return failure(
                        self.tasks,
                        durable,
                        current_now,
                        model_calls,
                        tool_calls,
                        "reasoning",
                        "autonomous_reasoning_failed",
                        None,
                    )
            if elapsed >= _RUNTIME_SECONDS:
                return budget_wait(self.tasks, durable, current_now, model_calls, tool_calls)
            response = parsed_response(decision[1])
            decision_hash = decision[1].decision_hash
            if isinstance(response, AutonomousToolCall) and tool_calls >= _TOOL_CALLS:
                return budget_wait(self.tasks, durable, current_now, model_calls, tool_calls)
            try:
                fresh = self._task(task.task_id)
                reducer = AutonomousSupervisorReducer(self.tasks, self.memories, self._execution(elapsed))
                outcome = reducer.apply(
                    ApplyContext(
                        fresh, decision[0], decision[1], run_budget(model_calls, tool_calls, elapsed), current_now
                    )
                )
            except AutonomousExecutionTimeoutError:
                return budget_wait(self.tasks, durable, current_now, model_calls, tool_calls)
            except (AutonomousToolRuntimeError, AutonomousExecutionError):
                return failure(
                    self.tasks,
                    durable,
                    current_now,
                    model_calls,
                    tool_calls,
                    "tool",
                    "autonomous_tool_failed",
                    decision_hash,
                )
            except AutonomousMemoryStoreError:
                return failure(
                    self.tasks,
                    durable,
                    current_now,
                    model_calls,
                    tool_calls,
                    "memory",
                    "autonomous_memory_failed",
                    decision_hash,
                )
            tool_calls += outcome.tool_calls
            iterations += 1
            if outcome.status != "continue":
                projected = self._task(task.task_id)
                return tick_result(projected, outcome.status, model_calls, tool_calls)

    def run_due(
        self,
        now: dt.datetime,
        events: Collection[str] = (),
        *,
        max_tasks: int | None = None,
    ) -> tuple[AutonomousSupervisorTickResult, ...]:
        current_now = utc_time(now)
        runnable = self.tasks.reader().runnable(current_now, events=events)
        selected = runnable if max_tasks is None else runnable[:max_tasks]
        return tuple(self.tick(task, current_now, events=events) for task in selected)

    def _task(self, task_id: AutonomousTaskId | str) -> Task:
        task = self.tasks.reader().task(task_id)
        if task is None:
            raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_task_missing")
        return task

    def _execution(self, elapsed: float) -> BoundedAutonomousExecution:
        remaining = max(0.001, _RUNTIME_SECONDS - elapsed)
        return BoundedAutonomousExecution(
            self.reasoner, self.tools, min(self.execution_timeout_seconds, remaining), remaining
        )

    def admit_evidence(
        self, task_id: AutonomousTaskId | str, evidence: ResearchAgentEvidenceV1, now: dt.datetime
    ) -> bool:
        with self.tasks.admission_writer() as writer:
            task = writer.task(str(task_id))
            if task is None:
                raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_task_missing")
            return admit_evidence_with_writer(writer, AutonomousEvidenceAdmission(task, evidence, now))

    def _persist_decision(
        self, task: Task, response: AutonomousReasoningResponse, now: dt.datetime, budget: AutonomousRunBudget
    ) -> DecisionRecord:
        sequence = len(self.tasks.reader().steps(task.task_id)) + 1
        payload = decision_payload(response, sequence)
        step = plain_step(
            task,
            sequence,
            now,
            _active_state(task.owner_role),
            payload_json(payload),
            task.source_evidence_ids,
            task.evidence_refs,
            budget,
        )
        with self.tasks.writer() as writer:
            _ = writer.append_step(step)
        return step, payload
