from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, override

from trading_agent.private_directory_identity import open_private_parent, require_private_directory_query_only
from trading_agent.sqlite_uri import sqlite_read_only_uri

_VERSION: Final = 1
_STATEMENTS: Final = (
    "CREATE TABLE kr_loop_snapshots (snapshot_id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,"
    "previous_snapshot_id TEXT,state TEXT NOT NULL,updated_at TEXT NOT NULL,payload_sha256 TEXT NOT NULL,"
    "payload_json TEXT NOT NULL)",
    "CREATE INDEX kr_loop_snapshots_candidate ON kr_loop_snapshots(candidate_id,updated_at,snapshot_id)",
    "CREATE TABLE kr_loop_releases (release_id TEXT PRIMARY KEY,generation INTEGER NOT NULL UNIQUE,"
    "action TEXT NOT NULL,candidate_id TEXT NOT NULL,recorded_at TEXT NOT NULL,payload_sha256 TEXT NOT NULL,"
    "payload_json TEXT NOT NULL)",
    "CREATE TRIGGER kr_loop_snapshots_no_update BEFORE UPDATE ON kr_loop_snapshots "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END",
    "CREATE TRIGGER kr_loop_snapshots_no_delete BEFORE DELETE ON kr_loop_snapshots "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END",
    "CREATE TRIGGER kr_loop_releases_no_update BEFORE UPDATE ON kr_loop_releases "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END",
    "CREATE TRIGGER kr_loop_releases_no_delete BEFORE DELETE ON kr_loop_releases "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END",
)
_EXPECTED: Final = {
    (kind, name): " ".join(statement.split())
    for kind, name, statement in (
        ("table", "kr_loop_snapshots", _STATEMENTS[0]),
        ("index", "kr_loop_snapshots_candidate", _STATEMENTS[1]),
        ("table", "kr_loop_releases", _STATEMENTS[2]),
        ("trigger", "kr_loop_snapshots_no_update", _STATEMENTS[3]),
        ("trigger", "kr_loop_snapshots_no_delete", _STATEMENTS[4]),
        ("trigger", "kr_loop_releases_no_update", _STATEMENTS[5]),
        ("trigger", "kr_loop_releases_no_delete", _STATEMENTS[6]),
    )
}


class InvalidKrLoopStoreSqliteError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop Engineer SQLite boundary is invalid"


@contextmanager
def open_kr_loop_database(path: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    parent = open_private_parent(path.parent, create=write)
    descriptor: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        require_private_directory_query_only(parent)
        flags = os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_RDWR if write else os.O_RDONLY)
        try:
            descriptor = os.open(path.name, flags | (os.O_CREAT | os.O_EXCL if write else 0), 0o600, dir_fd=parent)
        except FileExistsError:
            descriptor = os.open(path.name, flags, dir_fd=parent)
        if not _private(os.fstat(descriptor)):
            raise InvalidKrLoopStoreSqliteError
        connection = sqlite3.connect(path if write else sqlite_read_only_uri(path), uri=not write, timeout=5.0)
        if not write:
            connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        if connection is not None:
            connection.close()
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def prepare_kr_loop_database(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA trusted_schema=OFF")
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        for statement in _STATEMENTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version={_VERSION}")
        connection.commit()
    require_kr_loop_schema(connection)


def require_kr_loop_schema(connection: sqlite3.Connection) -> None:
    objects = {
        (str(row[0]), str(row[1])): " ".join(str(row[2]).split())
        for row in connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    if connection.execute("PRAGMA user_version").fetchone() != (_VERSION,) or objects != _EXPECTED:
        raise InvalidKrLoopStoreSqliteError


def _private(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


__all__ = (
    "InvalidKrLoopStoreSqliteError",
    "open_kr_loop_database",
    "prepare_kr_loop_database",
    "require_kr_loop_schema",
)
