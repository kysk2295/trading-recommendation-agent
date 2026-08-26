from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Final

from trading_agent._autonomous_supervisor_reducer import (
    ApplyContext,
    AutonomousSupervisorReducer,
    _active_state,
)
from trading_agent._autonomous_supervisor_steps import (
    FailurePayload,
    InvalidAutonomousSupervisorError,
    SourceAdmissionPayload,
    WaitPayload,
    decision_payload,
    parsed_response,
    payload_json,
    reasoning_request,
)
from trading_agent._autonomous_supervisor_steps import (
    future_wait as _future_wait,
)
from trading_agent._autonomous_supervisor_steps import (
    plain_step as _plain_step,
)
from trading_agent._autonomous_supervisor_steps import (
    run_budget as _budget,
)
from trading_agent._autonomous_supervisor_steps import (
    safe_payload as _safe_payload,
)
from trading_agent._autonomous_supervisor_steps import (
    tick_result as _result,
)
from trading_agent._autonomous_supervisor_steps import (
    unapplied_decision as _unapplied_decision,
)
from trading_agent._autonomous_supervisor_steps import (
    utc_time as _utc,
)
from trading_agent.autonomous_memory_store import AutonomousMemoryStore, AutonomousMemoryStoreError
from trading_agent.autonomous_reasoning import (
    AutonomousReasoningClient,
    AutonomousToolCall,
    InvalidAutonomousReasoningError,
    validate_reasoning_response,
)
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousSupervisorTickResult,
    AutonomousTaskId,
    AutonomousTaskState,
)
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime, AutonomousToolRuntimeError
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1

_MODEL_CALLS: Final = 8
_TOOL_CALLS: Final = 16
_RUNTIME_SECONDS: Final = 120
_RETRY_DELAYS: Final = (15, 60, 240, 720)


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorRuntime:
    tasks: AutonomousTaskStore
    memories: AutonomousMemoryStore
    reasoner: AutonomousReasoningClient
    tools: AutonomousToolRuntime
    wall_clock: Callable[[], dt.datetime]
    monotonic: Callable[[], float]
    max_steps: int = 12

    def __post_init__(self) -> None:
        _utc(self.wall_clock())
        if not 1 <= self.max_steps <= 12:
            raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_max_steps_invalid")

    def tick(self, task: AutonomousResearchTask, now: dt.datetime) -> AutonomousSupervisorTickResult:
        current_now = _utc(now)
        durable = self.tasks.reader().task(task.task_id)
        if durable is None:
            raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_task_missing")
        if _future_wait(durable, current_now):
            return _result(durable, "blocked" if durable.state is AutonomousTaskState.BLOCKED else "waiting", 0, 0)
        started = self.monotonic()
        model_calls = 0
        tool_calls = 0
        iterations = 0
        reducer = AutonomousSupervisorReducer(self.tasks, self.memories, self.tools)
        while True:
            durable = self.tasks.reader().task(task.task_id)
            if durable is None:
                raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_task_missing")
            steps = self.tasks.reader().steps(task.task_id)
            decision = _unapplied_decision(steps)
            elapsed = self.monotonic() - started
            if decision is None and (
                model_calls >= _MODEL_CALLS or iterations >= self.max_steps or elapsed >= _RUNTIME_SECONDS
            ):
                return self._budget_wait(durable, current_now, model_calls, tool_calls, None)
            budget = _budget(model_calls, tool_calls, elapsed)
            if decision is None:
                try:
                    request = reasoning_request(
                        self.memories,
                        self.tools.allowed_tool_names,
                        durable,
                        steps,
                        current_now,
                        budget,
                    )
                    response = self.reasoner.next_step(request)
                    validate_reasoning_response(request, response)
                    model_calls += 1
                    decision = self._persist_decision(
                        durable, response, current_now, _budget(model_calls, tool_calls, elapsed)
                    )
                except InvalidAutonomousReasoningError:
                    return self._failure(
                        durable, current_now, model_calls, tool_calls, "reasoning", "autonomous_reasoning_failed", None
                    )
            response = parsed_response(decision[1])
            if isinstance(response, AutonomousToolCall) and tool_calls >= _TOOL_CALLS:
                return self._budget_wait(durable, current_now, model_calls, tool_calls, decision[1].decision_hash)
            try:
                fresh = self.tasks.reader().task(task.task_id)
                if fresh is None:
                    raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_task_missing")
                outcome = reducer.apply(
                    ApplyContext(
                        fresh, decision[0], decision[1], _budget(model_calls, tool_calls, elapsed), current_now
                    )
                )
            except AutonomousToolRuntimeError:
                return self._failure(
                    durable,
                    current_now,
                    model_calls,
                    tool_calls,
                    "tool",
                    "autonomous_tool_failed",
                    decision[1].decision_hash,
                )
            except AutonomousMemoryStoreError:
                return self._failure(
                    durable,
                    current_now,
                    model_calls,
                    tool_calls,
                    "memory",
                    "autonomous_memory_failed",
                    decision[1].decision_hash,
                )
            tool_calls += outcome.tool_calls
            iterations += 1
            if outcome.status != "continue":
                projected = self.tasks.reader().task(task.task_id)
                if projected is None:
                    raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_task_missing")
                return _result(projected, outcome.status, model_calls, tool_calls)

    def run_due(self, now: dt.datetime, events: Collection[str] = ()) -> tuple[AutonomousSupervisorTickResult, ...]:
        current_now = _utc(now)
        return tuple(self.tick(task, current_now) for task in self.tasks.reader().runnable(current_now, events=events))

    def admit_evidence(
        self, task_id: AutonomousTaskId | str, evidence: ResearchAgentEvidenceV1, now: dt.datetime
    ) -> bool:
        current_now = _utc(now)
        task = self.tasks.reader().task(task_id)
        if task is None:
            raise InvalidAutonomousSupervisorError(reason="autonomous_supervisor_task_missing")
        if evidence.agent_family_id != task.agent_family_id:
            raise InvalidAutonomousSupervisorError(reason="autonomous_evidence_family_mismatch")
        if evidence.market_id != task.market_scope:
            raise InvalidAutonomousSupervisorError(reason="autonomous_evidence_market_mismatch")
        if evidence.evidence_id in task.source_evidence_ids:
            return False
        payload = SourceAdmissionPayload(
            evidence_id=evidence.evidence_id,
            source_key=evidence.source_key,
            payload_sha256=evidence.payload_sha256,
            bounded_payload_json=evidence.bounded_payload_json,
            subject_refs=evidence.subject_refs,
        )
        refs = tuple(sorted(set(task.evidence_refs) | set(evidence.evidence_refs) | set(evidence.subject_refs)))
        step = _plain_step(
            task,
            len(self.tasks.reader().steps(task.task_id)) + 1,
            current_now,
            AutonomousTaskState.QUEUED,
            payload_json(payload),
            tuple(sorted(set(task.source_evidence_ids) | {evidence.evidence_id})),
            refs,
        )
        with self.tasks.writer() as writer:
            return writer.append_step(step)

    def _persist_decision(self, task, response, now, budget):
        sequence = len(self.tasks.reader().steps(task.task_id)) + 1
        payload = decision_payload(response, sequence)
        step = _plain_step(
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

    def _budget_wait(self, task, now, model_calls, tool_calls, decision_hash):
        wake = now + dt.timedelta(minutes=1)
        payload = WaitPayload(decision_hash=decision_hash, cause="budget")
        step = _plain_step(
            task,
            len(self.tasks.reader().steps(task.task_id)) + 1,
            now,
            AutonomousTaskState.WAITING_TIME,
            payload_json(payload),
            task.source_evidence_ids,
            task.evidence_refs,
            _budget(model_calls, tool_calls, 0),
            wake,
        )
        with self.tasks.writer() as writer:
            _ = writer.append_step(step)
        return _result(self.tasks.reader().task(task.task_id) or task, "waiting", model_calls, tool_calls)

    def _failure(self, task, now, model_calls, tool_calls, source, reason, decision_hash):
        steps = self.tasks.reader().steps(task.task_id)
        retry = 1 + sum(isinstance(_safe_payload(step), FailurePayload) for step in steps)
        minutes = _RETRY_DELAYS[retry - 1] if retry <= len(_RETRY_DELAYS) else 1440
        wake = now + dt.timedelta(minutes=minutes)
        payload = FailurePayload(decision_hash=decision_hash, source=source, stable_reason=reason, retry_count=retry)
        step = _plain_step(
            task,
            len(steps) + 1,
            now,
            AutonomousTaskState.BLOCKED,
            payload_json(payload),
            task.source_evidence_ids,
            task.evidence_refs,
            _budget(model_calls, tool_calls, 0),
            wake,
            reason,
        )
        with self.tasks.writer() as writer:
            _ = writer.append_step(step)
        return _result(self.tasks.reader().task(task.task_id) or task, "blocked", model_calls, tool_calls)
