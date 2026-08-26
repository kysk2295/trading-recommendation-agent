from __future__ import annotations

import datetime as dt
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import active_children
from pathlib import Path

import pytest

import trading_agent._autonomous_supervisor_execution as execution
from tests.autonomous_supervisor_fixtures import fixture_reasoner, fixture_tool
from tests.test_autonomous_supervisor_execution import _completion, _runtime, now_clock, observed_tool
from tests.test_autonomous_task_models import NOW, task_fixture
from trading_agent._autonomous_supervisor_execution import task_execution_lease
from trading_agent.autonomous_reasoning import (
    AutonomousReasoningRequest,
    AutonomousReasoningResponse,
    AutonomousToolArguments,
    AutonomousToolCall,
)
from trading_agent.autonomous_task_models import AutonomousAgentRole
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolRuntime


@dataclass(frozen=True, slots=True)
class CallbackReasoner:
    callback: Callable[[], None]

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        self.callback()
        return _completion()


@dataclass(frozen=True, slots=True)
class ExplosiveReduceReasoner:
    side_effect: Path

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        return _completion()

    def __reduce__(self) -> tuple[type[ExplosiveReduceReasoner], tuple[Path]]:
        self.side_effect.touch()
        time.sleep(5)
        return ExplosiveReduceReasoner, (self.side_effect,)


def test_sigterm_ignoring_reasoner_is_killed_and_reaped(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path, fixture_reasoner(tmp_path, (), behavior="stubborn", timeout_seconds=10.0), timeout=1.0
    )
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    before = {child.pid for child in active_children()}
    started = time.monotonic()

    result = runtime.tick(task, NOW)

    assert time.monotonic() - started < 2.0
    assert result.status == "waiting"
    assert {child.pid for child in active_children()} == before


def test_spawn_completes_while_background_thread_is_live(tmp_path: Path) -> None:
    callback = tmp_path / "reasoner-called-with-background-thread"
    release = threading.Event()
    background = threading.Thread(target=release.wait)
    background.start()

    try:
        runtime = _runtime(tmp_path, fixture_reasoner(tmp_path, (_completion(),), marker=callback))
        task = task_fixture()
        with runtime.tasks.writer() as writer:
            assert writer.create_task(task)
        started = time.monotonic()

        result = runtime.tick(task, NOW)

        assert time.monotonic() - started < 6.0
        assert result.status == "completed"
        assert callback.exists()
    finally:
        release.set()
        background.join()


def test_lease_contention_returns_nonterminal_retry_projection(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, fixture_reasoner(tmp_path, (_completion(),)))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)

    with task_execution_lease(runtime.tasks.path, task.task_id) as acquired:
        assert acquired
        result = runtime.tick(task, NOW)

    durable = runtime.tasks.reader().task(task.task_id)
    assert result.status == "waiting"
    assert result.next_wake_at == NOW + dt.timedelta(seconds=1)
    assert durable is not None and durable.state == task.state
    assert runtime.tasks.reader().steps(task.task_id) == ()


def test_spawn_launch_is_safe_while_background_thread_is_live(tmp_path: Path) -> None:
    callback = tmp_path / "race-callback"
    runtime = _runtime(tmp_path, fixture_reasoner(tmp_path, (_completion(),), marker=callback))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    result = runtime.tick(task, NOW)

    assert result.status == "completed"
    assert callback.exists()


def test_timeout_kills_callback_descendant_before_late_side_effect(tmp_path: Path) -> None:
    pid_path = tmp_path / "descendant.pid"
    side_effect = tmp_path / "descendant-late-side-effect"
    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read evidence through a bounded process-group-contained tool callback.",
    )
    runtime = _runtime(
        tmp_path,
        fixture_reasoner(tmp_path, (call,)),
        fixture_tool("descendant", primary=pid_path, secondary=side_effect),
        timeout=0.8,
    )
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)

    result = runtime.tick(task, NOW)
    descendant = int(pid_path.read_text(encoding="ascii"))
    time.sleep(0.4)

    assert result.status == "waiting"
    assert not side_effect.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(descendant, 0)
    safe_tools = AutonomousToolRuntime(
        (
            AutonomousToolBinding(
                name="evidence.read",
                allowed_roles=frozenset({AutonomousAgentRole.SUPERVISOR}),
                allowed_arguments=frozenset({"evidence_id"}),
                invoke=observed_tool,
                evidence_refs=("evidence:tool",),
            ),
        ),
        now_clock,
        worker_modules=frozenset({"tests.test_autonomous_supervisor_execution"}),
    )
    restarted = runtime.__class__(
        tasks=runtime.tasks,
        memories=runtime.memories,
        reasoner=runtime.reasoner,
        tools=safe_tools,
        wall_clock=runtime.wall_clock,
        monotonic=runtime.monotonic,
        max_steps=runtime.max_steps,
        execution_timeout_seconds=2.0,
    )
    resumed = restarted.tick(task, NOW + dt.timedelta(minutes=1))
    assert resumed.status == "waiting"


def test_unpickleable_reasoner_boundary_is_rejected_stably(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, CallbackReasoner(lambda: None))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    started = time.monotonic()

    result = runtime.tick(task, NOW)

    assert time.monotonic() - started < 0.5
    assert result.status == "blocked"
    assert not active_children()


def test_parent_rejects_reduce_hook_without_executing_it(tmp_path: Path) -> None:
    side_effect = tmp_path / "parent-reduce-hook-called"
    runtime = _runtime(tmp_path, ExplosiveReduceReasoner(side_effect))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    started = time.monotonic()

    result = runtime.tick(task, NOW)

    assert time.monotonic() - started < 0.5
    assert result.status == "blocked"
    assert not side_effect.exists()
    assert not active_children()
    durable = runtime.tasks.reader().task(task.task_id)
    assert durable is not None and durable.blocked_reason == "autonomous_reasoning_failed"


def test_pre_ready_timeout_reaps_direct_child_without_late_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callback = tmp_path / "late-reasoner-callback"
    runtime = _runtime(tmp_path, fixture_reasoner(tmp_path, (_completion(),), marker=callback))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    before = {child.pid for child in active_children()}
    monkeypatch.setattr(execution, "_STARTUP_SECONDS", 0.0)

    result = runtime.tick(task, NOW)
    time.sleep(0.4)

    assert result.status == "blocked"
    assert not callback.exists()
    assert {child.pid for child in active_children()} == before
