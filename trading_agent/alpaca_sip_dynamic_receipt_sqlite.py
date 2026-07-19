from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from pathlib import Path
from types import TracebackType

from trading_agent.alpaca_sip_dynamic_receipt_models import AlpacaSipDynamicReceiptError

_SCHEMA_VERSION = 1
_SCHEMA = (
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
_OBJECTS = {
    "dynamic_connections",
    "dynamic_connections_no_delete",
    "dynamic_connections_no_update",
    "dynamic_receipts",
    "dynamic_receipts_no_delete",
    "dynamic_receipts_no_update",
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


def require_dynamic_receipt_schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if (
        connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,)
        or {row[0] for row in rows} != _OBJECTS
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
    if connection.execute("PRAGMA user_version").fetchone() == (0,):
        connection.executescript(_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        connection.commit()
    require_dynamic_receipt_schema(connection)


__all__ = (
    "AlpacaSipDynamicReceiptWriter",
    "require_dynamic_receipt_schema",
    "require_private_dynamic_receipt_file",
)
