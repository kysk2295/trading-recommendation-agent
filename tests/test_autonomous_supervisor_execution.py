from __future__ import annotations

import datetime as dt
import os
import threading
import time
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path

from tests.test_autonomous_task_models import NOW, task_fixture
from trading_agent._autonomous_supervisor_steps import parse_payload
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AutonomousComplete,
    AutonomousDefer,
    AutonomousReasoningClient,
    AutonomousReasoningRequest,
    AutonomousReasoningResponse,
    AutonomousToolArguments,
    AutonomousToolCall,
)
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolRuntime


@dataclass(frozen=True, slots=True)
class ConstantReasoner:
    response: AutonomousReasoningResponse
    delay_seconds: float = 0.0

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        time.sleep(self.delay_seconds)
        return self.response


@dataclass(frozen=True, slots=True)
class HungReasoner:
    release_path: Path

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        while not self.release_path.exists():
            time.sleep(0.005)
        return _completion()


def _completion() -> AutonomousComplete:
    return AutonomousComplete(
        summary="The bounded supervisor task completed with durable evidence lineage.",
        completion_evidence_refs=("evidence:root",),
        reason="The durable evidence is sufficient for an explicit bounded completion.",
    )


def _runtime(
    tmp_path: Path,
    reasoner: AutonomousReasoningClient,
    invoke=lambda _args: '{"status":"observed"}',
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
        lambda: NOW,
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
    runtime = _runtime(tmp_path, HungReasoner(release), timeout=0.05)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    started = time.monotonic()
    result = runtime.tick(task, NOW)

    assert time.monotonic() - started < 0.5
    assert result.status == "waiting"
    assert tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id)) == (
        "wait",
    )


def test_hung_tool_is_terminated_without_late_side_effect(tmp_path: Path) -> None:
    release = tmp_path / "release-tool"
    side_effect = tmp_path / "late-side-effect"

    def hung_tool(_args) -> str:
        while not release.exists():
            time.sleep(0.005)
        side_effect.touch()
        return '{"status":"late"}'

    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read the bounded evidence through the authorized supervisor tool binding.",
    )
    runtime = _runtime(tmp_path, ConstantReasoner(call), hung_tool, timeout=0.05)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    started = time.monotonic()
    result = runtime.tick(task, NOW)
    time.sleep(0.05)

    kinds = tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id))
    assert time.monotonic() - started < 0.5
    assert result.status == "waiting"
    assert kinds == ("decision", "wait")
    assert not side_effect.exists()


def test_concurrent_ticks_hold_one_task_execution_lease(tmp_path: Path) -> None:
    model_calls = tmp_path / "model-calls"

    @dataclass(frozen=True, slots=True)
    class DivergentReasoner:
        def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
            del request
            with model_calls.open("a", encoding="utf-8") as stream:
                _ = stream.write("called\n")
            time.sleep(0.1)
            return AutonomousComplete(
                summary=f"The bounded task completed through isolated worker process {os.getpid()}.",
                completion_evidence_refs=("evidence:root",),
                reason="The durable evidence is sufficient for an explicit bounded completion.",
            )

    runtime = _runtime(tmp_path, DivergentReasoner())
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    context = get_context("fork")

    def run() -> None:
        _ = runtime.tick(task, NOW)

    workers = (context.Process(target=run), context.Process(target=run))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(1.0)

    kinds = tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id))
    assert not any(worker.is_alive() for worker in workers)
    assert all(worker.exitcode == 0 for worker in workers)
    assert model_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert kinds == ("decision", "completion")


def test_concurrent_ticks_invoke_and_apply_tool_once(tmp_path: Path) -> None:
    invocations = tmp_path / "tool-invocations"

    def tool(_args) -> str:
        with invocations.open("a", encoding="utf-8") as stream:
            _ = stream.write("invoked\n")
        time.sleep(0.1)
        return '{"status":"observed"}'

    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read the bounded evidence through the authorized supervisor tool binding.",
    )
    runtime = _runtime(tmp_path, ConstantReasoner(call), tool)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    context = get_context("fork")
    workers = tuple(context.Process(target=lambda: runtime.tick(task, NOW)) for _ in range(2))

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(1.0)

    kinds = tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id))
    assert all(worker.exitcode == 0 for worker in workers)
    assert invocations.read_text(encoding="utf-8").splitlines() == ["invoked"]
    assert kinds == ("decision", "observation", "wait")


def test_secondary_thread_tick_returns_without_forking_callback(tmp_path: Path) -> None:
    callback = tmp_path / "reasoner-called"

    @dataclass(frozen=True, slots=True)
    class MarkingReasoner:
        def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
            del request
            callback.touch()
            return _completion()

    runtime = _runtime(tmp_path, MarkingReasoner())
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    results: list[str] = []
    worker = threading.Thread(target=lambda: results.append(runtime.tick(task, NOW).status))

    worker.start()
    worker.join(0.5)

    assert not worker.is_alive()
    assert results == ["blocked"]
    assert not callback.exists()
