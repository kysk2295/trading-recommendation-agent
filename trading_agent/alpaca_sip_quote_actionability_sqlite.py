from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import override

_SCHEMA_V1 = """
CREATE TABLE alpaca_sip_quote_actionability (
 generation INTEGER PRIMARY KEY AUTOINCREMENT,
 artifact_id TEXT NOT NULL UNIQUE,
 base_signal_id TEXT NOT NULL,
 scan_started_at TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,
 payload_json BLOB NOT NULL
);
CREATE TRIGGER alpaca_sip_quote_actionability_no_update
BEFORE UPDATE ON alpaca_sip_quote_actionability
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER alpaca_sip_quote_actionability_no_delete
BEFORE DELETE ON alpaca_sip_quote_actionability
BEGIN SELECT RAISE(ABORT, 'append only'); END;
"""
_SCHEMA_V2 = """
CREATE TABLE alpaca_sip_quote_actionability_creation (
 generation INTEGER PRIMARY KEY AUTOINCREMENT,
 creation_id TEXT NOT NULL UNIQUE,
 artifact_id TEXT NOT NULL UNIQUE,
 manifest_id TEXT NOT NULL,
 evaluated_at TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,
 payload_json BLOB NOT NULL
);
CREATE TRIGGER alpaca_sip_quote_actionability_creation_no_update
BEFORE UPDATE ON alpaca_sip_quote_actionability_creation
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER alpaca_sip_quote_actionability_creation_no_delete
BEFORE DELETE ON alpaca_sip_quote_actionability_creation
BEGIN SELECT RAISE(ABORT, 'append only'); END;
"""
_OBJECTS_V1 = {
    "alpaca_sip_quote_actionability",
    "alpaca_sip_quote_actionability_no_delete",
    "alpaca_sip_quote_actionability_no_update",
}
_OBJECTS_V2 = _OBJECTS_V1 | {
    "alpaca_sip_quote_actionability_creation",
    "alpaca_sip_quote_actionability_creation_no_delete",
    "alpaca_sip_quote_actionability_creation_no_update",
}


class AlpacaSipQuoteActionabilitySqliteError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP quote actionability SQLite is invalid"


def open_actionability_connection(
    path: Path,
    *,
    write: bool,
    target_version: int,
) -> sqlite3.Connection:
    if path.is_symlink() or target_version not in {1, 2}:
        raise AlpacaSipQuoteActionabilitySqliteError
    connection: sqlite3.Connection | None = None
    try:
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            if existed:
                _require_private_file(path)
            connection = sqlite3.connect(path)
            if not existed:
                os.chmod(path, 0o600)
            _require_private_file(path)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA_V1)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            if target_version == 2 and connection.execute("PRAGMA user_version").fetchone() == (1,):
                _require_schema(connection)
                connection.executescript(_SCHEMA_V2)
                connection.execute("PRAGMA user_version=2")
                connection.commit()
        else:
            _require_private_file(path)
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        _require_schema(connection)
        return connection
    except (OSError, sqlite3.Error, ValueError):
        if connection is not None:
            connection.close()
        raise AlpacaSipQuoteActionabilitySqliteError from None


def _require_schema(connection: sqlite3.Connection) -> None:
    objects = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
        )
    }
    version = connection.execute("PRAGMA user_version").fetchone()
    if (
        (version == (1,) and objects != _OBJECTS_V1)
        or (version == (2,) and objects != _OBJECTS_V2)
        or version not in {(1,), (2,)}
    ):
        raise AlpacaSipQuoteActionabilitySqliteError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaSipQuoteActionabilitySqliteError


__all__ = (
    "AlpacaSipQuoteActionabilitySqliteError",
    "open_actionability_connection",
)
