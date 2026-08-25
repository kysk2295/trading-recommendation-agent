from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
import stat
from pathlib import Path

import pytest

import trading_agent.autonomous_task_store as task_store
from trading_agent.autonomous_task_models import (
    AutonomousAgentRole,
    AutonomousResearchTask,
    AutonomousRunBudget,
    AutonomousTaskState,
    AutonomousTaskStep,
    InvalidAutonomousTaskFieldError,
    autonomous_task_id,
)
from trading_agent.autonomous_task_store import (
    AutonomousTaskConflictError,
    AutonomousTaskStore,
    InvalidAutonomousTaskStoreError,
)
from trading_agent.research_agent_cycle_models import EvidenceId

NOW = dt.datetime(2026, 8, 26, 14, 30, tzinfo=dt.UTC)
ROOT = EvidenceId(hashlib.sha256(b"root").hexdigest())
OTHER = EvidenceId(hashlib.sha256(b"other").hexdigest())


def task_fixture(**updates: object) -> AutonomousResearchTask:
    payload: dict[str, object] = {
        "task_id": autonomous_task_id("day_trading", "kr_equities", ROOT),
        "goal": "Assess current-session catalyst evidence for Samsung Electronics.",
        "owner_role": AutonomousAgentRole.SUPERVISOR,
        "agent_family_id": "day_trading",
        "market_scope": "kr_equities",
        "state": AutonomousTaskState.QUEUED,
        "priority": 50,
        "root_source_evidence_id": ROOT,
        "source_evidence_ids": (ROOT,),
        "evidence_refs": ("evidence:root",),
        "current_plan": ("observe_market",),
        "agent_version": "supervisor-v1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(updates)
    return AutonomousResearchTask.model_validate(payload)


def step_fixture(task: AutonomousResearchTask, **updates: object) -> AutonomousTaskStep:
    payload: dict[str, object] = {
        "task_id": task.task_id,
        "sequence": 1,
        "role": AutonomousAgentRole.MARKET_OBSERVER,
        "agent_family_id": task.agent_family_id,
        "market_scope": task.market_scope,
        "root_source_evidence_id": task.root_source_evidence_id,
        "agent_version": task.agent_version,
        "state": AutonomousTaskState.OBSERVING,
        "source_evidence_ids": task.source_evidence_ids,
        "evidence_refs": task.evidence_refs,
        "budget": AutonomousRunBudget(
            remaining_model_calls=12,
            remaining_tool_calls=24,
            remaining_runtime_seconds=300,
        ),
        "occurred_at": NOW,
    }
    payload.update(updates)
    return AutonomousTaskStep.model_validate(payload)


def task_for(root: EvidenceId, **updates: object) -> AutonomousResearchTask:
    values = {
        "task_id": autonomous_task_id("day_trading", "kr_equities", root),
        "root_source_evidence_id": root,
        "source_evidence_ids": (root,),
    }
    values.update(updates)
    return task_fixture(**values)


def test_task_and_step_exact_replay_survive_restart(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_fixture()
    step = step_fixture(task)

    # When
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task) is True
        assert writer.append_step(step) is True
        assert writer.create_task(task) is False
        assert writer.append_step(step) is False

    # Then
    reopened = AutonomousTaskStore(path).reader()
    assert reopened.task(task.task_id) is not None
    assert reopened.steps(task.task_id) == (step,)


def test_replays_and_missing_or_out_of_order_steps_fail_closed(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_fixture()
    conflicting_task = task.model_copy(update={"goal": "A different current-session research objective."})
    first = step_fixture(task)
    divergent = step_fixture(task, payload_json='{"state":"different"}')

    # When
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task)

        # Then
        with pytest.raises(AutonomousTaskConflictError, match="task_replay_conflict"):
            writer.create_task(conflicting_task)
        with pytest.raises(AutonomousTaskConflictError, match="task_missing"):
            writer.append_step(step_fixture(task_for(OTHER)))
        with pytest.raises(AutonomousTaskConflictError, match="step_sequence_invalid"):
            writer.append_step(step_fixture(task, sequence=2))
        assert writer.append_step(first)
        with pytest.raises(AutonomousTaskConflictError, match="step_replay_conflict"):
            writer.append_step(divergent)


def test_terminal_and_invalid_authority_steps_are_rejected(tmp_path: Path) -> None:
    # Given
    task = task_fixture()
    terminal = step_fixture(
        task,
        state=AutonomousTaskState.COMPLETED,
        terminal_reason="completed research lineage",
    )
    invalid_authority = step_fixture(task).model_copy(update={"agent_version": "supervisor-v2"})

    # When
    with AutonomousTaskStore(tmp_path / "autonomous.sqlite3").writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(terminal)

        # Then
        with pytest.raises(AutonomousTaskConflictError, match="terminal_task_step_rejected"):
            writer.append_step(step_fixture(task, sequence=2))
    with AutonomousTaskStore(tmp_path / "authority.sqlite3").writer() as writer:
        assert writer.create_task(task)
        with pytest.raises(InvalidAutonomousTaskFieldError, match="step_projection_authority_mismatch"):
            writer.append_step(invalid_authority)


def test_projection_preserves_root_and_source_lineage_after_multiple_steps(tmp_path: Path) -> None:
    # Given
    source_a = EvidenceId(hashlib.sha256(b"a").hexdigest())
    source_b = EvidenceId(hashlib.sha256(b"b").hexdigest())
    task = task_fixture(source_evidence_ids=tuple(sorted((ROOT, source_a))))
    first = step_fixture(
        task,
        source_evidence_ids=tuple(sorted((ROOT, source_b))),
        evidence_refs=("evidence:next",),
        working_memory_ids=("memory:next",),
        occurred_at=NOW + dt.timedelta(seconds=1),
    )
    blocked = step_fixture(
        task,
        sequence=2,
        role=AutonomousAgentRole.RESEARCH,
        state=AutonomousTaskState.BLOCKED,
        source_evidence_ids=tuple(sorted((ROOT, source_a, source_b))),
        evidence_refs=("evidence:blocked",),
        working_memory_ids=("memory:blocked",),
        occurred_at=NOW + dt.timedelta(seconds=2),
        next_wake_event="source_available",
        blocked_reason="source temporarily unavailable",
    )

    # When
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(first)
        assert writer.append_step(blocked)

    # Then
    projected = AutonomousTaskStore(path).reader().task(task.task_id)
    assert projected is not None
    assert projected.root_source_evidence_id == ROOT
    assert projected.source_evidence_ids == tuple(sorted((ROOT, source_a, source_b)))
    assert projected.owner_role is AutonomousAgentRole.RESEARCH
    assert projected.blocked_reason == "source temporarily unavailable"
    assert projected.evidence_refs == ("evidence:blocked",)
    assert projected.working_memory_ids == ("memory:blocked",)


def test_runnable_filters_wakes_events_and_terminal_tasks_in_priority_order(tmp_path: Path) -> None:
    # Given
    active = task_for(EvidenceId(hashlib.sha256(b"active").hexdigest()), priority=20)
    due = task_for(
        EvidenceId(hashlib.sha256(b"due").hexdigest()),
        priority=80,
        state=AutonomousTaskState.WAITING_TIME,
        next_wake_at=NOW + dt.timedelta(minutes=1),
        updated_at=NOW,
    )
    event = task_for(
        EvidenceId(hashlib.sha256(b"event").hexdigest()),
        priority=80,
        state=AutonomousTaskState.WAITING_EVENT,
        next_wake_event="news",
    )
    blocked = task_for(
        EvidenceId(hashlib.sha256(b"blocked").hexdigest()),
        priority=90,
        state=AutonomousTaskState.BLOCKED,
        next_wake_at=NOW + dt.timedelta(minutes=1),
        blocked_reason="awaiting a current-session source",
    )
    future = task_for(
        EvidenceId(hashlib.sha256(b"future").hexdigest()),
        state=AutonomousTaskState.WAITING_TIME,
        next_wake_at=NOW + dt.timedelta(days=1),
    )
    terminal = task_for(
        EvidenceId(hashlib.sha256(b"terminal").hexdigest()),
        state=AutonomousTaskState.COMPLETED,
        terminal_reason="completed lineage",
    )
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        for task in (active, due, event, blocked, future, terminal):
            assert writer.create_task(task)

    # When
    runnable = AutonomousTaskStore(path).reader().runnable(NOW + dt.timedelta(minutes=2), events={"news"})

    # Then
    assert tuple(task.task_id for task in runnable) == (blocked.task_id, event.task_id, due.task_id, active.task_id)
    with pytest.raises(InvalidAutonomousTaskStoreError, match="naive_time_input"):
        AutonomousTaskStore(path).reader().runnable(dt.datetime(2026, 8, 26, 14, 30), events=())


def test_runnable_wakes_blocked_task_only_for_matching_event(tmp_path: Path) -> None:
    # Given
    blocked = task_for(
        EvidenceId(hashlib.sha256(b"blocked-event").hexdigest()),
        state=AutonomousTaskState.BLOCKED,
        next_wake_event="source_restored",
        blocked_reason="awaiting a restored current-session source",
    )
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(blocked)

    # When
    reader = AutonomousTaskStore(path).reader()

    # Then
    assert reader.runnable(NOW, events={"source_restored"}) == (blocked,)
    assert reader.runnable(NOW, events={"different_event"}) == ()
    assert reader.runnable(NOW, events=()) == ()


def test_matching_open_tasks_uses_family_market_and_subject_intersection(tmp_path: Path) -> None:
    # Given
    matching = task_for(
        EvidenceId(hashlib.sha256(b"matching").hexdigest()),
        subject_refs=("symbol:005930",),
        updated_at=NOW + dt.timedelta(seconds=2),
    )
    older = task_for(
        EvidenceId(hashlib.sha256(b"older").hexdigest()),
        subject_refs=("symbol:005930", "theme:ai"),
        updated_at=NOW + dt.timedelta(seconds=1),
    )
    wrong_market = task_for(
        EvidenceId(hashlib.sha256(b"wrong-market").hexdigest()),
        market_scope="us_equities",
        task_id=autonomous_task_id(
            "day_trading", "us_equities", EvidenceId(hashlib.sha256(b"wrong-market").hexdigest())
        ),
        subject_refs=("symbol:005930",),
    )
    terminal = task_for(
        EvidenceId(hashlib.sha256(b"complete").hexdigest()),
        state=AutonomousTaskState.ABANDONED,
        terminal_reason="closed lineage",
        subject_refs=("symbol:005930",),
    )
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        for task in (matching, older, wrong_market, terminal):
            assert writer.create_task(task)

    # When
    found = AutonomousTaskStore(path).reader().matching_open_tasks("day_trading", "kr_equities", {"symbol:005930"})

    # Then
    assert tuple(task.task_id for task in found) == (matching.task_id, older.task_id)
    assert AutonomousTaskStore(path).reader().matching_open_tasks("day_trading", "kr_equities", ()) == ()


def test_store_security_append_only_reader_and_corruption_guards(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_fixture()
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task)

    # When / Then
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(path)
    with pytest.raises(InvalidAutonomousTaskStoreError, match="database_path_invalid"):
        AutonomousTaskStore(alias).reader().task(task.task_id)
    with task_store._reader_connection(path) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO autonomous_tasks VALUES ('x','x','x','x')")
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM autonomous_tasks WHERE task_id=?", (task.task_id,))
        connection.execute("DROP TRIGGER autonomous_tasks_no_update")
        connection.execute("UPDATE autonomous_tasks SET payload_json='{}' WHERE task_id=?", (task.task_id,))
        connection.execute(
            "CREATE TRIGGER autonomous_tasks_no_update BEFORE UPDATE ON autonomous_tasks "
            "BEGIN SELECT RAISE(ABORT, 'different'); END"
        )
    with pytest.raises(InvalidAutonomousTaskStoreError, match="schema_objects_invalid"):
        AutonomousTaskStore(path).reader().task(task.task_id)
    corrupt_path = tmp_path / "corrupt.sqlite3"
    with AutonomousTaskStore(corrupt_path).writer() as writer:
        assert writer.create_task(task)
    with sqlite3.connect(corrupt_path) as connection:
        connection.execute("INSERT INTO autonomous_tasks VALUES ('x','x','x','{}')")
    with pytest.raises(InvalidAutonomousTaskStoreError, match="task_payload_invalid"):
        AutonomousTaskStore(corrupt_path).reader().tasks()


def test_second_writer_lease_is_rejected(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task_fixture())

        # When / Then
        with (
            pytest.raises(InvalidAutonomousTaskStoreError, match="database_write_failed"),
            AutonomousTaskStore(path).writer(),
        ):
            pass
