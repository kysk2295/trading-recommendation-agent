from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_theme_day_shadow_exit_models import KrThemeDayShadowExit
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.sqlite_uri import sqlite_read_only_uri

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kr_theme_day_shadow_exits (
  exit_key TEXT PRIMARY KEY,
  entry_id TEXT NOT NULL UNIQUE,
  trial_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX kr_theme_day_shadow_exits_by_trial
ON kr_theme_day_shadow_exits(trial_id, entry_id);
CREATE TRIGGER kr_theme_day_shadow_exits_no_update
BEFORE UPDATE ON kr_theme_day_shadow_exits BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_day_shadow_exits_no_delete
BEFORE DELETE ON kr_theme_day_shadow_exits BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kr_theme_day_shadow_exits",
        "kr_theme_day_shadow_exits_by_trial",
        "kr_theme_day_shadow_exits_no_update",
        "kr_theme_day_shadow_exits_no_delete",
    }
)


class InvalidKrThemeDayShadowExitStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day shadow exit store is invalid"


@final
class KrThemeDayShadowExitStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def exits(self) -> tuple[KrThemeDayShadowExit, ...]:
        if self.path.is_symlink():
            raise InvalidKrThemeDayShadowExitStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(sqlite_read_only_uri(self.path), uri=True)) as connection:
                _require_schema(connection)
                rows: list[tuple[str, str, str, str, str]] = connection.execute(
                    "SELECT exit_key,entry_id,trial_id,payload_sha256,payload_json "
                    "FROM kr_theme_day_shadow_exits ORDER BY rowid"
                ).fetchall()
            return tuple(_exit_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayShadowExitStoreError from None

    def append(self, exit: KrThemeDayShadowExit) -> bool:
        try:
            exit = KrThemeDayShadowExit.model_validate(exit.model_dump(mode="python"))
            _ = self.exits()
            if self.path.is_symlink():
                raise InvalidKrThemeDayShadowExitStoreError
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(exit)
                existing: tuple[str, str, str, str, str] | None = connection.execute(
                    "SELECT exit_key,entry_id,trial_id,payload_sha256,payload_json "
                    "FROM kr_theme_day_shadow_exits WHERE entry_id=?",
                    (exit.entry_id,),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKrThemeDayShadowExitStoreError
                    connection.rollback()
                    return False
                _ = connection.execute("INSERT INTO kr_theme_day_shadow_exits VALUES (?,?,?,?,?)", row)
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayShadowExitStoreError from None


def _row(exit: KrThemeDayShadowExit) -> tuple[str, str, str, str, str]:
    payload = canonical_experiment_ledger_json(exit)
    return (
        exit.exit_id,
        exit.entry_id,
        exit.trial_id,
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _exit_from_row(row: tuple[str, str, str, str, str]) -> KrThemeDayShadowExit:
    key, entry_id, trial_id, payload_sha, payload = row
    exit = KrThemeDayShadowExit.model_validate_json(payload)
    if row != _row(exit) or key != exit.exit_id or entry_id != exit.entry_id or trial_id != exit.trial_id:
        raise InvalidKrThemeDayShadowExitStoreError
    if payload_sha != hashlib.sha256(payload.encode()).hexdigest():
        raise InvalidKrThemeDayShadowExitStoreError
    return exit


def _prepare(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKrThemeDayShadowExitStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if objects != _OBJECTS:
        raise InvalidKrThemeDayShadowExitStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDayShadowExitStoreError
