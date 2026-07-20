from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from trading_agent.alpaca_news_schema import (
    ALPACA_NEWS_SCHEMA,
    ALPACA_NEWS_SCHEMA_OBJECTS,
    ALPACA_NEWS_SCHEMA_VERSION,
)
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
    require_private_directory_query_only,
)
from trading_agent.sqlite_uri import sqlite_read_only_uri, sqlite_read_write_uri


class AlpacaNewsStoreError(ValueError):
    def __str__(self) -> str:
        return "Alpaca news store is invalid"


def _signature(connection: sqlite3.Connection) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )


def _expected_signature() -> tuple[tuple[str, str, str, str], ...]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(ALPACA_NEWS_SCHEMA)
        return _signature(connection)


_EXPECTED_SIGNATURE = _expected_signature()


@contextmanager
def news_writer(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        parent = open_private_parent(path.parent, create=True)
        try:
            require_private_directory(parent)
            descriptor = _open_file(path, parent, write=True)
            try:
                with closing(sqlite3.connect(sqlite_read_write_uri(path), uri=True, timeout=0.0)) as connection:
                    _require_bound(path, parent, descriptor)
                    _ = connection.execute("PRAGMA foreign_keys = ON")
                    _prepare(connection)
                    connection.execute("BEGIN IMMEDIATE")
                    completed = False
                    try:
                        yield connection
                        _require_bound(path, parent, descriptor)
                        connection.commit()
                        completed = True
                    finally:
                        if not completed and connection.in_transaction:
                            connection.rollback()
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise AlpacaNewsStoreError from None


@contextmanager
def news_reader(path: Path) -> Iterator[sqlite3.Connection]:
    try:
        parent = open_private_parent(path.parent, create=False)
        try:
            require_private_directory_query_only(parent)
            descriptor = _open_file(path, parent, write=False)
            try:
                with closing(sqlite3.connect(sqlite_read_only_uri(path), uri=True)) as connection:
                    _require_bound(path, parent, descriptor)
                    _ = connection.execute("PRAGMA query_only = ON")
                    _require_schema(connection)
                    yield connection
                    _require_bound(path, parent, descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise AlpacaNewsStoreError from None


def _prepare(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        if _signature(connection):
            raise AlpacaNewsStoreError
        connection.executescript(f"{ALPACA_NEWS_SCHEMA}PRAGMA user_version={ALPACA_NEWS_SCHEMA_VERSION};")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if (
        connection.execute("PRAGMA user_version").fetchone() != (ALPACA_NEWS_SCHEMA_VERSION,)
        or objects != ALPACA_NEWS_SCHEMA_OBJECTS
        or _signature(connection) != _EXPECTED_SIGNATURE
        or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
    ):
        raise AlpacaNewsStoreError


def _open_file(path: Path, parent: int, *, write: bool) -> int:
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_RDWR if write else os.O_RDONLY)
    try:
        descriptor = os.open(path.name, flags, dir_fd=parent)
    except FileNotFoundError:
        if not write:
            raise
        descriptor = os.open(path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
    _require_file(descriptor)
    return descriptor


def _require_bound(path: Path, parent: int, descriptor: int) -> None:
    require_open_directory_path(path.parent, parent)
    named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise AlpacaNewsStoreError
    _require_file(descriptor)


def _require_file(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaNewsStoreError
