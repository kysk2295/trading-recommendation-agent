from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tests.autonomous_supervisor_fixtures import fixture_reasoner, fixture_tool
from tests.test_autonomous_task_models import NOW, task_fixture
from trading_agent._autonomous_supervisor_execution import AutonomousExecutionCrash
from trading_agent._autonomous_supervisor_steps import parse_payload
from trading_agent.autonomous_memory_models import AutonomousMemoryScope
from trading_agent.autonomous_memory_store import (
    AutonomousMemoryStore,
    AutonomousMemoryWriter,
    InvalidAutonomousMemoryStoreError,
)
from trading_agent.autonomous_reasoning import (
    AutonomousDefer,
    AutonomousDelegate,
    AutonomousRecordMemory,
    AutonomousToolArguments,
    AutonomousToolCall,
)
from trading_agent.autonomous_supervisor_runtime import (
    AutonomousSupervisorRuntime,
    InvalidAutonomousSupervisorError,
)
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskState
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import (
    AutonomousToolBinding,
    AutonomousToolInvocationError,
    AutonomousToolRuntime,
)
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    ResearchAgentEvidenceV1,
    ResearchAgentTriggerKind,
)


class CrashSentinel(BaseException):
    pass


def _now() -> dt.datetime:
    return NOW


def _zero() -> float:
    return 0.0


def _empty_tool(_args: AutonomousToolArguments) -> str:
    return "{}"


def _observed_tool(_args: AutonomousToolArguments) -> str:
    return '{"status":"observed"}'


def _crash_tool(_args: AutonomousToolArguments) -> str:
    raise CrashSentinel


def _fail_tool(_args: AutonomousToolArguments) -> str:
    raise AutonomousToolInvocationError(reason="fixture_tool_failure")


def _runtime(
    tmp_path: Path,
    reasoner,
    invoke,
    *,
    max_steps: int = 12,
) -> AutonomousSupervisorRuntime:
    binding = AutonomousToolBinding(
        name="evidence.read",
        allowed_roles=frozenset({AutonomousAgentRole.SUPERVISOR}),
        allowed_arguments=frozenset({"evidence_id"}),
        invoke=invoke,
        evidence_refs=("evidence:tool",),
    )
    return AutonomousSupervisorRuntime(
        tasks=AutonomousTaskStore(tmp_path / "tasks.sqlite3"),
        memories=AutonomousMemoryStore(tmp_path / "memories.sqlite3"),
        reasoner=reasoner,
        tools=AutonomousToolRuntime(
            (binding,),
            _now,
            worker_modules=frozenset(
                {"test_autonomous_supervisor_recovery", "tests.autonomous_supervisor_fixtures"}
            ),
        ),
        wall_clock=_now,
        monotonic=_zero,
        max_steps=max_steps,
    )


def _call():
    return AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": "a" * 64}),
        reason="Read the bounded source evidence through the authorized host tool binding.",
    )


def test_restart_replays_unapplied_tool_decision_once(tmp_path: Path) -> None:
    # Given: tool dispatch crashes the process after its model decision is durable.
    task = task_fixture()
    first_reasoner = fixture_reasoner(tmp_path, (_call(),))
    crashed = _runtime(tmp_path, first_reasoner, _crash_tool, max_steps=1)
    with crashed.tasks.writer() as writer:
        assert writer.create_task(task)

    # When: a new runtime resumes from the same stores.
    with pytest.raises(AutonomousExecutionCrash):
        crashed.tick(task, NOW)
    restarted_reasoner = fixture_reasoner(tmp_path, ())
    restarted = _runtime(tmp_path, restarted_reasoner, _observed_tool, max_steps=1)
    result = restarted.tick(task, NOW)

    # Then: the original decision is reused and exactly one observation becomes durable.
    kinds = tuple(parse_payload(step.payload_json).kind for step in restarted.tasks.reader().steps(task.task_id))
    assert result.status == "waiting"
    assert kinds.count("decision") == 1
    assert kinds.count("observation") == 1


def test_restart_replays_decision_deferred_by_runtime_deadline(tmp_path: Path) -> None:
    # Given: a slow model decision is durable behind a one-minute budget wait.
    decision = AutonomousDelegate(
        role=AutonomousAgentRole.RESEARCH,
        objective="Resume the durable research handoff during the next fresh runtime slice.",
        reason="The expired slice must preserve this decision without applying it early.",
    )
    first_reasoner = fixture_reasoner(tmp_path, (decision,))
    runtime = _runtime(tmp_path, first_reasoner, _empty_tool, max_steps=1)
    runtime = replace(runtime, monotonic=iter((0.0, 0.0, 121.0)).__next__)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    assert runtime.tick(task, NOW).status == "waiting"
    restarted_reasoner = fixture_reasoner(tmp_path, ())
    restarted = replace(runtime, reasoner=restarted_reasoner, monotonic=_zero)

    # When: the one-minute wait becomes due in a fresh process-style runtime.
    result = restarted.tick(task, NOW + dt.timedelta(minutes=1))

    # Then: the durable decision applies without another model decision.
    kinds = tuple(parse_payload(step.payload_json).kind for step in restarted.tasks.reader().steps(task.task_id))
    assert result.status == "waiting"
    assert kinds == ("decision", "wait", "delegate", "wait")


def test_restart_reuses_memory_written_before_application_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: memory append commits, then the process crashes before its task application step.
    request = AutonomousRecordMemory(
        scope=AutonomousMemoryScope.WORK,
        memory_key="work.crash.recovery",
        summary="A committed memory must be replayed idempotently after a process interruption.",
        fact_refs=("fact:durable",),
        evidence_refs=("evidence:root",),
        reason="The task reducer must link the already committed memory without a new version.",
    )
    reasoner = fixture_reasoner(tmp_path, (request,))
    runtime = _runtime(tmp_path, reasoner, _empty_tool, max_steps=1)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    original = AutonomousMemoryWriter.append
    crashed = False

    def append_then_crash(writer: AutonomousMemoryWriter, record):
        nonlocal crashed
        result = original(writer, record)
        if not crashed:
            crashed = True
            raise CrashSentinel
        return result

    monkeypatch.setattr(AutonomousMemoryWriter, "append", append_then_crash)

    # When: the runtime restarts and applies the durable decision.
    with pytest.raises(CrashSentinel):
        runtime.tick(task, NOW)
    restarted_reasoner = fixture_reasoner(tmp_path, ())
    restarted = _runtime(tmp_path, restarted_reasoner, _empty_tool, max_steps=1)
    result = restarted.tick(task, NOW)

    # Then: one memory version and one application step exist.
    records = restarted.memories.reader().history(request.memory_key)
    kinds = tuple(parse_payload(step.payload_json).kind for step in restarted.tasks.reader().steps(task.task_id))
    assert result.status == "waiting"
    assert len(records) == 1 and records[0].version == 1
    assert kinds.count("memory") == 1


def test_five_reasoning_failures_back_off_without_abandoning(tmp_path: Path) -> None:
    # Given: a typed reasoner fails on every due retry.
    reasoner = fixture_reasoner(tmp_path, ())
    runtime = _runtime(tmp_path, reasoner, _empty_tool)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)

    # When: five due slices encounter the same typed failure.
    now = NOW
    results = []
    for _delay in (15, 60, 240, 720, 1440):
        result = runtime.tick(task, now)
        results.append(result)
        now = result.next_wake_at or now

    # Then: retry five remains blocked for 24 hours with immutable root lineage.
    projected = runtime.tasks.reader().task(task.task_id)
    assert [
        int((item.next_wake_at - at).total_seconds() / 60)
        for item, at in zip(results, (NOW, *[r.next_wake_at for r in results[:-1]]), strict=True)
    ] == [15, 60, 240, 720, 1440]
    assert projected is not None and projected.state is AutonomousTaskState.BLOCKED
    assert projected.root_source_evidence_id == task.root_source_evidence_id
    assert projected.source_evidence_ids == task.source_evidence_ids
    assert projected.terminal_reason is None


def test_tool_and_memory_failures_persist_stable_blocked_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: authorized tool invocation and memory persistence each fail through typed boundaries.
    tool_task = task_fixture()

    tool_runtime = _runtime(tmp_path / "tool", fixture_reasoner(tmp_path, (_call(),)), fixture_tool("fail"))
    with tool_runtime.tasks.writer() as writer:
        assert writer.create_task(tool_task)
    memory_request = AutonomousRecordMemory(
        scope=AutonomousMemoryScope.WORK,
        memory_key="work.failure.retry",
        summary="A typed persistence failure must produce a stable blocked retry transition.",
        fact_refs=("fact:failure",),
        evidence_refs=("evidence:root",),
        reason="Persist the work memory or retain a deterministic retry wake for recovery.",
    )
    memory_runtime = _runtime(
        tmp_path / "memory", fixture_reasoner(tmp_path, (memory_request,)), fixture_tool("empty")
    )
    with memory_runtime.tasks.writer() as writer:
        assert writer.create_task(tool_task)

    def fail_memory(_writer: AutonomousMemoryWriter, _record) -> bool:
        raise InvalidAutonomousMemoryStoreError(reason="fixture_memory_failure")

    monkeypatch.setattr(AutonomousMemoryWriter, "append", fail_memory)

    # When: each task executes one fresh slice.
    tool_result = tool_runtime.tick(tool_task, NOW)
    memory_result = memory_runtime.tick(tool_task, NOW)

    # Then: both failures use the first 15-minute retry and expose no raw provider text.
    assert tool_result.status == "blocked"
    assert memory_result.status == "blocked"
    assert tool_result.next_wake_at == NOW + dt.timedelta(minutes=15)
    assert memory_result.next_wake_at == NOW + dt.timedelta(minutes=15)
    blocked_tool = tool_runtime.tasks.reader().task(tool_task.task_id)
    blocked_memory = memory_runtime.tasks.reader().task(tool_task.task_id)
    assert blocked_tool is not None and blocked_tool.blocked_reason == "autonomous_tool_failed"
    assert blocked_memory is not None and blocked_memory.blocked_reason == "autonomous_memory_failed"


def test_evidence_admission_replays_and_wakes_without_changing_root(tmp_path: Path) -> None:
    # Given: a task is waiting and a new same-family same-market evidence record arrives.
    defer = AutonomousDefer(
        reason="Wait for new source evidence before continuing this bounded research task.",
        resume_condition="New source evidence is admitted to the durable task lineage.",
        next_wake_event="new_evidence",
    )
    runtime = _runtime(tmp_path, fixture_reasoner(tmp_path, (defer,)), fixture_tool("empty"))
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)
    assert runtime.tick(task, NOW).status == "waiting"
    bounded = '{"symbol":"005930"}'
    evidence = ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(hashlib.sha256(b"new-evidence").hexdigest()),
        agent_family_id=task.agent_family_id,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key="fixture.new-evidence",
        evidence_refs=("evidence:new",),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=hashlib.sha256(bounded.encode()).hexdigest(),
        market_id=task.market_scope,
        bounded_payload_json=bounded,
        subject_refs=("symbol:005930",),
    )

    # When: the evidence is admitted twice exactly.
    assert runtime.admit_evidence(task.task_id, evidence, NOW + dt.timedelta(seconds=1)) is True
    assert runtime.admit_evidence(task.task_id, evidence, NOW + dt.timedelta(seconds=2)) is False
    conflict = evidence.model_copy(update={"source_key": "fixture.conflicting-evidence"})
    with pytest.raises(InvalidAutonomousSupervisorError, match="autonomous_evidence_replay_conflict"):
        runtime.admit_evidence(task.task_id, conflict, NOW + dt.timedelta(seconds=2))

    # Then: admission wakes the task and preserves its original root authority.
    projected = runtime.tasks.reader().task(task.task_id)
    assert projected is not None and projected.state is AutonomousTaskState.QUEUED
    assert projected.root_source_evidence_id == task.root_source_evidence_id
    assert evidence.evidence_id in projected.source_evidence_ids
    wrong = evidence.model_copy(update={"market_id": "us_equities"})
    with pytest.raises(InvalidAutonomousSupervisorError, match="autonomous_evidence_market_mismatch"):
        runtime.admit_evidence(task.task_id, wrong, NOW + dt.timedelta(seconds=3))
