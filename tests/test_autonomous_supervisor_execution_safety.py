from __future__ import annotations

import datetime as dt
import signal
import threading
import time
from dataclasses import dataclass
from multiprocessing import active_children
from pathlib import Path

from tests.test_autonomous_supervisor_execution import ConstantReasoner, _completion, _runtime
from tests.test_autonomous_task_models import NOW, task_fixture
from trading_agent._autonomous_supervisor_execution import task_execution_lease
from trading_agent.autonomous_reasoning import (
    AutonomousReasoningRequest,
    AutonomousReasoningResponse,
)


def test_sigterm_ignoring_reasoner_is_killed_and_reaped(tmp_path: Path) -> None:
    @dataclass(frozen=True, slots=True)
    class StubbornReasoner:
        def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
            del request
            _ = signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                time.sleep(0.005)

    runtime = _runtime(tmp_path, StubbornReasoner(), timeout=0.05)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    before = {child.pid for child in active_children()}
    started = time.monotonic()

    result = runtime.tick(task, NOW)

    assert time.monotonic() - started < 0.5
    assert result.status == "waiting"
    assert {child.pid for child in active_children()} == before


def test_main_thread_rejects_fork_while_background_thread_is_live(tmp_path: Path) -> None:
    callback = tmp_path / "reasoner-called-with-background-thread"
    release = threading.Event()
    background = threading.Thread(target=release.wait)
    background.start()

    @dataclass(frozen=True, slots=True)
    class MarkingReasoner:
        def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
            del request
            callback.touch()
            return _completion()

    try:
        runtime = _runtime(tmp_path, MarkingReasoner())
        task = task_fixture()
        with runtime.tasks.writer() as writer:
            assert writer.create_task(task)
        started = time.monotonic()

        result = runtime.tick(task, NOW)

        assert time.monotonic() - started < 0.5
        assert result.status == "blocked"
        assert not callback.exists()
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
