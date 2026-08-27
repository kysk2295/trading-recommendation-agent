from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import final, override

from pydantic import ValidationError

from trading_agent.kr_autonomous_pending_plan_models import KrAutonomousPendingPlan
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri

_CREATE = (
    "CREATE TABLE kr_autonomous_pending_plans (plan_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
    "payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL)"
)
_INDEX = "CREATE INDEX kr_autonomous_pending_plans_task ON kr_autonomous_pending_plans(task_id,plan_id)"
_UPDATE = (
    "CREATE TRIGGER kr_autonomous_pending_plans_no_update BEFORE UPDATE ON "
    "kr_autonomous_pending_plans BEGIN SELECT RAISE(ABORT, 'append-only'); END"
)
_DELETE = (
    "CREATE TRIGGER kr_autonomous_pending_plans_no_delete BEFORE DELETE ON "
    "kr_autonomous_pending_plans BEGIN SELECT RAISE(ABORT, 'append-only'); END"
)
_STATEMENTS = (_CREATE, _INDEX, _UPDATE, _DELETE)
_EXPECTED = {
    ("table", "kr_autonomous_pending_plans"): " ".join(_CREATE.split()),
    ("index", "kr_autonomous_pending_plans_task"): " ".join(_INDEX.split()),
    ("trigger", "kr_autonomous_pending_plans_no_update"): " ".join(_UPDATE.split()),
    ("trigger", "kr_autonomous_pending_plans_no_delete"): " ".join(_DELETE.split()),
}
type _Row = tuple[str, str, str, str]


class InvalidKrAutonomousPendingPlanStoreError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR autonomous pending-plan store is invalid"


@final
class KrAutonomousPendingPlanStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def append(self, plan: KrAutonomousPendingPlan) -> bool:
        try:
            trusted = KrAutonomousPendingPlan.model_validate_json(plan.model_dump_json())
            payload = _canonical(trusted)
            with _open(self.path, write=True) as connection:
                row = _row(trusted, payload)
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT plan_id,task_id,payload_sha256,payload_json FROM "
                    "kr_autonomous_pending_plans WHERE plan_id=?",
                    (trusted.plan_id,),
                ).fetchone()
                if existing is not None:
                    if existing != row or _decode(existing) != trusted:
                        raise InvalidKrAutonomousPendingPlanStoreError
                    connection.commit()
                    return False
                connection.execute("INSERT INTO kr_autonomous_pending_plans VALUES (?,?,?,?)", row)
                connection.commit()
                return True
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrAutonomousPendingPlanStoreError from None

    def plan(self, plan_id: str) -> KrAutonomousPendingPlan | None:
        if len(plan_id) != 64 or any(value not in "0123456789abcdef" for value in plan_id):
            raise InvalidKrAutonomousPendingPlanStoreError
        if self.path.is_symlink():
            raise InvalidKrAutonomousPendingPlanStoreError
        if not self.path.exists():
            return None
        try:
            with _open(self.path, write=False) as connection:
                row = connection.execute(
                    "SELECT plan_id,task_id,payload_sha256,payload_json FROM "
                    "kr_autonomous_pending_plans WHERE plan_id=?",
                    (plan_id,),
                ).fetchone()
                return None if row is None else _decode(row)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrAutonomousPendingPlanStoreError from None


def _canonical(plan: KrAutonomousPendingPlan) -> str:
    return plan.model_dump_json(exclude_none=False)


def _row(plan: KrAutonomousPendingPlan, payload: str) -> _Row:
    return (plan.plan_id, plan.request.thesis.task_id, hashlib.sha256(payload.encode("utf-8")).hexdigest(), payload)


def _decode(row: _Row) -> KrAutonomousPendingPlan:
    plan = KrAutonomousPendingPlan.model_validate_json(row[3])
    if row != _row(plan, _canonical(plan)):
        raise InvalidKrAutonomousPendingPlanStoreError
    return plan


@contextmanager
def _open(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    parent = descriptor = None
    connection = None
    try:
        parent = open_private_parent(path.parent, create=write)
        require_private_directory_query_only(parent)
        flags = os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_RDWR if write else os.O_RDONLY)
        try:
            descriptor = os.open(path.name, flags | (os.O_CREAT | os.O_EXCL if write else 0), 0o600, dir_fd=parent)
        except FileExistsError:
            descriptor = os.open(path.name, flags, dir_fd=parent)
        if not _private(os.fstat(descriptor)):
            raise InvalidKrAutonomousPendingPlanStoreError
        _require_identity(parent, path.name, descriptor)
        connection = sqlite3.connect(path if write else sqlite_read_only_uri(path), uri=not write, timeout=5.0)
        _require_identity(parent, path.name, descriptor)
        if write:
            _prepare(connection)
        else:
            connection.execute("PRAGMA query_only=ON")
            _schema(connection)
        yield connection
    finally:
        try:
            if parent is not None and descriptor is not None:
                _require_identity(parent, path.name, descriptor)
        finally:
            if connection is not None:
                connection.close()
            if descriptor is not None:
                os.close(descriptor)
            if parent is not None:
                os.close(parent)


def _private(value: os.stat_result) -> bool:
    return (
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.getuid()
        and value.st_nlink == 1
        and stat.S_IMODE(value.st_mode) == 0o600
    )


def _require_identity(parent: int, name: str, descriptor: int) -> None:
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if not _private(named) or not _private(opened) or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise InvalidKrAutonomousPendingPlanStoreError


def _prepare(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        if connection.execute("PRAGMA user_version").fetchone() == (0,):
            for statement in _STATEMENTS:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=1")
        _schema(connection)
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise


def _schema(connection: sqlite3.Connection) -> None:
    actual = {
        (str(row[0]), str(row[1])): " ".join(str(row[2]).split())
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    if connection.execute("PRAGMA user_version").fetchone() != (1,) or actual != _EXPECTED:
        raise InvalidKrAutonomousPendingPlanStoreError


__all__ = ("InvalidKrAutonomousPendingPlanStoreError", "KrAutonomousPendingPlanStore")
