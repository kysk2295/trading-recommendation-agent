from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_theme_day_trial_terminal_models import KrThemeDayTrialTerminalArtifact

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kr_theme_day_trial_terminals (
  artifact_key TEXT PRIMARY KEY,
  trial_id TEXT NOT NULL UNIQUE,
  terminal_kind TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX kr_theme_day_trial_terminals_by_kind
ON kr_theme_day_trial_terminals(terminal_kind, trial_id);
CREATE TRIGGER kr_theme_day_trial_terminals_no_update
BEFORE UPDATE ON kr_theme_day_trial_terminals BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_day_trial_terminals_no_delete
BEFORE DELETE ON kr_theme_day_trial_terminals BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kr_theme_day_trial_terminals",
        "kr_theme_day_trial_terminals_by_kind",
        "kr_theme_day_trial_terminals_no_update",
        "kr_theme_day_trial_terminals_no_delete",
    }
)


class InvalidKrThemeDayTrialTerminalStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day trial terminal store is invalid"


@final
class KrThemeDayTrialTerminalStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def artifacts(self) -> tuple[KrThemeDayTrialTerminalArtifact, ...]:
        if self.path.is_symlink():
            raise InvalidKrThemeDayTrialTerminalStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)) as connection:
                _require_schema(connection)
                rows: list[tuple[str, str, str, str, str]] = connection.execute(
                    "SELECT artifact_key,trial_id,terminal_kind,payload_sha256,payload_json "
                    "FROM kr_theme_day_trial_terminals ORDER BY rowid"
                ).fetchall()
            return tuple(_artifact_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayTrialTerminalStoreError from None

    def append(self, artifact: KrThemeDayTrialTerminalArtifact) -> bool:
        try:
            artifact = KrThemeDayTrialTerminalArtifact.model_validate(artifact.model_dump(mode="python"))
            _ = self.artifacts()
            if self.path.is_symlink():
                raise InvalidKrThemeDayTrialTerminalStoreError
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(artifact)
                existing: tuple[str, str, str, str, str] | None = connection.execute(
                    "SELECT artifact_key,trial_id,terminal_kind,payload_sha256,payload_json "
                    "FROM kr_theme_day_trial_terminals WHERE trial_id=?",
                    (artifact.payload.trial_id,),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKrThemeDayTrialTerminalStoreError
                    connection.rollback()
                    return False
                _ = connection.execute("INSERT INTO kr_theme_day_trial_terminals VALUES (?,?,?,?,?)", row)
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDayTrialTerminalStoreError from None


def _row(artifact: KrThemeDayTrialTerminalArtifact) -> tuple[str, str, str, str, str]:
    payload = canonical_experiment_ledger_json(artifact)
    return (
        artifact.artifact_id,
        artifact.payload.trial_id,
        artifact.payload.terminal_kind.value,
        hashlib.sha256(payload.encode()).hexdigest(),
        payload,
    )


def _artifact_from_row(row: tuple[str, str, str, str, str]) -> KrThemeDayTrialTerminalArtifact:
    key, trial_id, terminal_kind, payload_sha, payload = row
    artifact = KrThemeDayTrialTerminalArtifact.model_validate_json(payload)
    if (
        row != _row(artifact)
        or key != artifact.artifact_id
        or trial_id != artifact.payload.trial_id
        or terminal_kind != artifact.payload.terminal_kind.value
        or payload_sha != hashlib.sha256(payload.encode()).hexdigest()
    ):
        raise InvalidKrThemeDayTrialTerminalStoreError
    return artifact


def _prepare(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKrThemeDayTrialTerminalStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    )
    if objects != _OBJECTS:
        raise InvalidKrThemeDayTrialTerminalStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDayTrialTerminalStoreError
