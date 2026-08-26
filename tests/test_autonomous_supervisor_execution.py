from __future__ import annotations

import datetime as dt
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from tests.test_autonomous_task_models import NOW, task_fixture
from trading_agent._autonomous_supervisor_steps import parse_payload
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AUTONOMOUS_REASONING_RESPONSE_ADAPTER,
    AutonomousComplete,
    AutonomousDefer,
    AutonomousReasoningClient,
    AutonomousReasoningRequest,
    AutonomousReasoningResponse,
    AutonomousToolArguments,
    AutonomousToolCall,
)
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousSupervisorTickResult
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolRuntime


@dataclass(frozen=True, slots=True, init=False)
class ConstantReasoner:
    response_json: str
    delay_seconds: float

    def __init__(self, response: AutonomousReasoningResponse, delay_seconds: float = 0.0) -> None:
        object.__setattr__(self, "response_json", response.model_dump_json())
        object.__setattr__(self, "delay_seconds", delay_seconds)

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        time.sleep(self.delay_seconds)
        return AUTONOMOUS_REASONING_RESPONSE_ADAPTER.validate_json(self.response_json)


@dataclass(frozen=True, slots=True)
class HungReasoner:
    release_path: Path

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        while not self.release_path.exists():
            time.sleep(0.005)
        return _completion()


@dataclass(frozen=True, slots=True)
class HungTool:
    release_path: Path
    side_effect_path: Path

    def __call__(self, _args: AutonomousToolArguments) -> str:
        while not self.release_path.exists():
            time.sleep(0.005)
        self.side_effect_path.touch()
        return '{"status":"late"}'


@dataclass(frozen=True, slots=True)
class DivergentReasoner:
    model_calls: Path

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        with self.model_calls.open("a", encoding="utf-8") as stream:
            _ = stream.write("called\n")
        time.sleep(0.1)
        return AutonomousComplete(
            summary=f"The bounded task completed through isolated worker process {os.getpid()}.",
            completion_evidence_refs=("evidence:root",),
            reason="The durable evidence is sufficient for an explicit bounded completion.",
        )


@dataclass(frozen=True, slots=True)
class RecordingTool:
    invocations: Path

    def __call__(self, _args: AutonomousToolArguments) -> str:
        with self.invocations.open("a", encoding="utf-8") as stream:
            _ = stream.write("invoked\n")
        time.sleep(0.1)
        return '{"status":"observed"}'


@dataclass(frozen=True, slots=True)
class MarkingReasoner:
    callback: Path

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        self.callback.touch()
        return _completion()


def observed_tool(_args: AutonomousToolArguments) -> str:
    return '{"status":"observed"}'


def now_clock() -> dt.datetime:
    return NOW


def _completion() -> AutonomousComplete:
    return AutonomousComplete(
        summary="The bounded supervisor task completed with durable evidence lineage.",
        completion_evidence_refs=("evidence:root",),
        reason="The durable evidence is sufficient for an explicit bounded completion.",
    )


def _runtime(
    tmp_path: Path,
    reasoner: AutonomousReasoningClient,
    invoke=observed_tool,
    *,
    timeout: float = 120.0,
) -> AutonomousSupervisorRuntime:
    tools = AutonomousToolRuntime(
        (
            AutonomousToolBinding(
                name="evidence.read",
                allowed_roles=frozenset({AutonomousAgentRole.SUPERVISOR}),
                allowed_arguments=frozenset({"evidence_id"}),
                invoke=invoke,
                evidence_refs=("evidence:tool",),
            ),
        ),
        now_clock,
    )
    return AutonomousSupervisorRuntime(
        tasks=AutonomousTaskStore(tmp_path / "tasks.sqlite3"),
        memories=AutonomousMemoryStore(tmp_path / "memories.sqlite3"),
        reasoner=reasoner,
        tools=tools,
        wall_clock=lambda: NOW,
        monotonic=time.monotonic,
        max_steps=1,
        execution_timeout_seconds=timeout,
    )


def test_direct_tick_waiting_event_requires_matching_event(tmp_path: Path) -> None:
    event = "source.admitted"
    waiting = AutonomousDefer(
        reason="Wait for the named source admission before resuming this durable task.",
        resume_condition="A matching source admission event is supplied to the supervisor.",
        next_wake_event=event,
    )
    runtime = _runtime(tmp_path, ConstantReasoner(waiting))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    assert runtime.tick(task, NOW).status == "waiting"
    before = runtime.tasks.reader().steps(task.task_id)

    absent = runtime.tick(task, NOW + dt.timedelta(seconds=1))

    assert absent.status == "waiting"
    assert absent.model_calls == 0
    assert runtime.tasks.reader().steps(task.task_id) == before


def test_hung_reasoner_is_terminated_within_configured_deadline(tmp_path: Path) -> None:
    release = tmp_path / "release-reasoner"
    runtime = _runtime(tmp_path, HungReasoner(release), timeout=1.0)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    started = time.monotonic()
    result = runtime.tick(task, NOW)

    assert time.monotonic() - started < 2.0
    assert result.status == "waiting"
    assert tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id)) == (
        "wait",
    )


def test_hung_tool_is_terminated_without_late_side_effect(tmp_path: Path) -> None:
    release = tmp_path / "release-tool"
    side_effect = tmp_path / "late-side-effect"

    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read the bounded evidence through the authorized supervisor tool binding.",
    )
    runtime = _runtime(tmp_path, ConstantReasoner(call), HungTool(release, side_effect), timeout=1.0)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    started = time.monotonic()
    result = runtime.tick(task, NOW)
    time.sleep(0.05)

    kinds = tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id))
    assert time.monotonic() - started < 3.0
    assert result.status == "waiting"
    assert kinds == ("decision", "wait")
    assert not side_effect.exists()


def test_concurrent_ticks_hold_one_task_execution_lease(tmp_path: Path) -> None:
    model_calls = tmp_path / "model-calls"

    runtime = _runtime(tmp_path, DivergentReasoner(model_calls))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    results: list[AutonomousSupervisorTickResult] = []

    def run() -> None:
        results.append(runtime.tick(task, NOW))

    workers = (threading.Thread(target=run), threading.Thread(target=run))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(6.0)

    kinds = tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id))
    assert not any(worker.is_alive() for worker in workers)
    assert tuple(sorted(result.status for result in results)) == ("completed", "waiting")
    assert sum(result.next_wake_at == NOW + dt.timedelta(seconds=1) for result in results) == 1
    assert model_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert kinds == ("decision", "completion")


def test_concurrent_ticks_invoke_and_apply_tool_once(tmp_path: Path) -> None:
    invocations = tmp_path / "tool-invocations"

    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read the bounded evidence through the authorized supervisor tool binding.",
    )
    runtime = _runtime(tmp_path, ConstantReasoner(call), RecordingTool(invocations))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    results: list[AutonomousSupervisorTickResult] = []

    def run() -> None:
        results.append(runtime.tick(task, NOW))

    workers = (threading.Thread(target=run), threading.Thread(target=run))

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(6.0)

    kinds = tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id))
    assert not any(worker.is_alive() for worker in workers)
    assert [result.status for result in results] == ["waiting", "waiting"], (results, kinds, invocations.exists())
    assert {result.next_wake_at for result in results} == {
        NOW + dt.timedelta(seconds=1),
        NOW + dt.timedelta(minutes=1),
    }
    assert invocations.read_text(encoding="utf-8").splitlines() == ["invoked"]
    assert kinds == ("decision", "observation", "wait")


def test_secondary_thread_tick_returns_without_forking_callback(tmp_path: Path) -> None:
    callback = tmp_path / "reasoner-called"

    runtime = _runtime(tmp_path, MarkingReasoner(callback))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    results: list[str] = []
    worker = threading.Thread(target=lambda: results.append(runtime.tick(task, NOW).status))

    worker.start()
    worker.join(6.0)

    assert not worker.is_alive()
    assert results == ["completed"]
    assert callback.exists()
