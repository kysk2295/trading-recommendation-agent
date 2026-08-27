from __future__ import annotations

import datetime as dt
import functools
from pathlib import Path

import pytest

import trading_agent.autonomous_supervisor_service as supervisor_service
from tests.autonomous_supervisor_fixtures import fixture_reasoner
from trading_agent._autonomous_supervisor_steps import (
    DecisionPayload,
    FailurePayload,
    ObservationPayload,
    parsed_response,
    safe_payload,
)
from trading_agent.autonomous_reasoning import AutonomousDefer, AutonomousToolArguments, AutonomousToolCall
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolBinding,
    AutonomousToolExecutionContext,
    AutonomousToolRuntime,
)
from trading_agent.researcher_llm import LlmProposalClient


def tool_proposal_client(tmp_path: Path, wake_at: dt.datetime) -> LlmProposalClient:
    call = AutonomousToolCall(
        tool_name="memory.search",
        args=AutonomousToolArguments({"scope": "market", "subject_ref": "005930"}),
        reason="Search bounded market memory before the next research decision.",
    )
    defer = AutonomousDefer(
        reason="The bounded evidence read is complete and awaits the next review.",
        resume_condition="Resume at the next scheduled bounded evidence review.",
        next_wake_at=wake_at,
    )
    return fixture_reasoner(tmp_path, (call, defer)).client


def durable_tool_request_result_counts(database: Path, task_id: str) -> tuple[int, int, int]:
    store = AutonomousTaskStore(database)
    try:
        payloads = tuple(safe_payload(step) for step in store.reader().steps(task_id))
    finally:
        store.close()
    decisions = tuple(payload for payload in payloads if isinstance(payload, DecisionPayload))
    requests = sum(isinstance(parsed_response(payload), AutonomousToolCall) for payload in decisions)
    observations = sum(isinstance(payload, ObservationPayload) for payload in payloads)
    failures = sum(isinstance(payload, FailurePayload) and payload.source == "tool" for payload in payloads)
    return requests, observations, failures


def instrumented_memory_search_tool(
    args: AutonomousToolArguments,
    context: AutonomousToolExecutionContext,
    *,
    memory_database: str,
) -> str:
    from trading_agent.autonomous_supervisor_service import memory_search_tool

    result = memory_search_tool(args, context, memory_database=memory_database)
    marker = Path(memory_database).with_suffix(".tool-invocations")
    with marker.open("a", encoding="ascii") as stream:
        stream.write(f"{context.task_id}\n")
    return result


def instrument_memory_search(runtime: AutonomousToolRuntime, memory_database: Path) -> AutonomousToolRuntime:
    bindings = object.__getattribute__(runtime, "_bindings")
    clock = object.__getattribute__(runtime, "_clock")
    worker_modules = frozenset({"trading_agent.autonomous_supervisor_service", __name__})
    instrumented = tuple(
        AutonomousToolBinding(
            binding.name,
            binding.allowed_roles,
            binding.allowed_arguments,
            (
                functools.partial(instrumented_memory_search_tool, memory_database=str(memory_database))
                if binding.name == "memory.search"
                else binding.invoke
            ),
            binding.evidence_refs,
        )
        for binding in bindings.values()
        if binding.name in {"evidence.read", "memory.search", "task.history"}
    )
    return AutonomousToolRuntime(instrumented, clock, worker_modules=worker_modules)


def install_memory_search_instrumentation(monkeypatch: pytest.MonkeyPatch) -> None:
    build_tools = supervisor_service.build_foundation_tool_runtime
    monkeypatch.setattr(
        supervisor_service,
        "build_foundation_tool_runtime",
        lambda tasks, memories, browser=None, kr=None: instrument_memory_search(
            build_tools(tasks, memories, browser=browser, kr=kr), memories.path
        ),
    )
