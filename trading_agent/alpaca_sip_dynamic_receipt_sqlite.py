from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from pathlib import Path
from types import TracebackType

from trading_agent.alpaca_sip_dynamic_receipt_models import AlpacaSipDynamicReceiptError

_SCHEMA_VERSION = 2
_SCHEMA_V1 = (
    "CREATE TABLE dynamic_connections (connection_epoch TEXT PRIMARY KEY,plan_id TEXT NOT NULL,"
    "policy_identity_sha256 TEXT NOT NULL,policy_semantic_version TEXT NOT NULL,"
    "evaluated_at TEXT NOT NULL,market_date TEXT NOT NULL,bindings_json TEXT NOT NULL,"
    "bound_at TEXT NOT NULL,content_sha256 TEXT NOT NULL);"
    "CREATE TABLE dynamic_receipts (generation INTEGER PRIMARY KEY AUTOINCREMENT,"
    "receipt_id TEXT NOT NULL UNIQUE,connection_epoch TEXT NOT NULL,sequence INTEGER NOT NULL,"
    "plan_id TEXT NOT NULL,kind TEXT NOT NULL,received_at TEXT NOT NULL,payload_sha256 TEXT NOT NULL,"
    "payload BLOB NOT NULL,UNIQUE(connection_epoch,sequence));"
    "CREATE TRIGGER dynamic_connections_no_update BEFORE UPDATE ON dynamic_connections "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER dynamic_connections_no_delete BEFORE DELETE ON dynamic_connections "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER dynamic_receipts_no_update BEFORE UPDATE ON dynamic_receipts "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER dynamic_receipts_no_delete BEFORE DELETE ON dynamic_receipts "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
)
_SCHEMA_V2 = (
    "CREATE TABLE dynamic_terminals (connection_epoch TEXT PRIMARY KEY,plan_id TEXT NOT NULL,"
    "terminal_at TEXT NOT NULL,status TEXT NOT NULL,receipt_count INTEGER NOT NULL,"
    "content_sha256 TEXT NOT NULL);"
    "CREATE TRIGGER dynamic_terminals_no_update BEFORE UPDATE ON dynamic_terminals "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER dynamic_terminals_no_delete BEFORE DELETE ON dynamic_terminals "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
)
_OBJECTS_V1 = {
    "dynamic_connections",
    "dynamic_connections_no_delete",
    "dynamic_connections_no_update",
    "dynamic_receipts",
    "dynamic_receipts_no_delete",
    "dynamic_receipts_no_update",
}
_OBJECTS_V2 = _OBJECTS_V1 | {
    "dynamic_terminals",
    "dynamic_terminals_no_delete",
    "dynamic_terminals_no_update",
}


class AlpacaSipDynamicReceiptWriter:
    __slots__ = ("_connection", "_handle", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None
        self._connection = None

    def __enter__(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self._path}.writer.lock")
        require_private_dynamic_receipt_file(lock_path)
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        self._handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        try:
            require_private_dynamic_receipt_file(self._path)
            connection = sqlite3.connect(self._path)
            os.chmod(self._path, 0o600)
            require_private_dynamic_receipt_file(self._path)
            _prepare(connection)
            self._connection = connection
            return connection
        except (AlpacaSipDynamicReceiptError, OSError, sqlite3.Error, ValueError):
            self._handle.close()
            self._handle = None
            raise AlpacaSipDynamicReceiptError from None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = exc_type, exc_value, traceback
        if self._connection is not None:
            self._connection.close()
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


class AlpacaSipDynamicConnectionLease:
    __slots__ = ("_handle", "_path")

    def __init__(self, database_path: Path) -> None:
        self._path = Path(f"{database_path}.owner.lock")
        self._handle = None

    def __enter__(self) -> None:
        descriptor = -1
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            require_private_dynamic_receipt_file(self._path)
            descriptor = os.open(
                self._path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.fchmod(descriptor, 0o600)
            self._handle = os.fdopen(descriptor, "a+", encoding="utf-8")
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (AlpacaSipDynamicReceiptError, OSError, ValueError):
            if self._handle is None and descriptor >= 0:
                os.close(descriptor)
            elif self._handle is not None:
                self._handle.close()
                self._handle = None
            raise AlpacaSipDynamicReceiptError from None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = exc_type, exc_value, traceback
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


def require_dynamic_receipt_schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    version = connection.execute("PRAGMA user_version").fetchone()
    objects = {row[0] for row in rows}
    if (
        (version == (1,) and objects != _OBJECTS_V1)
        or (version == (_SCHEMA_VERSION,) and objects != _OBJECTS_V2)
        or version not in {(1,), (_SCHEMA_VERSION,)}
    ):
        raise AlpacaSipDynamicReceiptError


def require_private_dynamic_receipt_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaSipDynamicReceiptError


def _prepare(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(_SCHEMA_V1)
        _ = connection.execute("PRAGMA user_version=1")
        connection.commit()
        version = (1,)
    if version == (1,):
        require_dynamic_receipt_schema(connection)
        connection.executescript(_SCHEMA_V2)
        _ = connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        connection.commit()
    require_dynamic_receipt_schema(connection)


__all__ = (
    "AlpacaSipDynamicConnectionLease",
    "AlpacaSipDynamicReceiptWriter",
    "require_dynamic_receipt_schema",
    "require_private_dynamic_receipt_file",
)
