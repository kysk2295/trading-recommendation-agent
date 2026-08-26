from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from trading_agent._autonomous_task_store_projection import (
    AutonomousTaskReader,
    _require_appendable,
    _step_row,
    _steps_for_task,
    _task_from_row,
    _task_row,
)
from trading_agent._autonomous_task_store_sqlite import (
    AutonomousTaskConflictError,
    AutonomousTaskStoreError,
    InvalidAutonomousTaskStoreError,
    _DatabaseIdentity,
    _flush_writer_generation,
    _open_private_database,
    _open_private_parent,
    _reconcile_writer_generation,
    _require_open_directory_path,
    _require_private_directory,
    _writer_connection,
    _writer_lease,
)
from trading_agent.autonomous_task_models import (
    AutonomousResearchTask,
    AutonomousTaskStep,
    validate_autonomous_step_projection,
)


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

    def create_task_with_initial_step(
        self,
        task: AutonomousResearchTask,
        step: AutonomousTaskStep,
    ) -> bool:
        self._require_active()
        validate_autonomous_step_projection(task, step)
        _require_appendable(task, (), step)
        task_row = _task_row(task)
        step_row = _step_row(step)
        self._begin()
        try:
            existing_task = self._connection.execute(
                "SELECT task_id,root_source_evidence_id,payload_sha256,payload_json "
                "FROM autonomous_tasks WHERE task_id=?",
                (task.task_id,),
            ).fetchone()
            existing_step = self._connection.execute(
                "SELECT step_id,task_id,sequence,occurred_at,payload_sha256,payload_json "
                "FROM autonomous_task_steps WHERE step_id=? OR (task_id=? AND sequence=?)",
                (step.step_id, step.task_id, step.sequence),
            ).fetchone()
            if existing_task is not None or existing_step is not None:
                if (
                    existing_task is not None
                    and existing_step is not None
                    and tuple(existing_task) == task_row
                    and tuple(existing_step) == step_row
                ):
                    self._connection.rollback()
                    return False
                raise AutonomousTaskConflictError(reason="initial_admission_replay_conflict")
            _ = self._connection.execute("INSERT INTO autonomous_tasks VALUES (?,?,?,?)", task_row)
            _ = self._connection.execute("INSERT INTO autonomous_task_steps VALUES (?,?,?,?,?,?)", step_row)
            self._connection.commit()
            self._flush_mutation()
            return True
        except AutonomousTaskStoreError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise InvalidAutonomousTaskStoreError(reason="initial_admission_insert_failed") from error

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


__all__ = (
    "AutonomousTaskConflictError",
    "AutonomousTaskReader",
    "AutonomousTaskStore",
    "AutonomousTaskStoreError",
    "AutonomousTaskWriter",
    "InvalidAutonomousTaskStoreError",
)
