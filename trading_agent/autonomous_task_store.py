from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, assert_never

from trading_agent._autonomous_task_store_sqlite import (
    AutonomousTaskConflictError,
    AutonomousTaskStoreError,
    InvalidAutonomousTaskStoreError,
    _DatabaseIdentity,
    _flush_writer_generation,
    _open_private_database,
    _open_private_parent,
    _reader_connection,
    _reconcile_writer_generation,
    _require_open_directory_path,
    _require_private_directory,
    _writer_connection,
    _writer_lease,
)
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousTaskState,
    AutonomousTaskStep,
    autonomous_step_payload,
    validate_autonomous_step_projection,
)

_TERMINAL_STATES: Final = frozenset({AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED})


class AutonomousTaskStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        absolute = Path(os.path.abspath(path.expanduser()))
        self.path = absolute.parent.resolve(strict=False) / absolute.name

    @contextmanager
    def writer(self) -> Iterator[AutonomousTaskWriter]:
        parent = -1
        descriptor = -1
        try:
            parent = _open_private_parent(self.path.parent, create=True)
            _require_private_directory(parent)
            _require_open_directory_path(self.path.parent, parent)
            with _writer_lease(self.path, parent):
                descriptor = _open_private_database(parent, self.path.name, create=True, write=True)
                identity = _DatabaseIdentity(parent, self.path.name, descriptor, self.path)
                descriptor = -1
                try:
                    with _writer_connection(identity) as connection:
                        writer = AutonomousTaskWriter(
                            connection,
                            lambda: _flush_writer_generation(identity, connection),
                            lambda: _reconcile_writer_generation(identity, connection),
                        )
                        try:
                            yield writer
                        finally:
                            writer.close()
                finally:
                    os.close(identity.descriptor)
            _require_open_directory_path(self.path.parent, parent)
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            if isinstance(error, AutonomousTaskStoreError):
                raise
            raise InvalidAutonomousTaskStoreError(reason="database_write_failed") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent >= 0:
                os.close(parent)

    def reader(self) -> AutonomousTaskReader:
        return AutonomousTaskReader(self.path)


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


class AutonomousTaskWriter:
    __slots__ = ("_active", "_connection", "_flush", "_reconcile")

    _active: bool
    _connection: sqlite3.Connection
    _flush: Callable[[], None]
    _reconcile: Callable[[], None]

    def __init__(
        self, connection: sqlite3.Connection, flush: Callable[[], None], reconcile: Callable[[], None]
    ) -> None:
        self._connection = connection
        self._flush = flush
        self._reconcile = reconcile
        self._active = True

    def create_task(self, task: AutonomousResearchTask) -> bool:
        self._require_active()
        row = _task_row(task)
        self._begin()
        try:
            existing = self._connection.execute(
                "SELECT task_id,root_source_evidence_id,payload_sha256,payload_json "
                "FROM autonomous_tasks WHERE task_id=?",
                (task.task_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == row:
                    self._connection.rollback()
                    return False
                raise AutonomousTaskConflictError(reason="task_replay_conflict")
            _ = self._connection.execute("INSERT INTO autonomous_tasks VALUES (?,?,?,?)", row)
            self._connection.commit()
            self._flush_mutation()
            return True
        except AutonomousTaskStoreError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise InvalidAutonomousTaskStoreError(reason="task_insert_failed") from error

    def append_step(self, step: AutonomousTaskStep) -> bool:
        self._require_active()
        self._begin()
        try:
            task_row = self._connection.execute(
                "SELECT task_id,root_source_evidence_id,payload_sha256,payload_json "
                "FROM autonomous_tasks WHERE task_id=?",
                (step.task_id,),
            ).fetchone()
            if task_row is None:
                raise AutonomousTaskConflictError(reason="task_missing")
            task = _task_from_row(task_row)
            existing = self._connection.execute(
                "SELECT step_id,task_id,sequence,occurred_at,payload_sha256,payload_json FROM autonomous_task_steps "
                "WHERE step_id=? OR (task_id=? AND sequence=?)",
                (step.step_id, step.task_id, step.sequence),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == _step_row(step):
                    self._connection.rollback()
                    return False
                raise AutonomousTaskConflictError(reason="step_replay_conflict")
            steps = _steps_for_task(self._connection, task.task_id)
            _require_appendable(task, steps, step)
            validate_autonomous_step_projection(task, step)
            _ = self._connection.execute("INSERT INTO autonomous_task_steps VALUES (?,?,?,?,?,?)", _step_row(step))
            self._connection.commit()
            self._flush_mutation()
            return True
        except AutonomousTaskStoreError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise InvalidAutonomousTaskStoreError(reason="step_insert_failed") from error

    def close(self) -> None:
        self._active = False

    def _begin(self) -> None:
        _ = self._connection.execute("BEGIN IMMEDIATE")

    def _require_active(self) -> None:
        if not self._active:
            raise InvalidAutonomousTaskStoreError(reason="writer_inactive")

    def _flush_mutation(self) -> None:
        try:
            self._flush()
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            try:
                self._reconcile()
            except (OSError, sqlite3.Error, TypeError, ValueError) as reconciliation_error:
                self._active = False
                raise InvalidAutonomousTaskStoreError(
                    reason="writer_generation_reconcile_failed"
                ) from reconciliation_error
            raise InvalidAutonomousTaskStoreError(reason="writer_generation_flush_failed") from error


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


__all__ = (
    "AutonomousTaskConflictError",
    "AutonomousTaskReader",
    "AutonomousTaskStore",
    "AutonomousTaskStoreError",
    "AutonomousTaskWriter",
    "InvalidAutonomousTaskStoreError",
)
