from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, final

from pydantic import ValidationError

from trading_agent.kr_social_signal_models import KrSocialSignal, canonical_kr_social_signal_json
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA_VERSION: Final = 1
_SHA256: Final = re.compile(r"^[a-f0-9]{64}$")
_STATEMENTS: Final = (
    "CREATE TABLE kr_social_signals (signal_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
    "symbol TEXT NOT NULL, normalized_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL)",
    "CREATE INDEX kr_social_signals_by_task ON kr_social_signals(task_id, normalized_at, signal_id)",
    "CREATE TRIGGER kr_social_signals_no_update BEFORE UPDATE ON kr_social_signals "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER kr_social_signals_no_delete BEFORE DELETE ON kr_social_signals "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
)
_EXPECTED_SCHEMA: Final = {
    ("table", "kr_social_signals"): " ".join(_STATEMENTS[0].split()),
    ("index", "kr_social_signals_by_task"): " ".join(_STATEMENTS[1].split()),
    ("trigger", "kr_social_signals_no_update"): " ".join(_STATEMENTS[2].split()),
    ("trigger", "kr_social_signals_no_delete"): " ".join(_STATEMENTS[3].split()),
}


class InvalidKrSocialSignalStoreError(RuntimeError):
    def __str__(self) -> str:
        return "KR social signal store is invalid"


class KrSocialSignalConflictError(RuntimeError):
    def __str__(self) -> str:
        return "KR social signal conflict"


@final
class KrSocialSignalStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def append(self, signal: KrSocialSignal) -> bool:
        try:
            trusted = KrSocialSignal.model_validate(signal.model_dump(mode="python"))
            payload = canonical_kr_social_signal_json(trusted)
            with _open_writer(self.path) as connection:
                return _append(connection, trusted, payload)
        except KrSocialSignalConflictError:
            raise
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrSocialSignalStoreError from None

    def get(self, signal_id: str) -> KrSocialSignal | None:
        if _SHA256.fullmatch(signal_id) is None:
            raise InvalidKrSocialSignalStoreError
        if self.path.is_symlink():
            raise InvalidKrSocialSignalStoreError
        if not self.path.exists():
            return None
        try:
            with _open_reader(self.path) as connection:
                row = connection.execute(
                    "SELECT signal_id,task_id,symbol,normalized_at,payload_sha256,payload_json "
                    "FROM kr_social_signals WHERE signal_id=?",
                    (signal_id,),
                ).fetchone()
                return None if row is None else _decode(row)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrSocialSignalStoreError from None

    def signals_for_task(self, task_id: str) -> tuple[KrSocialSignal, ...]:
        if _SHA256.fullmatch(task_id) is None:
            raise InvalidKrSocialSignalStoreError
        if self.path.is_symlink():
            raise InvalidKrSocialSignalStoreError
        if not self.path.exists():
            return ()
        try:
            with _open_reader(self.path) as connection:
                rows = connection.execute(
                    "SELECT signal_id,task_id,symbol,normalized_at,payload_sha256,payload_json "
                    "FROM kr_social_signals WHERE task_id=? ORDER BY normalized_at,signal_id",
                    (task_id,),
                ).fetchall()
                return tuple(_decode(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValidationError, ValueError):
            raise InvalidKrSocialSignalStoreError from None


def _append(connection: sqlite3.Connection, signal: KrSocialSignal, payload: str) -> bool:
    payload_sha256 = hashlib.sha256(payload.encode("ascii")).hexdigest()
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT signal_id,task_id,symbol,normalized_at,payload_sha256,payload_json "
            "FROM kr_social_signals WHERE signal_id=?",
            (signal.signal_id,),
        ).fetchone()
        if existing is not None:
            try:
                stored = _decode(existing)
            except InvalidKrSocialSignalStoreError:
                raise KrSocialSignalConflictError from None
            if stored == signal and existing == _row(signal, payload, payload_sha256):
                connection.commit()
                return False
            raise KrSocialSignalConflictError
        connection.execute("INSERT INTO kr_social_signals VALUES (?,?,?,?,?,?)", _row(signal, payload, payload_sha256))
        connection.commit()
        return True
    except (KrSocialSignalConflictError, InvalidKrSocialSignalStoreError, sqlite3.Error):
        connection.rollback()
        raise


def _decode(row: tuple[str, ...]) -> KrSocialSignal:
    if len(row) != 6:
        raise InvalidKrSocialSignalStoreError
    signal = KrSocialSignal.model_validate_json(row[5])
    payload = canonical_kr_social_signal_json(signal)
    if row != _row(signal, payload, hashlib.sha256(payload.encode("ascii")).hexdigest()):
        raise InvalidKrSocialSignalStoreError
    return signal


def _row(signal: KrSocialSignal, payload: str, payload_sha256: str) -> tuple[str, str, str, str, str, str]:
    return (signal.signal_id, signal.task_id, signal.symbol, _timestamp(signal), payload_sha256, payload)


def _timestamp(signal: KrSocialSignal) -> str:
    return str(signal.model_dump(mode="json")["normalized_at"])


@contextmanager
def _open_writer(path: Path) -> Iterator[sqlite3.Connection]:
    parent: int | None = None
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        parent = open_private_parent(path.parent, create=True)
        require_private_directory_query_only(parent)
        descriptor = _open_file(parent, path.name, write=True)
        connection = sqlite3.connect(path, timeout=5.0)
        _require_identity(parent, path.name, descriptor)
        _prepare(connection)
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


@contextmanager
def _open_reader(path: Path) -> Iterator[sqlite3.Connection]:
    parent: int | None = None
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        parent = open_private_parent(path.parent, create=False)
        require_private_directory_query_only(parent)
        descriptor = _open_file(parent, path.name, write=False)
        connection = sqlite3.connect(sqlite_read_only_uri(path), uri=True)
        _require_identity(parent, path.name, descriptor)
        connection.execute("PRAGMA query_only=ON")
        _require_schema(connection)
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


def _open_file(parent: int, name: str, *, write: bool) -> int:
    flags = os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_RDWR if write else os.O_RDONLY)
    created = False
    if write:
        try:
            descriptor = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            created = True
        except FileExistsError:
            descriptor = os.open(name, flags, dir_fd=parent)
    else:
        descriptor = os.open(name, flags, dir_fd=parent)
    if created:
        os.fchmod(descriptor, 0o600)
    if not _private_file(os.fstat(descriptor)):
        os.close(descriptor)
        raise InvalidKrSocialSignalStoreError
    return descriptor


def _require_identity(parent: int, name: str, descriptor: int) -> None:
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (
        not _private_file(named)
        or not _private_file(opened)
        or (named.st_dev, named.st_ino)
        != (
            opened.st_dev,
            opened.st_ino,
        )
    ):
        raise InvalidKrSocialSignalStoreError


def _private_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == _current_owner_id()
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _current_owner_id() -> int:
    return os.getuid()


def _prepare(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("BEGIN IMMEDIATE")
    try:
        if connection.execute("PRAGMA user_version").fetchone() == (0,):
            for statement in _STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        _require_schema(connection)
        connection.commit()
    except (InvalidKrSocialSignalStoreError, sqlite3.Error):
        connection.rollback()
        raise


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKrSocialSignalStoreError
    objects = {
        (str(row[0]), str(row[1])): " ".join(str(row[2]).split())
        for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if objects != _EXPECTED_SCHEMA:
        raise InvalidKrSocialSignalStoreError


__all__ = (
    "InvalidKrSocialSignalStoreError",
    "KrSocialSignalConflictError",
    "KrSocialSignalStore",
)
