from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final

from trading_agent.kr_theme_day_session_evidence import (
    InvalidKrThemeDaySessionEvidenceError,
    KrThemeDaySessionSourceAttestation,
    kr_theme_day_session_source_attestation_bytes,
    kr_theme_day_session_source_attestation_from_bytes,
)

_SCHEMA: Final = """
CREATE TABLE kr_theme_day_session_source_attestations (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  attestation_id TEXT NOT NULL UNIQUE,
  event_id TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  cycle_key TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload BLOB NOT NULL
);
CREATE INDEX kr_theme_day_session_source_attestations_by_session
ON kr_theme_day_session_source_attestations(session_id, generation);
CREATE TRIGGER kr_theme_day_session_source_attestations_no_update
BEFORE UPDATE ON kr_theme_day_session_source_attestations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_day_session_source_attestations_no_delete
BEFORE DELETE ON kr_theme_day_session_source_attestations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kr_theme_day_session_source_attestations",
        "kr_theme_day_session_source_attestations_by_session",
        "kr_theme_day_session_source_attestations_no_delete",
        "kr_theme_day_session_source_attestations_no_update",
    }
)


@final
class KrThemeDaySessionEvidenceStore:
    __slots__ = ("path",)

    def __init__(self, audit_path: Path) -> None:
        absolute = audit_path.expanduser().absolute()
        self.path = absolute.with_name(f"{absolute.stem}-evidence{absolute.suffix}")

    def append(self, attestation: KrThemeDaySessionSourceAttestation) -> bool:
        try:
            payload = kr_theme_day_session_source_attestation_bytes(attestation)
            row = (
                attestation.attestation_id,
                attestation.event_id,
                attestation.session_id,
                attestation.phase.value,
                attestation.cycle_key,
                hashlib.sha256(payload).hexdigest(),
                payload,
            )
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT attestation_id,event_id,session_id,phase,cycle_key,payload_sha256,payload "
                    "FROM kr_theme_day_session_source_attestations WHERE event_id=?",
                    (attestation.event_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise InvalidKrThemeDaySessionEvidenceError
                    return False
                _ = connection.execute(
                    "INSERT INTO kr_theme_day_session_source_attestations "
                    "(attestation_id,event_id,session_id,phase,cycle_key,payload_sha256,payload) "
                    "VALUES (?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDaySessionEvidenceError from None

    def attestations(self, session_id: str) -> tuple[KrThemeDaySessionSourceAttestation, ...]:
        if not self.path.exists():
            return ()
        try:
            with closing(self._connection(write=False)) as connection:
                rows = connection.execute(
                    "SELECT attestation_id,event_id,session_id,phase,cycle_key,payload_sha256,payload "
                    "FROM kr_theme_day_session_source_attestations WHERE session_id=? ORDER BY generation",
                    (session_id,),
                ).fetchall()
            attestations: list[KrThemeDaySessionSourceAttestation] = []
            for row in rows:
                if hashlib.sha256(row[6]).hexdigest() != row[5]:
                    raise InvalidKrThemeDaySessionEvidenceError
                attestation = kr_theme_day_session_source_attestation_from_bytes(row[6])
                if (
                    attestation.attestation_id,
                    attestation.event_id,
                    attestation.session_id,
                    attestation.phase.value,
                    attestation.cycle_key,
                ) != row[:5]:
                    raise InvalidKrThemeDaySessionEvidenceError
                attestations.append(attestation)
            return tuple(attestations)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKrThemeDaySessionEvidenceError from None

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise InvalidKrThemeDaySessionEvidenceError
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            existed = self.path.exists()
            if existed:
                _require_private(self.path)
            connection = sqlite3.connect(self.path, timeout=0.0)
            if not existed:
                os.chmod(self.path, 0o600)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA)
                _ = connection.execute("PRAGMA user_version=1")
                connection.commit()
        else:
            _require_private(self.path)
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            _ = connection.execute("PRAGMA query_only=ON")
        objects = frozenset(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%'"
            )
        )
        if connection.execute("PRAGMA user_version").fetchone() != (1,) or objects != _OBJECTS:
            connection.close()
            raise InvalidKrThemeDaySessionEvidenceError
        return connection


def _require_private(path: Path) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKrThemeDaySessionEvidenceError
