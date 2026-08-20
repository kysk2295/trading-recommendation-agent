from __future__ import annotations

import datetime as dt
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pytest

from tests.day_agent_support import NOW, day_task
from trading_agent.day_agent_reasoning import DayAgentReasoningClient
from trading_agent.day_agent_runtime import DayAgentRuntime, run_day_agent_task
from trading_agent.day_agent_task_models import DayAgentAction, DayAgentTaskState
from trading_agent.day_agent_task_store import DayAgentTaskStore
from trading_agent.day_agent_tool_models import (
    DayAgentReasoningRequest,
    DayAgentReasoningResponse,
    DayAgentThesisSubmission,
    DayAgentToolArguments,
    DayAgentToolCall,
)
from trading_agent.day_agent_tool_runtime import (
    DayAgentToolBinding,
    DayAgentToolRuntime,
    DayAgentToolRuntimeError,
)


def _tool_call(action: DayAgentAction, **arguments: str) -> DayAgentToolCall:
    return DayAgentToolCall(
        action=action,
        arguments=arguments,
        reason="Inspect one bounded source before deciding the next research step.",
    )


def _thesis_call() -> DayAgentThesisSubmission:
    return DayAgentThesisSubmission(
        thesis="Current-session catalyst leadership supports a reviewable long thesis artifact.",
        evidence_refs=("evidence.catalyst", "evidence.leader", "evidence.situation"),
        reason="The bounded observations now support a falsifiable research conclusion.",
    )


@dataclass(slots=True)
class ScriptedDayReasoner:
    responses: tuple[DayAgentReasoningResponse, ...]
    role: Literal["reasoning", "coding"] = "reasoning"
    requests: list[DayAgentReasoningRequest] = field(default_factory=list)

    def next_step(self, request: DayAgentReasoningRequest) -> DayAgentReasoningResponse:
        self.requests.append(request)
        return self.responses[len(self.requests) - 1]


def _binding(action: DayAgentAction, evidence_ref: str) -> DayAgentToolBinding:
    def invoke(arguments: DayAgentToolArguments) -> str:
        return f'{{"symbol":"{arguments.root["symbol"]}","status":"current"}}'

    return DayAgentToolBinding(
        action=action,
        allowed_arguments=frozenset({"symbol"}),
        invoke=invoke,
        evidence_refs=(evidence_ref,),
    )


def _runtime(path: Path, client: DayAgentReasoningClient, *, max_steps: int) -> DayAgentRuntime:
    tools = DayAgentToolRuntime(
        bindings=(
            _binding(DayAgentAction.INSPECT_SITUATION, "evidence.situation"),
            _binding(DayAgentAction.READ_CATALYSTS, "evidence.catalyst"),
            _binding(DayAgentAction.COMPARE_LEADERS, "evidence.leader"),
        ),
        clock=lambda: NOW,
    )
    return DayAgentRuntime(
        store=DayAgentTaskStore(path / "day-agent.sqlite3"),
        reasoner=client,
        tools=tools,
        max_steps=max_steps,
        clock=lambda: NOW,
    )


def test_day_agent_chooses_multiple_tools_and_resumes_after_restart(tmp_path: Path) -> None:
    # Given
    client = ScriptedDayReasoner(
        (
            _tool_call(DayAgentAction.INSPECT_SITUATION, symbol="NVDA"),
            _tool_call(DayAgentAction.READ_CATALYSTS, symbol="NVDA"),
            _tool_call(DayAgentAction.COMPARE_LEADERS, symbol="NVDA"),
            _thesis_call(),
        )
    )

    # When
    first = run_day_agent_task(_runtime(tmp_path, client, max_steps=2), day_task())
    persisted_after_first = DayAgentTaskStore(tmp_path / "day-agent.sqlite3").reader().steps(first.task.task_id)
    resumed = run_day_agent_task(_runtime(tmp_path, client, max_steps=4), first.task)

    # Then
    assert first.state is DayAgentTaskState.WAITING
    assert len(persisted_after_first) == 4
    assert resumed.state is DayAgentTaskState.COMPLETED
    assert tuple(step.action for step in resumed.steps) == (
        DayAgentAction.INSPECT_SITUATION,
        DayAgentAction.READ_CATALYSTS,
        DayAgentAction.COMPARE_LEADERS,
        DayAgentAction.SUBMIT_TRADE_THESIS,
    )
    assert len(DayAgentTaskStore(tmp_path / "day-agent.sqlite3").reader().steps(resumed.task.task_id)) == 8
    assert len(client.requests) == 4


def test_day_agent_budget_exhaustion_blocks_before_another_model_call(tmp_path: Path) -> None:
    # Given
    client = ScriptedDayReasoner((_tool_call(DayAgentAction.INSPECT_SITUATION, symbol="NVDA"),))
    task = day_task().model_copy(
        update={"budget": day_task().budget.model_copy(update={"remaining_model_calls": 1})}
    )

    # When
    result = run_day_agent_task(_runtime(tmp_path, client, max_steps=4), task)

    # Then
    assert result.state is DayAgentTaskState.BLOCKED
    assert result.task.terminal_reason == "day_agent_model_budget_exhausted"
    assert len(client.requests) == 1
    assert result.task.budget.remaining_model_calls == 0


def test_day_agent_runtime_budget_decreases_for_model_and_tool_elapsed_time(tmp_path: Path) -> None:
    # Given
    client = ScriptedDayReasoner((_tool_call(DayAgentAction.INSPECT_SITUATION, symbol="NVDA"),))
    moments = iter((NOW, NOW + dt.timedelta(seconds=2), NOW + dt.timedelta(seconds=2), NOW + dt.timedelta(seconds=5)))
    runtime = _runtime(tmp_path, client, max_steps=1)
    measured = DayAgentRuntime(
        store=runtime.store,
        reasoner=runtime.reasoner,
        tools=runtime.tools,
        max_steps=runtime.max_steps,
        clock=lambda: next(moments),
    )

    # When
    result = run_day_agent_task(measured, day_task())

    # Then
    assert result.task.budget.remaining_runtime_seconds == 55


def test_restart_dispatches_persisted_decision_without_duplicate_model_call(tmp_path: Path) -> None:
    # Given
    client = ScriptedDayReasoner((_tool_call(DayAgentAction.INSPECT_SITUATION, symbol="NVDA"),))
    dispatches: list[str] = []

    def interrupted(arguments: DayAgentToolArguments) -> str:
        dispatches.append(arguments.root["symbol"])
        if len(dispatches) == 1:
            raise KeyboardInterrupt
        return '{"status":"current","symbol":"NVDA"}'

    tools = DayAgentToolRuntime(
        bindings=(
            DayAgentToolBinding(
                action=DayAgentAction.INSPECT_SITUATION,
                allowed_arguments=frozenset({"symbol"}),
                invoke=interrupted,
                evidence_refs=("evidence.situation",),
            ),
        ),
        clock=lambda: NOW,
    )
    runtime = DayAgentRuntime(
        store=DayAgentTaskStore(tmp_path / "day-agent.sqlite3"),
        reasoner=client,
        tools=tools,
        max_steps=1,
        clock=lambda: NOW,
    )
    with pytest.raises(KeyboardInterrupt):
        _ = run_day_agent_task(runtime, day_task())

    # When
    resumed = run_day_agent_task(runtime, day_task())

    # Then
    assert resumed.state is DayAgentTaskState.WAITING
    assert len(client.requests) == 1
    assert dispatches == ["NVDA", "NVDA"]
    assert len(DayAgentTaskStore(tmp_path / "day-agent.sqlite3").reader().steps(resumed.task.task_id)) == 2


@pytest.mark.parametrize(
    "forbidden",
    ("unknown", "provider", "credential", "account", "position", "order", "sizing", "mutation"),
)
def test_tool_runtime_denies_undeclared_or_unknown_authority(forbidden: str) -> None:
    # Given
    runtime = DayAgentToolRuntime(
        bindings=(_binding(DayAgentAction.INSPECT_SITUATION, "evidence.situation"),),
        clock=lambda: NOW,
    )
    action = "not_a_tool" if forbidden == "unknown" else DayAgentAction.INSPECT_SITUATION
    call = _tool_call(DayAgentAction.INSPECT_SITUATION, symbol="NVDA").model_copy(
        update={
            "action": action,
            "arguments": DayAgentToolArguments({"symbol": "NVDA", forbidden: "denied"}),
        }
    )

    # When / Then
    with pytest.raises(DayAgentToolRuntimeError, match="day_agent_tool_authority_denied"):
        runtime.dispatch(call)


def test_tool_runtime_import_boundary_has_no_broker_authority_names() -> None:
    # Given / When
    import trading_agent.day_agent_tool_runtime as module

    names = tuple(name.lower() for name in module.__dict__)

    # Then
    forbidden = ("alpaca_paper", "paper_mutation", "order", "account", "position", "balance", "credential")
    assert all(term not in name for name in names for term in forbidden)


def test_reasoning_role_is_required_for_trade_thesis(tmp_path: Path) -> None:
    # Given
    client = ScriptedDayReasoner((_thesis_call(),), role="coding")

    # When
    result = run_day_agent_task(_runtime(tmp_path, client, max_steps=1), day_task())

    # Then
    assert result.state is DayAgentTaskState.BLOCKED
    assert result.task.terminal_reason == "day_agent_role_authority_denied"


def test_tool_observation_is_bounded_hashed_and_timezone_aware() -> None:
    # Given
    runtime = DayAgentToolRuntime(
        bindings=(_binding(DayAgentAction.INSPECT_SITUATION, "evidence.situation"),),
        clock=lambda: NOW.astimezone(dt.timezone(dt.timedelta(hours=9))),
    )

    # When
    observation = runtime.dispatch(_tool_call(DayAgentAction.INSPECT_SITUATION, symbol="NVDA"))

    # Then
    assert observation.observed_at.tzinfo is not None
    assert observation.content_sha256 in observation.evidence_refs
    assert len(observation.bounded_json.encode()) <= 16_384
    assert inspect.isclass(type(observation))
