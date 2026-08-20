from __future__ import annotations

import datetime as dt
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.day_agent_support import NOW, day_step, day_task
from trading_agent.day_agent_task_models import DayAgentAction, DayAgentBudget, DayAgentTaskState, DayAgentTaskStep
from trading_agent.day_agent_task_store import (
    DayAgentTaskConflictError,
    DayAgentTaskStore,
    InvalidDayAgentTaskStoreError,
)


def test_research_task_survives_restart_without_duplicate_step(tmp_path: Path) -> None:
    path = tmp_path / "day-agent.sqlite3"
    task = day_task(task_id="task-20260821-NVDA")
    step = day_step(task, sequence=1, action=DayAgentAction.INSPECT_SITUATION)

    with DayAgentTaskStore(path).writer() as writer:
        assert writer.create_task(task) is True
        assert writer.append_step(step) is True
        assert writer.append_step(step) is False

    reopened = DayAgentTaskStore(path).reader()

    assert reopened.task(task.task_id) == task
    assert reopened.steps(task.task_id) == (step,)


def test_task_creation_is_idempotent_and_conflicting_payload_fails_closed(tmp_path: Path) -> None:
    task = day_task()
    conflict = day_task(
        task_id=task.task_id,
        budget=DayAgentBudget(remaining_model_calls=3, remaining_tool_calls=8, remaining_runtime_seconds=60),
    )

    with DayAgentTaskStore(tmp_path / "day-agent.sqlite3").writer() as writer:
        assert writer.create_task(task) is True
        assert writer.create_task(task) is False
        with pytest.raises(DayAgentTaskConflictError, match="task_replay_conflict"):
            writer.create_task(conflict)


def test_task_and_step_require_ordered_unique_evidence_references() -> None:
    with pytest.raises(ValidationError, match="sorted_unique_evidence_refs_required"):
        _ = day_task().model_validate(
            day_task().model_dump(mode="python") | {"evidence_refs": ("z", "a", "a")}
        )
    with pytest.raises(ValidationError, match="sorted_unique_evidence_refs_required"):
        _ = day_step(day_task(), sequence=1, action=DayAgentAction.READ_CATALYSTS).model_validate(
            day_step(day_task(), sequence=1, action=DayAgentAction.READ_CATALYSTS).model_dump(mode="python")
            | {"evidence_refs": ("z", "a", "a")}
        )


def test_steps_are_append_only_and_divergent_replay_conflicts(tmp_path: Path) -> None:
    task = day_task()
    first = day_step(task, sequence=1, action=DayAgentAction.INSPECT_SITUATION)
    divergent = day_step(task, sequence=1, action=DayAgentAction.READ_CATALYSTS)

    with DayAgentTaskStore(tmp_path / "day-agent.sqlite3").writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(first)
        with pytest.raises(DayAgentTaskConflictError, match="step_replay_conflict"):
            writer.append_step(divergent)
        with pytest.raises(DayAgentTaskConflictError, match="step_sequence_invalid"):
            writer.append_step(day_step(task, sequence=3, action=DayAgentAction.READ_CATALYSTS))


def test_only_one_open_step_can_exist_before_a_resulting_transition(tmp_path: Path) -> None:
    task = day_task()
    first = day_step(task, sequence=1, action=DayAgentAction.INSPECT_SITUATION)
    second_open = day_step(task, sequence=2, action=DayAgentAction.READ_CATALYSTS)
    waiting = day_step(
        task,
        sequence=2,
        action=DayAgentAction.DEFER,
        state=DayAgentTaskState.WAITING,
    )

    with DayAgentTaskStore(tmp_path / "day-agent.sqlite3").writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(first)
        with pytest.raises(DayAgentTaskConflictError, match="open_step_already_exists"):
            writer.append_step(second_open)
        assert writer.append_step(waiting)


def test_open_task_rejects_exhausted_model_or_runtime_budget() -> None:
    exhausted = DayAgentBudget(remaining_model_calls=0, remaining_tool_calls=0, remaining_runtime_seconds=0)

    with pytest.raises(ValidationError, match="active_task_budget_exhausted"):
        _ = day_task(budget=exhausted)


def test_waiting_task_requires_a_scheduled_wake_and_terminal_task_requires_reason() -> None:
    task = day_task()
    with pytest.raises(ValidationError, match="waiting_task_wake_required"):
        _ = task.model_validate(
            task.model_dump(mode="python")
            | {"state": DayAgentTaskState.WAITING, "scheduled_wake_at": None}
        )
    with pytest.raises(ValidationError, match="terminal_task_reason_required"):
        _ = task.model_validate(
            task.model_dump(mode="python")
            | {"state": DayAgentTaskState.COMPLETED, "terminal_reason": None}
        )


def test_terminal_task_cannot_accept_new_steps(tmp_path: Path) -> None:
    task = day_task(state=DayAgentTaskState.COMPLETED)
    step = day_step(task, sequence=1, action=DayAgentAction.INSPECT_SITUATION)

    with DayAgentTaskStore(tmp_path / "day-agent.sqlite3").writer() as writer:
        assert writer.create_task(task)
        with pytest.raises(DayAgentTaskConflictError, match="terminal_task_step_rejected"):
            writer.append_step(step)


def test_database_is_private_and_reader_rejects_symlink_and_hardlink(tmp_path: Path) -> None:
    path = tmp_path / "day-agent.sqlite3"
    with DayAgentTaskStore(path).writer() as writer:
        assert writer.create_task(day_task())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    symlink = tmp_path / "alias.sqlite3"
    symlink.symlink_to(path)
    with pytest.raises(InvalidDayAgentTaskStoreError, match="database_path_invalid"):
        _ = DayAgentTaskStore(symlink).reader().task("task-20260821-NVDA")

    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(path, hardlink)
    with pytest.raises(InvalidDayAgentTaskStoreError, match="database_path_invalid"):
        _ = DayAgentTaskStore(hardlink).reader().task("task-20260821-NVDA")


def test_reader_projects_latest_step_state_budget_and_evidence_after_reopen(tmp_path: Path) -> None:
    task = day_task()
    budget = DayAgentBudget(remaining_model_calls=3, remaining_tool_calls=7, remaining_runtime_seconds=45)
    step = day_step(
        task,
        sequence=1,
        action=DayAgentAction.DEFER,
        state=DayAgentTaskState.WAITING,
        budget=budget,
        evidence_refs=("evidence.catalyst.001", "evidence.market.001"),
    )
    with DayAgentTaskStore(tmp_path / "day-agent.sqlite3").writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(step)

    projected = DayAgentTaskStore(tmp_path / "day-agent.sqlite3").reader().task(task.task_id)

    assert projected is not None
    assert projected.state is DayAgentTaskState.WAITING
    assert projected.budget == budget
    assert projected.evidence_refs == step.evidence_refs
    assert projected.scheduled_wake_at == step.scheduled_wake_at
    assert projected.updated_at == NOW


def test_append_rejects_precreation_and_nonmonotonic_step_times_without_committing(tmp_path: Path) -> None:
    task = day_task()
    before_creation = day_step(
        task,
        sequence=1,
        action=DayAgentAction.INSPECT_SITUATION,
        occurred_at=NOW - dt.timedelta(seconds=1),
    )
    first = day_step(
        task,
        sequence=1,
        action=DayAgentAction.DEFER,
        state=DayAgentTaskState.WAITING,
        occurred_at=NOW + dt.timedelta(seconds=2),
    )
    before_prior = day_step(
        task,
        sequence=2,
        action=DayAgentAction.INSPECT_SITUATION,
        occurred_at=NOW + dt.timedelta(seconds=1),
    )
    path = tmp_path / "day-agent.sqlite3"

    with DayAgentTaskStore(path).writer() as writer:
        assert writer.create_task(task)
        with pytest.raises(DayAgentTaskConflictError, match="step_timestamp_invalid"):
            writer.append_step(before_creation)
        assert writer.append_step(first)
        with pytest.raises(DayAgentTaskConflictError, match="step_timestamp_invalid"):
            writer.append_step(before_prior)

    reopened = DayAgentTaskStore(path).reader()
    projected = reopened.task(task.task_id)
    assert projected is not None
    assert projected.state is DayAgentTaskState.WAITING
    assert reopened.steps(task.task_id) == (first,)


def test_step_research_updates_project_durably_and_exact_replay_is_idempotent(tmp_path: Path) -> None:
    task = day_task()
    step = DayAgentTaskStep(
        task_id=task.task_id,
        sequence=1,
        action=DayAgentAction.DEFER,
        reason="Wait for a verified catalyst publication before resuming.",
        evidence_refs=("evidence.catalyst.001",),
        budget=DayAgentBudget(remaining_model_calls=3, remaining_tool_calls=7, remaining_runtime_seconds=45),
        state=DayAgentTaskState.WAITING,
        occurred_at=NOW + dt.timedelta(seconds=1),
        scheduled_wake_at=NOW + dt.timedelta(minutes=5),
        current_hypothesis="A verified catalyst is required before leader comparison.",
        falsification_conditions=("catalyst_refuted",),
        open_questions=("Has the catalyst been verified?",),
        resume_condition="Resume after the catalyst is verified.",
    )
    path = tmp_path / "day-agent.sqlite3"

    with DayAgentTaskStore(path).writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(step)
        assert not writer.append_step(step)

    projected = DayAgentTaskStore(path).reader().task(task.task_id)
    assert projected is not None
    assert projected.current_hypothesis == step.current_hypothesis
    assert projected.falsification_conditions == step.falsification_conditions
    assert projected.open_questions == step.open_questions
    assert projected.resume_condition == step.resume_condition


def test_step_budget_cannot_increase_and_open_zero_tool_budget_is_invalid(tmp_path: Path) -> None:
    budget = DayAgentBudget(remaining_model_calls=1, remaining_tool_calls=1, remaining_runtime_seconds=1)
    task = day_task(budget=budget)
    increase = day_step(
        task,
        sequence=1,
        action=DayAgentAction.DEFER,
        state=DayAgentTaskState.WAITING,
        budget=DayAgentBudget(remaining_model_calls=12, remaining_tool_calls=24, remaining_runtime_seconds=300),
    )

    with DayAgentTaskStore(tmp_path / "day-agent.sqlite3").writer() as writer:
        assert writer.create_task(task)
        with pytest.raises(DayAgentTaskConflictError, match="step_budget_increase"):
            writer.append_step(increase)

    with pytest.raises(ValidationError, match="active_task_budget_exhausted"):
        _ = day_step(
            task,
            sequence=1,
            action=DayAgentAction.INSPECT_SITUATION,
            budget=DayAgentBudget(remaining_model_calls=1, remaining_tool_calls=0, remaining_runtime_seconds=1),
        )
    waiting = day_step(
        task,
        sequence=1,
        action=DayAgentAction.DEFER,
        state=DayAgentTaskState.WAITING,
        budget=DayAgentBudget(remaining_model_calls=0, remaining_tool_calls=0, remaining_runtime_seconds=0),
    )
    assert waiting.state is DayAgentTaskState.WAITING
