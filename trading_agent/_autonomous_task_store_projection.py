from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Collection
from pathlib import Path
from typing import Final, assert_never

from trading_agent._autonomous_task_store_sqlite import (
    AutonomousTaskConflictError,
    InvalidAutonomousTaskStoreError,
    _reader_connection,
)
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousTaskState,
    AutonomousTaskStep,
    autonomous_step_payload,
    validate_autonomous_step_projection,
)

_TERMINAL_STATES: Final = frozenset({AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED})


class AutonomousTaskReader:
    __slots__ = ("_path",)

    _path: Path

    def __init__(self, path: Path) -> None:
        self._path = path

    def task(self, task_id: str) -> AutonomousResearchTask | None:
        try:
            with _reader_connection(self._path) as connection:
                row = connection.execute(
                    "SELECT task_id,root_source_evidence_id,payload_sha256,payload_json "
                    "FROM autonomous_tasks WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    return None
                task = _task_from_row(row)
                return _project_task(task, _steps_for_task(connection, task.task_id))
        except FileNotFoundError:
            return None

    def steps(self, task_id: str) -> tuple[AutonomousTaskStep, ...]:
        try:
            with _reader_connection(self._path) as connection:
                return _steps_for_task(connection, task_id)
        except FileNotFoundError:
            return ()

    def tasks(self) -> tuple[AutonomousResearchTask, ...]:
        try:
            with _reader_connection(self._path) as connection:
                rows = connection.execute(
                    "SELECT task_id,root_source_evidence_id,payload_sha256,payload_json "
                    "FROM autonomous_tasks ORDER BY task_id"
                ).fetchall()
                return tuple(_project_task(_task_from_row(row), _steps_for_task(connection, row[0])) for row in rows)
        except FileNotFoundError:
            return ()

    def matching_open_tasks(
        self, family: str, market: str, subject_refs: Collection[str]
    ) -> tuple[AutonomousResearchTask, ...]:
        if not subject_refs:
            return ()
        matches = tuple(
            task
            for task in self.tasks()
            if task.agent_family_id == family
            and task.market_scope == market
            and task.state not in _TERMINAL_STATES
            and bool(set(task.subject_refs).intersection(subject_refs))
        )
        return tuple(sorted(matches, key=lambda task: (-task.updated_at.timestamp(), task.task_id)))

    def runnable(self, now: dt.datetime, *, events: Collection[str]) -> tuple[AutonomousResearchTask, ...]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise InvalidAutonomousTaskStoreError(reason="naive_time_input")
        normalized = now.astimezone(dt.UTC)
        ready = tuple(task for task in self.tasks() if _is_runnable(task, normalized, events))
        return tuple(sorted(ready, key=lambda task: (-task.priority, _wake_or_created(task), task.task_id)))


def _require_appendable(
    task: AutonomousResearchTask, steps: tuple[AutonomousTaskStep, ...], step: AutonomousTaskStep
) -> None:
    projected = _project_task(task, steps)
    if projected.state in _TERMINAL_STATES:
        raise AutonomousTaskConflictError(reason="terminal_task_step_rejected")
    if step.sequence != len(steps) + 1:
        raise AutonomousTaskConflictError(reason="step_sequence_invalid")
    previous = task.updated_at if not steps else steps[-1].occurred_at
    if step.occurred_at < previous:
        raise AutonomousTaskConflictError(reason="step_timestamp_invalid")


def _project_task(task: AutonomousResearchTask, steps: tuple[AutonomousTaskStep, ...]) -> AutonomousResearchTask:
    projected = task
    for step in steps:
        validate_autonomous_step_projection(projected, step)
        projected = AutonomousResearchTask.model_validate(
            projected.model_dump(mode="python")
            | {
                "state": step.state,
                "owner_role": step.role,
                "source_evidence_ids": tuple(
                    sorted(set(projected.source_evidence_ids) | set(step.source_evidence_ids))
                ),
                "evidence_refs": step.evidence_refs,
                "working_memory_ids": step.working_memory_ids,
                "updated_at": step.occurred_at,
                "next_wake_at": step.next_wake_at,
                "next_wake_event": step.next_wake_event,
                "blocked_reason": step.blocked_reason,
                "terminal_reason": step.terminal_reason,
            }
        )
    return projected


def _is_runnable(task: AutonomousResearchTask, now: dt.datetime, events: Collection[str]) -> bool:
    match task.state:
        case AutonomousTaskState.COMPLETED | AutonomousTaskState.ABANDONED:
            return False
        case AutonomousTaskState.WAITING_EVENT:
            return task.next_wake_event in events
        case AutonomousTaskState.WAITING_TIME:
            return task.next_wake_at is not None and task.next_wake_at <= now
        case AutonomousTaskState.BLOCKED:
            if task.next_wake_event is not None:
                return task.next_wake_event in events
            return task.next_wake_at is not None and task.next_wake_at <= now
        case (
            AutonomousTaskState.QUEUED
            | AutonomousTaskState.OBSERVING
            | AutonomousTaskState.RESEARCHING
            | AutonomousTaskState.DELIBERATING
            | AutonomousTaskState.ACTING
            | AutonomousTaskState.EVALUATING
            | AutonomousTaskState.LEARNING
        ):
            return True
        case unreachable:
            assert_never(unreachable)


def _wake_or_created(task: AutonomousResearchTask) -> dt.datetime:
    return task.next_wake_at or task.created_at


def _task_row(task: AutonomousResearchTask) -> tuple[str, str, str, str]:
    payload = _payload(task)
    return (task.task_id, task.root_source_evidence_id, hashlib.sha256(payload.encode()).hexdigest(), payload)


def _step_row(step: AutonomousTaskStep) -> tuple[str, str, int, str, str, str]:
    payload = _payload(step)
    return (
        step.step_id,
        step.task_id,
        step.sequence,
        step.occurred_at.isoformat(),
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _payload(item: AutonomousResearchTask | AutonomousTaskStep) -> str:
    if isinstance(item, AutonomousTaskStep):
        return autonomous_step_payload(item)
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _task_from_row(row: tuple[str, str, str, str]) -> AutonomousResearchTask:
    task_id, root_source_evidence_id, payload_sha256, payload = row
    try:
        task = AutonomousResearchTask.model_validate_json(payload)
    except ValueError as error:
        raise InvalidAutonomousTaskStoreError(reason="task_payload_invalid") from error
    if _task_row(task) != (task_id, root_source_evidence_id, payload_sha256, payload):
        raise InvalidAutonomousTaskStoreError(reason="task_payload_invalid")
    return task


def _steps_for_task(connection: sqlite3.Connection, task_id: str) -> tuple[AutonomousTaskStep, ...]:
    rows = connection.execute(
        "SELECT step_id,task_id,sequence,occurred_at,payload_sha256,payload_json FROM autonomous_task_steps "
        "WHERE task_id=? ORDER BY sequence",
        (task_id,),
    ).fetchall()
    return tuple(_step_from_row(row) for row in rows)


def _step_from_row(row: tuple[str, str, int, str, str, str]) -> AutonomousTaskStep:
    step_id, task_id, sequence, occurred_at, payload_sha256, payload = row
    try:
        step = AutonomousTaskStep.model_validate_json(payload)
    except ValueError as error:
        raise InvalidAutonomousTaskStoreError(reason="step_payload_invalid") from error
    if _step_row(step) != (step_id, task_id, sequence, occurred_at, payload_sha256, payload):
        raise InvalidAutonomousTaskStoreError(reason="step_payload_invalid")
    return step
