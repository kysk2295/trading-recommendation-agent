from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Final, assert_never, final, override

from trading_agent.day_agent_task_models import (
    DayAgentResearchTask,
    DayAgentTaskState,
    DayAgentTaskStep,
)

# SIZE_OK — one SQLite authority keeps task, step, and projection invariants transactional.
_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE day_tasks (
  task_id TEXT PRIMARY KEY,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE day_task_steps (
  step_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES day_tasks(task_id),
  sequence INTEGER NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(task_id, sequence)
);
CREATE TRIGGER day_tasks_no_update BEFORE UPDATE ON day_tasks BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER day_tasks_no_delete BEFORE DELETE ON day_tasks BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER day_task_steps_no_update BEFORE UPDATE ON day_task_steps BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER day_task_steps_no_delete BEFORE DELETE ON day_task_steps BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_SCHEMA_OBJECTS: Final = frozenset(
    {
        "day_tasks",
        "day_task_steps",
        "day_tasks_no_update",
        "day_tasks_no_delete",
        "day_task_steps_no_update",
        "day_task_steps_no_delete",
    }
)


class DayAgentTaskStoreError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @override
    def __str__(self) -> str:
        return self.reason


class DayAgentTaskConflictError(DayAgentTaskStoreError):
    pass


class InvalidDayAgentTaskStoreError(DayAgentTaskStoreError):
    pass


@final
class DayAgentTaskStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = Path(os.path.abspath(path.expanduser()))

    @contextmanager
    def writer(self) -> Iterator[DayAgentTaskWriter]:
        _require_writable_database_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with closing(sqlite3.connect(self.path, timeout=10.0)) as connection:
                os.chmod(self.path, 0o600)
                _prepare_writer_connection(connection)
                writer = DayAgentTaskWriter(connection)
                try:
                    yield writer
                finally:
                    writer.close()
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            if isinstance(error, DayAgentTaskStoreError):
                raise
            raise InvalidDayAgentTaskStoreError(reason="database_write_failed") from error

    def reader(self) -> DayAgentTaskReader:
        return DayAgentTaskReader(self.path)


@final
class DayAgentTaskReader:
    __slots__ = ("_path",)

    _path: Path

    def __init__(self, path: Path) -> None:
        self._path = path

    def task(self, task_id: str) -> DayAgentResearchTask | None:
        if not _database_exists(self._path):
            return None
        with _reader_connection(self._path) as connection:
            row = connection.execute(
                "SELECT task_id,payload_sha256,payload_json FROM day_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            task = _task_from_row(row)
            steps = _steps_for_task(connection, task.task_id)
        return _project_task(task, steps)

    def steps(self, task_id: str) -> tuple[DayAgentTaskStep, ...]:
        if not _database_exists(self._path):
            return ()
        with _reader_connection(self._path) as connection:
            return _steps_for_task(connection, task_id)


@final
class DayAgentTaskWriter:
    __slots__ = ("_active", "_connection")

    _active: bool
    _connection: sqlite3.Connection

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._active = True

    def create_task(self, task: DayAgentResearchTask) -> bool:
        self._require_active()
        row = _task_row(task)
        self._begin()
        try:
            existing = self._connection.execute(
                "SELECT task_id,payload_sha256,payload_json FROM day_tasks WHERE task_id=?", (task.task_id,)
            ).fetchone()
            if existing is not None:
                if tuple(existing) == row:
                    self._connection.rollback()
                    return False
                raise DayAgentTaskConflictError(reason="task_replay_conflict")
            _ = self._connection.execute("INSERT INTO day_tasks VALUES (?,?,?)", row)
            self._connection.commit()
            return True
        except DayAgentTaskStoreError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise InvalidDayAgentTaskStoreError(reason="task_insert_failed") from error

    def append_step(self, step: DayAgentTaskStep) -> bool:
        self._require_active()
        self._begin()
        try:
            task_row = self._connection.execute(
                "SELECT task_id,payload_sha256,payload_json FROM day_tasks WHERE task_id=?", (step.task_id,)
            ).fetchone()
            if task_row is None:
                raise DayAgentTaskConflictError(reason="task_missing")
            task = _task_from_row(task_row)
            existing = self._connection.execute(
                "SELECT step_id,task_id,sequence,payload_sha256,payload_json FROM day_task_steps "
                "WHERE task_id=? AND sequence=?",
                (step.task_id, step.sequence),
            ).fetchone()
            if existing is not None:
                if tuple(existing) == _step_row(step):
                    self._connection.rollback()
                    return False
                raise DayAgentTaskConflictError(reason="step_replay_conflict")
            steps = _steps_for_task(self._connection, task.task_id)
            _require_appendable(task, steps, step)
            _ = self._connection.execute("INSERT INTO day_task_steps VALUES (?,?,?,?,?)", _step_row(step))
            self._connection.commit()
            return True
        except DayAgentTaskStoreError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise InvalidDayAgentTaskStoreError(reason="step_insert_failed") from error

    def close(self) -> None:
        self._active = False

    def _begin(self) -> None:
        _ = self._connection.execute("BEGIN IMMEDIATE")

    def _require_active(self) -> None:
        if not self._active:
            raise InvalidDayAgentTaskStoreError(reason="writer_inactive")


def _require_appendable(
    task: DayAgentResearchTask,
    steps: tuple[DayAgentTaskStep, ...],
    step: DayAgentTaskStep,
) -> None:
    if task.state in {DayAgentTaskState.COMPLETED, DayAgentTaskState.BLOCKED}:
        raise DayAgentTaskConflictError(reason="terminal_task_step_rejected")
    expected_sequence = len(steps) + 1
    if step.sequence != expected_sequence:
        raise DayAgentTaskConflictError(reason="step_sequence_invalid")
    if not steps:
        return
    previous = steps[-1]
    match previous.state:
        case DayAgentTaskState.COMPLETED | DayAgentTaskState.BLOCKED:
            raise DayAgentTaskConflictError(reason="terminal_task_step_rejected")
        case DayAgentTaskState.OPEN:
            if step.state is DayAgentTaskState.OPEN:
                raise DayAgentTaskConflictError(reason="open_step_already_exists")
        case DayAgentTaskState.WAITING:
            return
        case unreachable:
            assert_never(unreachable)


def _project_task(
    task: DayAgentResearchTask,
    steps: tuple[DayAgentTaskStep, ...],
) -> DayAgentResearchTask:
    if not steps:
        return task
    latest = steps[-1]
    return DayAgentResearchTask.model_validate(
        task.model_dump(mode="python")
        | {
            "state": latest.state,
            "evidence_refs": latest.evidence_refs,
            "budget": latest.budget,
            "updated_at": latest.occurred_at,
            "scheduled_wake_at": latest.scheduled_wake_at,
            "terminal_reason": latest.terminal_reason,
        }
    )


@contextmanager
def _reader_connection(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        _require_private_database_file(path)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
            _ = connection.execute("PRAGMA query_only = ON")
            _require_schema(connection)
            yield connection
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        if isinstance(error, DayAgentTaskStoreError):
            raise
        raise InvalidDayAgentTaskStoreError(reason="database_read_failed") from error


def _database_exists(path: Path) -> bool:
    if path.is_symlink():
        raise InvalidDayAgentTaskStoreError(reason="database_path_invalid")
    return path.exists()


def _require_writable_database_path(path: Path) -> None:
    if path.is_symlink():
        raise InvalidDayAgentTaskStoreError(reason="database_path_invalid")
    if path.exists():
        _require_private_database_file(path)


def _require_private_database_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise InvalidDayAgentTaskStoreError(reason="database_path_invalid") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidDayAgentTaskStoreError(reason="database_path_invalid")


def _prepare_writer_connection(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        objects = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        )
        if objects:
            raise InvalidDayAgentTaskStoreError(reason="schema_version_invalid")
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidDayAgentTaskStoreError(reason="schema_version_invalid")
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if objects != _SCHEMA_OBJECTS:
        raise InvalidDayAgentTaskStoreError(reason="schema_objects_invalid")


def _task_row(task: DayAgentResearchTask) -> tuple[str, str, str]:
    payload = _payload(task)
    return (task.task_id, hashlib.sha256(payload.encode()).hexdigest(), payload)


def _step_row(step: DayAgentTaskStep) -> tuple[str, str, int, str, str]:
    payload = _payload(step)
    return (step.step_id, step.task_id, step.sequence, hashlib.sha256(payload.encode()).hexdigest(), payload)


def _payload(item: DayAgentResearchTask | DayAgentTaskStep) -> str:
    return json.dumps(item.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _task_from_row(row: tuple[str, str, str]) -> DayAgentResearchTask:
    task_id, payload_sha256, payload = row
    try:
        task = DayAgentResearchTask.model_validate_json(payload)
    except ValueError as error:
        raise InvalidDayAgentTaskStoreError(reason="task_payload_invalid") from error
    if _task_row(task) != (task_id, payload_sha256, payload):
        raise InvalidDayAgentTaskStoreError(reason="task_payload_invalid")
    return task


def _steps_for_task(connection: sqlite3.Connection, task_id: str) -> tuple[DayAgentTaskStep, ...]:
    rows = connection.execute(
        "SELECT step_id,task_id,sequence,payload_sha256,payload_json FROM day_task_steps "
        "WHERE task_id=? ORDER BY sequence",
        (task_id,),
    ).fetchall()
    return tuple(_step_from_row(row) for row in rows)


def _step_from_row(row: tuple[str, str, int, str, str]) -> DayAgentTaskStep:
    step_id, task_id, sequence, payload_sha256, payload = row
    try:
        step = DayAgentTaskStep.model_validate_json(payload)
    except ValueError as error:
        raise InvalidDayAgentTaskStoreError(reason="step_payload_invalid") from error
    if _step_row(step) != (step_id, task_id, sequence, payload_sha256, payload):
        raise InvalidDayAgentTaskStoreError(reason="step_payload_invalid")
    return step


__all__ = (
    "DayAgentTaskConflictError",
    "DayAgentTaskReader",
    "DayAgentTaskStore",
    "DayAgentTaskStoreError",
    "DayAgentTaskWriter",
    "InvalidDayAgentTaskStoreError",
)
