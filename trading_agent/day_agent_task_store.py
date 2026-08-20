from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never, final, override

from trading_agent.day_agent_task_models import (
    DayAgentResearchTask,
    DayAgentTaskState,
    DayAgentTaskStep,
    day_agent_step_payload,
)
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
    require_private_directory_query_only,
)
from trading_agent.systematic_regime_store_file import (
    InvalidSystematicRegimeFileError,
    load_sqlite_database,
    open_private_file,
    replace_sqlite_database,
    require_private_file,
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


@dataclass(slots=True)
class _DatabaseIdentity:
    parent: int
    name: str
    descriptor: int
    path: Path


@final
class DayAgentTaskStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        absolute = Path(os.path.abspath(path.expanduser()))
        self.path = absolute.parent.resolve(strict=False) / absolute.name

    @contextmanager
    def writer(self) -> Iterator[DayAgentTaskWriter]:
        parent = -1
        descriptor = -1
        try:
            parent = open_private_parent(self.path.parent, create=True)
            require_private_directory(parent)
            require_open_directory_path(self.path.parent, parent)
            with _writer_lease(self.path, parent):
                descriptor = _open_private_database(parent, self.path.name, create=True, write=True)
                identity = _DatabaseIdentity(parent=parent, name=self.path.name, descriptor=descriptor, path=self.path)
                descriptor = -1
                try:
                    with _writer_connection(identity) as connection:
                        writer = DayAgentTaskWriter(
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
            require_open_directory_path(self.path.parent, parent)
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            if isinstance(error, DayAgentTaskStoreError):
                raise
            raise InvalidDayAgentTaskStoreError(reason="database_write_failed") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if parent >= 0:
                os.close(parent)

    def reader(self) -> DayAgentTaskReader:
        return DayAgentTaskReader(self.path)


@final
class DayAgentTaskReader:
    __slots__ = ("_path",)

    _path: Path

    def __init__(self, path: Path) -> None:
        self._path = path

    def task(self, task_id: str) -> DayAgentResearchTask | None:
        try:
            with _reader_connection(self._path) as connection:
                row = connection.execute(
                    "SELECT task_id,payload_sha256,payload_json FROM day_tasks WHERE task_id=?", (task_id,)
                ).fetchone()
                if row is None:
                    return None
                task = _task_from_row(row)
                steps = _steps_for_task(connection, task.task_id)
        except FileNotFoundError:
            return None
        return _project_task(task, steps)

    def steps(self, task_id: str) -> tuple[DayAgentTaskStep, ...]:
        try:
            with _reader_connection(self._path) as connection:
                return _steps_for_task(connection, task_id)
        except FileNotFoundError:
            return ()


@final
class DayAgentTaskWriter:
    __slots__ = ("_active", "_connection", "_flush", "_reconcile")

    _active: bool
    _connection: sqlite3.Connection
    _flush: Callable[[], None]
    _reconcile: Callable[[], None]

    def __init__(
        self,
        connection: sqlite3.Connection,
        flush: Callable[[], None],
        reconcile: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._flush = flush
        self._reconcile = reconcile
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
            self._flush_mutation()
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
            _require_step_progress(task, steps, step)
            _require_appendable(task, steps, step)
            _ = self._connection.execute("INSERT INTO day_task_steps VALUES (?,?,?,?,?)", _step_row(step))
            self._connection.commit()
            self._flush_mutation()
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

    def _flush_mutation(self) -> None:
        try:
            self._flush()
        except (OSError, sqlite3.Error, TypeError, ValueError) as error:
            try:
                self._reconcile()
            except (OSError, sqlite3.Error, TypeError, ValueError) as reconciliation_error:
                self._active = False
                raise InvalidDayAgentTaskStoreError(
                    reason="writer_generation_reconcile_failed"
                ) from reconciliation_error
            raise InvalidDayAgentTaskStoreError(reason="writer_generation_flush_failed") from error


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


def _require_step_progress(
    task: DayAgentResearchTask,
    steps: tuple[DayAgentTaskStep, ...],
    step: DayAgentTaskStep,
) -> None:
    previous_time = task.updated_at if not steps else steps[-1].occurred_at
    previous_budget = task.budget if not steps else steps[-1].budget
    if step.occurred_at < previous_time:
        raise DayAgentTaskConflictError(reason="step_timestamp_invalid")
    if (
        step.budget.remaining_model_calls > previous_budget.remaining_model_calls
        or step.budget.remaining_tool_calls > previous_budget.remaining_tool_calls
        or step.budget.remaining_runtime_seconds > previous_budget.remaining_runtime_seconds
    ):
        raise DayAgentTaskConflictError(reason="step_budget_increase")


def _project_task(
    task: DayAgentResearchTask,
    steps: tuple[DayAgentTaskStep, ...],
) -> DayAgentResearchTask:
    if not steps:
        return task
    latest = steps[-1]
    updates = {
            "state": latest.state,
            "evidence_refs": latest.evidence_refs,
            "budget": latest.budget,
            "updated_at": latest.occurred_at,
            "scheduled_wake_at": latest.scheduled_wake_at,
            "terminal_reason": latest.terminal_reason,
    }
    for prior in steps:
        for field in ("current_hypothesis", "falsification_conditions", "open_questions", "resume_condition"):
            value = getattr(prior, field)
            if value is not None:
                updates[field] = value
    return DayAgentResearchTask.model_validate(task.model_dump(mode="python") | updates)


@contextmanager
def _reader_connection(path: Path) -> Iterator[sqlite3.Connection]:
    parent = -1
    descriptor = -1
    try:
        parent = open_private_parent(path.parent, create=False)
        require_private_directory_query_only(parent)
        require_open_directory_path(path.parent, parent)
        descriptor = _open_private_database(parent, path.name, create=False, write=False)
        identity = _DatabaseIdentity(parent=parent, name=path.name, descriptor=descriptor, path=path)
        with closing(_connect_descriptor(descriptor)) as connection:
            _require_database_identity(identity)
            _enable_foreign_keys(connection)
            _ = connection.execute("PRAGMA query_only = ON")
            _require_schema(connection)
            yield connection
            _require_reader_snapshot(identity)
        _require_reader_snapshot(identity)
        require_open_directory_path(path.parent, parent)
    except FileNotFoundError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        if isinstance(error, DayAgentTaskStoreError):
            raise
        raise InvalidDayAgentTaskStoreError(reason="database_read_failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


def _open_private_database(parent: int, name: str, *, create: bool, write: bool) -> int:
    try:
        return open_private_file(parent, name, create=create, write=write)
    except FileNotFoundError:
        raise
    except (InvalidSystematicRegimeFileError, OSError, ValueError) as error:
        raise InvalidDayAgentTaskStoreError(reason="database_path_invalid") from error


@contextmanager
def _writer_lease(path: Path, parent: int) -> Iterator[None]:
    descriptor = _open_private_database(parent, f"{path.name}.writer.lock", create=True, write=True)
    parent_locked = False
    locked = False
    try:
        require_open_directory_path(path.parent, parent)
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent_locked = True
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if parent_locked:
            fcntl.flock(parent, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _writer_connection(identity: _DatabaseIdentity) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(":memory:")) as connection:
        _require_database_identity(identity)
        original = load_sqlite_database(connection, identity.descriptor)
        _enable_foreign_keys(connection)
        _prepare_writer_connection(connection)
        if connection.serialize() != original:
            _flush_writer_generation(identity, connection)
        yield connection
        _require_database_identity(identity)


def _flush_writer_generation(identity: _DatabaseIdentity, connection: sqlite3.Connection) -> None:
    _require_database_identity(identity)
    payload = connection.serialize()
    replace_sqlite_database(identity.parent, identity.name, payload)
    replacement = _open_private_database(identity.parent, identity.name, create=False, write=True)
    original = identity.descriptor
    try:
        _require_generation_payload(replacement, payload)
        identity.descriptor = replacement
        _require_database_identity(identity)
    except BaseException:
        identity.descriptor = original
        os.close(replacement)
        raise
    os.close(original)


def _require_generation_payload(descriptor: int, payload: bytes) -> None:
    if os.fstat(descriptor).st_size != len(payload) or os.pread(descriptor, len(payload), 0) != payload:
        raise OSError


def _reconcile_writer_generation(identity: _DatabaseIdentity, connection: sqlite3.Connection) -> None:
    replacement = _open_private_database(identity.parent, identity.name, create=False, write=True)
    original = identity.descriptor
    try:
        identity.descriptor = replacement
        _require_database_identity(identity)
        _ = load_sqlite_database(connection, replacement)
        _enable_foreign_keys(connection)
        _require_schema(connection)
    except BaseException:
        identity.descriptor = original
        os.close(replacement)
        raise
    os.close(original)


def _connect_descriptor(descriptor: int) -> sqlite3.Connection:
    return sqlite3.connect(f"file:/dev/fd/{descriptor}?mode=ro", uri=True, timeout=0.0)


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise sqlite3.DatabaseError


def _require_database_identity(identity: _DatabaseIdentity) -> None:
    require_open_directory_path(identity.path.parent, identity.parent)
    named = os.stat(identity.name, dir_fd=identity.parent, follow_symlinks=False)
    opened = os.fstat(identity.descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise OSError
    require_private_file(identity.descriptor)


def _require_reader_snapshot(identity: _DatabaseIdentity) -> None:
    require_open_directory_path(identity.path.parent, identity.parent)
    metadata = os.fstat(identity.descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink not in {0, 1}
    ):
        raise OSError


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
    if isinstance(item, DayAgentTaskStep):
        return day_agent_step_payload(item)
    return json.dumps(
        item.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


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
