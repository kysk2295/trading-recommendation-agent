from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_theme_day_shadow_entry_models import (
    KrThemeDayShadowEntry,
)
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kr_theme_day_shadow_entries (
  entry_key TEXT PRIMARY KEY,
  signal_id TEXT NOT NULL UNIQUE,
  trial_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX kr_theme_day_shadow_entries_by_trial
ON kr_theme_day_shadow_entries(trial_id, signal_id);
CREATE TRIGGER kr_theme_day_shadow_entries_no_update
BEFORE UPDATE ON kr_theme_day_shadow_entries BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_day_shadow_entries_no_delete
BEFORE DELETE ON kr_theme_day_shadow_entries BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kr_theme_day_shadow_entries",
        "kr_theme_day_shadow_entries_by_trial",
        "kr_theme_day_shadow_entries_no_update",
        "kr_theme_day_shadow_entries_no_delete",
    }
)


class InvalidKrThemeDayShadowEntryStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day shadow entry store is invalid"


@final
class KrThemeDayShadowEntryStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def entries(self) -> tuple[KrThemeDayShadowEntry, ...]:
        if self.path.is_symlink():
            raise InvalidKrThemeDayShadowEntryStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(sqlite_read_only_uri(self.path), uri=True)) as connection:
                _require_schema(connection)
                rows: list[tuple[str, str, str, str, str]] = connection.execute(
                    "SELECT entry_key,signal_id,trial_id,payload_sha256,payload_json "
                    "FROM kr_theme_day_shadow_entries ORDER BY rowid"
                ).fetchall()
            return tuple(_entry_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayShadowEntryStoreError from None

    def append(self, entry: KrThemeDayShadowEntry) -> bool:
        try:
            entry = _validated_entry(entry)
            _ = self.entries()
            if self.path.is_symlink():
                raise InvalidKrThemeDayShadowEntryStoreError
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(entry)
                existing: tuple[str, str, str, str, str] | None = connection.execute(
                    "SELECT entry_key,signal_id,trial_id,payload_sha256,payload_json "
                    "FROM kr_theme_day_shadow_entries WHERE signal_id=?",
                    (entry.signal_id,),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKrThemeDayShadowEntryStoreError
                    connection.rollback()
                    return False
                _ = connection.execute(
                    "INSERT INTO kr_theme_day_shadow_entries VALUES (?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayShadowEntryStoreError from None


def _row(entry: KrThemeDayShadowEntry) -> tuple[str, str, str, str, str]:
    payload = canonical_experiment_ledger_json(entry)
    return (
        entry.entry_id,
        entry.signal_id,
        entry.trial_id,
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _entry_from_row(row: tuple[str, str, str, str, str]) -> KrThemeDayShadowEntry:
    key, signal_id, trial_id, payload_sha, payload = row
    entry = KrThemeDayShadowEntry.model_validate_json(payload)
    if row != _row(entry) or key != entry.entry_id or signal_id != entry.signal_id or trial_id != entry.trial_id:
        raise InvalidKrThemeDayShadowEntryStoreError
    if payload_sha != hashlib.sha256(payload.encode()).hexdigest():
        raise InvalidKrThemeDayShadowEntryStoreError
    return entry


def _validated_entry(entry: KrThemeDayShadowEntry) -> KrThemeDayShadowEntry:
    return KrThemeDayShadowEntry.model_validate(entry.model_dump(mode="python"))


def _prepare(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKrThemeDayShadowEntryStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if objects != _OBJECTS:
        raise InvalidKrThemeDayShadowEntryStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDayShadowEntryStoreError
