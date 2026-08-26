from __future__ import annotations

import datetime as dt
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import active_children
from pathlib import Path

import pytest

import trading_agent._autonomous_supervisor_execution as execution
from tests.test_autonomous_supervisor_execution import (
    ConstantReasoner,
    MarkingReasoner,
    _completion,
    _runtime,
    now_clock,
    observed_tool,
)
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

_RACE_RELEASE = threading.Event()
_RACE_THREADS: list[threading.Thread] = []


@dataclass(frozen=True, slots=True)
class StubbornReasoner:
    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        _ = signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(0.005)


@dataclass(frozen=True, slots=True)
class LaunchRaceReasoner:
    callback: Path

    def __reduce__(self) -> tuple[type[LaunchRaceReasoner], tuple[Path]]:
        thread = threading.Thread(target=_RACE_RELEASE.wait)
        _RACE_THREADS.append(thread)
        thread.start()
        return LaunchRaceReasoner, (self.callback,)

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        self.callback.touch()
        return _completion()


@dataclass(frozen=True, slots=True)
class DescendantTool:
    pid_path: Path
    side_effect_path: Path

    def __call__(self, _args: AutonomousToolArguments) -> str:
        program = "import sys,time;from pathlib import Path;time.sleep(1);Path(sys.argv[1]).touch()"
        child = subprocess.Popen((sys.executable, "-c", program, str(self.side_effect_path)))
        self.pid_path.write_text(str(child.pid), encoding="ascii")
        while True:
            time.sleep(0.005)


@dataclass(frozen=True, slots=True)
class CallbackReasoner:
    callback: Callable[[], None]

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        self.callback()
        return _completion()


def _delayed_reasoner(callback: Path, delay_seconds: float) -> MarkingReasoner:
    time.sleep(delay_seconds)
    return MarkingReasoner(callback)


@dataclass(frozen=True, slots=True)
class DelayedReadyReasoner:
    callback: Path
    delay_seconds: float

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        del request
        self.callback.touch()
        return _completion()

    def __reduce__(
        self,
    ) -> tuple[Callable[[Path, float], MarkingReasoner], tuple[Path, float]]:
        return _delayed_reasoner, (self.callback, self.delay_seconds)


def test_sigterm_ignoring_reasoner_is_killed_and_reaped(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, StubbornReasoner(), timeout=1.0)
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
        runtime = _runtime(tmp_path, MarkingReasoner(callback))
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
    runtime = _runtime(tmp_path, ConstantReasoner(_completion()))
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


def test_spawn_launch_is_race_free_when_pickle_starts_threads(tmp_path: Path, recwarn: pytest.WarningsRecorder) -> None:
    callback = tmp_path / "race-callback"
    runtime = _runtime(tmp_path, LaunchRaceReasoner(callback))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    try:
        result = runtime.tick(task, NOW)
    finally:
        _RACE_RELEASE.set()
        for thread in _RACE_THREADS:
            thread.join()
        _RACE_THREADS.clear()
        _RACE_RELEASE.clear()

    assert result.status == "completed"
    assert callback.exists()
    assert not any(item.category is DeprecationWarning for item in recwarn)


def test_timeout_kills_callback_descendant_before_late_side_effect(tmp_path: Path) -> None:
    pid_path = tmp_path / "descendant.pid"
    side_effect = tmp_path / "descendant-late-side-effect"
    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read evidence through a bounded process-group-contained tool callback.",
    )
    runtime = _runtime(tmp_path, ConstantReasoner(call), DescendantTool(pid_path, side_effect), timeout=0.3)
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

    result = runtime.tick(task, NOW)

    assert result.status == "blocked"
    assert not active_children()


def test_pre_ready_timeout_reaps_direct_child_without_late_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    callback = tmp_path / "late-reasoner-callback"
    runtime = _runtime(tmp_path, DelayedReadyReasoner(callback, 0.3))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    before = {child.pid for child in active_children()}
    monkeypatch.setattr(execution, "_STARTUP_SECONDS", 0.05)

    result = runtime.tick(task, NOW)
    time.sleep(0.4)

    assert result.status == "blocked"
    assert not callback.exists()
    assert {child.pid for child in active_children()} == before
