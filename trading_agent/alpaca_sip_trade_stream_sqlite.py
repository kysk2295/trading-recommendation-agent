from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from pathlib import Path
from types import TracebackType

from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipTradeStreamProtocolError

_SCHEMA_VERSION = 2
_SCHEMA_V1 = (
    "CREATE TABLE IF NOT EXISTS control_frames (generation INTEGER PRIMARY KEY AUTOINCREMENT,"
    "connection_epoch TEXT NOT NULL,sequence INTEGER NOT NULL,received_at TEXT NOT NULL,"
    "payload_sha256 TEXT NOT NULL,payload BLOB NOT NULL,UNIQUE(connection_epoch,sequence));"
    "CREATE TABLE IF NOT EXISTS data_links (connection_epoch TEXT NOT NULL,sequence INTEGER NOT NULL,"
    "receipt_id TEXT NOT NULL,generation INTEGER NOT NULL,received_at TEXT NOT NULL,"
    "PRIMARY KEY(connection_epoch,sequence),UNIQUE(receipt_id));"
    "CREATE TABLE IF NOT EXISTS terminal_sessions (connection_epoch TEXT PRIMARY KEY,symbol TEXT NOT NULL,"
    "market_date TEXT NOT NULL,authorized_at TEXT NOT NULL,subscribed_at TEXT NOT NULL,"
    "terminal_at TEXT NOT NULL,status TEXT NOT NULL,data_count INTEGER NOT NULL,content_sha256 TEXT NOT NULL);"
    "CREATE TRIGGER control_frames_no_update BEFORE UPDATE ON control_frames "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER control_frames_no_delete BEFORE DELETE ON control_frames "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER data_links_no_update BEFORE UPDATE ON data_links "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER data_links_no_delete BEFORE DELETE ON data_links "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER terminal_sessions_no_update BEFORE UPDATE ON terminal_sessions "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER terminal_sessions_no_delete BEFORE DELETE ON terminal_sessions "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
)
_SCHEMA_V2 = (
    "CREATE TABLE IF NOT EXISTS connection_attempts (connection_epoch TEXT PRIMARY KEY,"
    "symbol TEXT NOT NULL,market_date TEXT NOT NULL,failed_at TEXT NOT NULL,stage TEXT NOT NULL,"
    "failure_code TEXT NOT NULL,content_sha256 TEXT NOT NULL);"
    "CREATE TRIGGER connection_attempts_no_update BEFORE UPDATE ON connection_attempts "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    "CREATE TRIGGER connection_attempts_no_delete BEFORE DELETE ON connection_attempts "
    "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
)
_SCHEMA_OBJECTS_V1 = {
    "control_frames",
    "control_frames_no_delete",
    "control_frames_no_update",
    "data_links",
    "data_links_no_delete",
    "data_links_no_update",
    "terminal_sessions",
    "terminal_sessions_no_delete",
    "terminal_sessions_no_update",
}
_SCHEMA_OBJECTS_V2 = _SCHEMA_OBJECTS_V1 | {
    "connection_attempts",
    "connection_attempts_no_delete",
    "connection_attempts_no_update",
}


class AlpacaSipStreamWriter:
    __slots__ = ("_connection", "_handle", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None
        self._connection = None

    def __enter__(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self._path}.writer.lock")
        require_private_alpaca_sip_stream_file(lock_path)
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        self._handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        try:
            require_private_alpaca_sip_stream_file(self._path)
            connection = sqlite3.connect(self._path)
            os.chmod(self._path, 0o600)
            require_private_alpaca_sip_stream_file(self._path)
            _prepare(connection)
            self._connection = connection
            return connection
        except (AlpacaSipTradeStreamProtocolError, OSError, sqlite3.Error, ValueError):
            self._handle.close()
            self._handle = None
            raise

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


def require_alpaca_sip_stream_schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    version = connection.execute("PRAGMA user_version").fetchone()
    objects = {row[0] for row in rows}
    if (
        (version == (1,) and objects != _SCHEMA_OBJECTS_V1)
        or (version == (_SCHEMA_VERSION,) and objects != _SCHEMA_OBJECTS_V2)
        or version not in {(1,), (_SCHEMA_VERSION,)}
    ):
        raise AlpacaSipTradeStreamProtocolError


def require_private_alpaca_sip_stream_file(path: Path) -> None:
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
        raise AlpacaSipTradeStreamProtocolError


def _prepare(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(_SCHEMA_V1)
        _ = connection.execute("PRAGMA user_version=1")
        connection.commit()
        version = (1,)
    if version == (1,):
        require_alpaca_sip_stream_schema(connection)
        connection.executescript(_SCHEMA_V2)
        _ = connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        connection.commit()
    require_alpaca_sip_stream_schema(connection)


__all__ = (
    "AlpacaSipStreamWriter",
    "require_alpaca_sip_stream_schema",
    "require_private_alpaca_sip_stream_file",
)
