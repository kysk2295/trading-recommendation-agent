from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from trading_agent.private_directory_identity import open_private_parent, require_private_directory
from trading_agent.sec_edgar_schema import (
    SEC_EDGAR_SCHEMA,
    SEC_EDGAR_SCHEMA_OBJECTS,
    SEC_EDGAR_SCHEMA_VERSION,
)
from trading_agent.sec_edgar_store_semantics import require_store_semantics
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError
from trading_agent.sec_edgar_store_version_chain import require_all_version_chains
from trading_agent.sqlite_uri import sqlite_read_only_uri


def _schema_signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )


def _expected_schema_signature() -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(SEC_EDGAR_SCHEMA)
        return _schema_signature(connection)


_EXPECTED_SCHEMA_SIGNATURE = _expected_schema_signature()


@contextmanager
def sec_writer(path: Path) -> Iterator[sqlite3.Connection]:
    if path.is_symlink():
        raise InvalidSecEdgarStoreError
    parent_descriptor = open_private_parent(path.parent, create=True)
    try:
        require_private_directory(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    if path.exists():
        _require_private_file(path)
    connection = sqlite3.connect(path, timeout=0.0)
    try:
        os.chmod(path, 0o600)
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _prepare(connection)
        connection.execute("BEGIN IMMEDIATE")
        yield connection
    finally:
        connection.close()


@contextmanager
def sec_reader(path: Path) -> Iterator[sqlite3.Connection]:
    if path.is_symlink():
        raise InvalidSecEdgarStoreError
    _require_private_file(path)
    with closing(sqlite3.connect(sqlite_read_only_uri(path), uri=True)) as connection:
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _require_schema(connection)
        yield connection


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        if _schema_signature(connection):
            raise InvalidSecEdgarStoreError
        connection.executescript(
            f"BEGIN IMMEDIATE;{SEC_EDGAR_SCHEMA}PRAGMA user_version={SEC_EDGAR_SCHEMA_VERSION};COMMIT;"
        )
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (SEC_EDGAR_SCHEMA_VERSION,):
        raise InvalidSecEdgarStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if (
        objects != SEC_EDGAR_SCHEMA_OBJECTS
        or _schema_signature(connection) != _EXPECTED_SCHEMA_SIGNATURE
        or connection.execute("PRAGMA foreign_keys").fetchone() != (1,)
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    ):
        raise InvalidSecEdgarStoreError
    require_all_version_chains(connection)
    require_store_semantics(connection)


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidSecEdgarStoreError
