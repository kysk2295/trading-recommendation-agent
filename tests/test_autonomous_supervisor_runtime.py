from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tests.autonomous_supervisor_fixtures import fixture_reasoner, now_clock, observed_tool, zero_clock
from tests.test_autonomous_task_models import NOW, OTHER, task_fixture
from tests.test_autonomous_task_store import task_for
from trading_agent._autonomous_supervisor_steps import parse_payload
from trading_agent.autonomous_memory_models import AutonomousMemoryScope
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AutonomousComplete,
    AutonomousDefer,
    AutonomousDelegate,
    AutonomousRecordMemory,
    AutonomousSubmitArtifact,
    AutonomousToolArguments,
    AutonomousToolCall,
)
from trading_agent.autonomous_reasoning_codec import AutonomousStructuredReasoner
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskState
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolBinding, AutonomousToolRuntime
from trading_agent.research_agent_cycle_models import EvidenceId

type SlowResponse = AutonomousToolCall | AutonomousDelegate | AutonomousRecordMemory | AutonomousSubmitArtifact


def _runtime(tmp_path: Path, reasoner: AutonomousStructuredReasoner) -> AutonomousSupervisorRuntime:
    tools = AutonomousToolRuntime(
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
        worker_modules=frozenset({"tests.autonomous_supervisor_fixtures"}),
    )
    return AutonomousSupervisorRuntime(
        tasks=AutonomousTaskStore(tmp_path / "tasks.sqlite3"),
        memories=AutonomousMemoryStore(tmp_path / "memories.sqlite3"),
        reasoner=reasoner,
        tools=tools,
        wall_clock=now_clock,
        monotonic=zero_clock,
    )


def test_tick_persists_multistep_workflow_and_wake(tmp_path: Path) -> None:
    # Given: a durable task and five typed decisions crossing tool, role, and memory boundaries.
    task = task_fixture(subject_refs=("symbol:005930",))
    call = AutonomousToolCall(
        tool_name="evidence.read",
        args=AutonomousToolArguments({"evidence_id": str(task.root_source_evidence_id)}),
        reason="Inspect the root evidence before delegating the bounded research task.",
    )
    wake = NOW + dt.timedelta(hours=1)
    reasoner = fixture_reasoner(
        tmp_path,
        (
            call,
            AutonomousDelegate(
                role=AutonomousAgentRole.RESEARCH,
                objective="Research the observed evidence and retain only supported claims.",
                reason="The observed source requires a focused evidence review before criticism.",
            ),
            AutonomousRecordMemory(
                scope=AutonomousMemoryScope.WORK,
                memory_key="work.samsung.review",
                summary="The root evidence was observed and remains pending critical review.",
                fact_refs=("fact:observed",),
                subject_refs=("symbol:005930",),
                evidence_refs=("evidence:tool",),
                reason="Persist the evidence-linked work state before changing reviewer roles.",
            ),
            AutonomousDelegate(
                role=AutonomousAgentRole.CRITIC,
                objective="Critique the evidence-linked work memory for unsupported conclusions.",
                reason="Independent criticism is required before a later supervised conclusion.",
            ),
            AutonomousDefer(
                reason="Wait for the next scheduled evidence review window before continuing.",
                resume_condition="The scheduled evidence review window has opened for this task.",
                next_wake_at=wake,
            ),
        )
    )
    runtime = _runtime(tmp_path, reasoner)
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)

    # When: one supervisor tick runs the complete bounded slice.
    result = runtime.tick(task, NOW)

    # Then: every decision has a durable application and the task can restart from storage.
    projected = runtime.tasks.reader().task(task.task_id)
    assert result.status == "waiting"
    assert result.model_calls == 5
    assert result.tool_calls == 1
    assert projected is not None and projected.state is AutonomousTaskState.WAITING_TIME
    assert len(runtime.memories.reader().history("work.samsung.review")) == 1


def test_budget_exhaustion_waits_without_terminalizing(tmp_path: Path) -> None:
    # Given: a reasoner that repeatedly delegates between active roles.
    responses = tuple(
        AutonomousDelegate(
            role=AutonomousAgentRole.RESEARCH if index % 2 == 0 else AutonomousAgentRole.CRITIC,
            objective=f"Perform bounded evidence review phase {index} before the next role handoff.",
            reason=f"Phase {index} needs an explicit durable role handoff for restart safety.",
        )
        for index in range(16)
    )
    reasoner = fixture_reasoner(tmp_path, responses)
    runtime = _runtime(tmp_path, reasoner)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)

    # When: the fresh per-tick model budget is exhausted.
    result = runtime.tick(task, NOW)

    # Then: the lifetime task waits one minute and remains nonterminal.
    projected = runtime.tasks.reader().task(task.task_id)
    assert result.status == "waiting"
    assert result.model_calls == 8
    assert result.next_wake_at == NOW + dt.timedelta(minutes=1)
    assert projected is not None and projected.state is AutonomousTaskState.WAITING_TIME
    second = runtime.tick(projected, NOW + dt.timedelta(minutes=1))
    assert second.model_calls == 8
    assert second.next_wake_at == NOW + dt.timedelta(minutes=2)


def test_artifact_no_trade_and_completion_keep_explicit_lifecycle(tmp_path: Path) -> None:
    # Given: an evaluating artifact precedes a nonterminal no-trade outcome and explicit completion.
    wake = NOW + dt.timedelta(minutes=5)
    reasoner = fixture_reasoner(
        tmp_path,
        (
            AutonomousSubmitArtifact(
                artifact_kind="context",
                artifact_json='{"status":"reviewed"}',
                evidence_refs=("evidence:root",),
                reason="Preserve the evidence-linked context before selecting a bounded outcome.",
            ),
            AutonomousSubmitArtifact(
                artifact_kind="no_trade",
                artifact_json='{"action":"no_trade"}',
                evidence_refs=("evidence:root",),
                next_wake_at=wake,
                reason="The current evidence does not support a trade and requires a later wake.",
            ),
            AutonomousComplete(
                summary="The deferred evidence review now has an explicit supported completion.",
                completion_evidence_refs=("evidence:root",),
                reason="The durable evidence supports closing this bounded autonomous task.",
            ),
        )
    )
    runtime = _runtime(tmp_path, reasoner)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)

    # When: the task evaluates, waits without terminalizing, and later completes.
    waiting = runtime.tick(task, NOW)
    completed = runtime.tick(task, wake)

    # Then: no-trade remains nonterminal while explicit evidenced completion is terminal.
    assert waiting.status == "waiting"
    assert completed.status == "completed"
    assert completed.model_calls == 1
    projected = runtime.tasks.reader().task(task.task_id)
    assert projected is not None and projected.state is AutonomousTaskState.COMPLETED
    assert projected.terminal_reason == "autonomous_task_completed"


def test_run_due_orders_event_and_time_tasks_and_excludes_terminal(tmp_path: Path) -> None:
    # Given: one higher-priority event wait and one lower-priority timed wait.
    high = task_for(OTHER, priority=90)
    low_root = EvidenceId(hashlib.sha256(b"low-task").hexdigest())
    low = task_for(low_root, priority=10)
    started_at = high.created_at
    due_at = started_at + dt.timedelta(minutes=2)
    reasoner = fixture_reasoner(
        tmp_path,
        (
            AutonomousDefer(
                reason="Wait for a named evidence event before resuming the high-priority task.",
                resume_condition="The named high-priority evidence event has been received.",
                next_wake_event="high_evidence",
            ),
            AutonomousComplete(
                summary="The high-priority event task completed with durable evidence lineage.",
                completion_evidence_refs=("evidence:root",),
                reason="The named event supplied the evidence needed for explicit completion.",
            ),
            AutonomousDefer(
                reason="Wait for the deterministic scheduled time before resuming this task.",
                resume_condition="The deterministic scheduled task wake time has been reached.",
                next_wake_at=due_at,
            ),
            AutonomousComplete(
                summary="The scheduled task completed with durable evidence lineage.",
                completion_evidence_refs=("evidence:root",),
                reason="The scheduled wake supplied the boundary needed for completion.",
            ),
        ),
        priority_routes=True,
    )
    runtime = _runtime(tmp_path, reasoner)
    with runtime.tasks.writer() as writer:
        assert writer.create_task(high)
        assert writer.create_task(low)
    assert runtime.tick(high, started_at).status == "waiting"
    assert runtime.tick(low, started_at).status == "waiting"
    before = len(runtime.tasks.reader().steps(low.task_id))

    # When: a premature direct tick and then the matching event/time due run occur.
    assert runtime.tick(low, started_at + dt.timedelta(minutes=1)).status == "waiting"
    after_future = len(runtime.tasks.reader().steps(low.task_id))
    results = runtime.run_due(due_at, events=("high_evidence",))

    # Then: future waiting adds no step, due order is deterministic, and terminals are excluded.
    assert after_future == before
    assert len(runtime.tasks.reader().steps(low.task_id)) > before
    assert tuple(item.task_id for item in results) == (high.task_id, low.task_id)
    assert runtime.run_due(due_at, events=("high_evidence",)) == ()


@pytest.mark.parametrize(
    "response",
    (
        AutonomousToolCall(
            tool_name="evidence.read",
            args=AutonomousToolArguments({"evidence_id": "a" * 64}),
            reason="Read bounded evidence only within the active supervisor runtime slice.",
        ),
        AutonomousDelegate(
            role=AutonomousAgentRole.RESEARCH,
            objective="Research the evidence only within the active supervisor runtime slice.",
            reason="A role handoff must wait when the current runtime slice is exhausted.",
        ),
        AutonomousRecordMemory(
            scope=AutonomousMemoryScope.WORK,
            memory_key="work.slow.reasoner",
            summary="A slow model decision must not write memory outside its runtime slice.",
            fact_refs=("fact:slow",),
            evidence_refs=("evidence:root",),
            reason="The memory application must wait for the next fresh runtime slice.",
        ),
        AutonomousSubmitArtifact(
            artifact_kind="context",
            artifact_json='{"status":"slow"}',
            evidence_refs=("evidence:root",),
            reason="The artifact application must wait for the next fresh runtime slice.",
        ),
    ),
)
def test_slow_reasoning_persists_decision_without_applying_response(tmp_path: Path, response: SlowResponse) -> None:
    # Given: monotonic time crosses the 120-second boundary during one model call.
    runtime = _runtime(tmp_path, fixture_reasoner(tmp_path, (response,)))
    runtime = replace(runtime, monotonic=iter((0.0, 0.0, 121.0)).__next__)
    task = task_fixture()
    with runtime.tasks.writer() as writer:
        assert writer.create_task(task)

    # When: the slow response returns after the slice deadline.
    result = runtime.tick(task, NOW)

    # Then: only the decision and budget wait are durable; no response application occurs.
    kinds = tuple(parse_payload(step.payload_json).kind for step in runtime.tasks.reader().steps(task.task_id))
    assert result.status == "waiting"
    assert kinds == ("decision", "wait")
    assert runtime.memories.reader().history("work.slow.reasoner") == ()
