from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import final, override

from trading_agent.alpaca_sip_dynamic_quote_actionability import (
    AlpacaSipDynamicQuoteActionabilityDecision,
)
from trading_agent.alpaca_sip_quote_actionability_artifact import (
    AlpacaSipQuoteActionabilityArtifact,
    actionability_artifact,
    actionability_artifact_bytes,
    actionability_artifact_from_bytes,
)
from trading_agent.trade_signal_publication import TradeSignalPublication

_SCHEMA = """
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
_OBJECTS = {
    "alpaca_sip_quote_actionability",
    "alpaca_sip_quote_actionability_no_delete",
    "alpaca_sip_quote_actionability_no_update",
}
type _ArtifactRow = tuple[str, str, str, str, bytes]


class AlpacaSipQuoteActionabilityStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP quote actionability store is invalid"


@final
class AlpacaSipQuoteActionabilityStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(
        self,
        base: TradeSignalPublication,
        decision: AlpacaSipDynamicQuoteActionabilityDecision,
    ) -> bool:
        try:
            artifact = actionability_artifact(base, decision)
            _ = self.records()
            payload = actionability_artifact_bytes(artifact)
            row = _row(artifact, payload)
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing: _ArtifactRow | None = connection.execute(
                    "SELECT artifact_id,base_signal_id,scan_started_at,payload_sha256,payload_json "
                    "FROM alpaca_sip_quote_actionability WHERE artifact_id=?",
                    (artifact.artifact_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSipQuoteActionabilityStoreError
                    return False
                connection.execute(
                    "INSERT INTO alpaca_sip_quote_actionability "
                    "(artifact_id,base_signal_id,scan_started_at,payload_sha256,payload_json) "
                    "VALUES (?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipQuoteActionabilityStoreError from None

    def records(self) -> tuple[AlpacaSipQuoteActionabilityArtifact, ...]:
        if self.path.is_symlink():
            raise AlpacaSipQuoteActionabilityStoreError
        if not self.path.exists():
            return ()
        try:
            with closing(self._connection(write=False)) as connection:
                rows: list[_ArtifactRow] = connection.execute(
                    "SELECT artifact_id,base_signal_id,scan_started_at,payload_sha256,payload_json "
                    "FROM alpaca_sip_quote_actionability ORDER BY generation"
                ).fetchall()
            artifacts: list[AlpacaSipQuoteActionabilityArtifact] = []
            for row in rows:
                artifact = actionability_artifact_from_bytes(row[4])
                if tuple(row) != _row(artifact, row[4]):
                    raise AlpacaSipQuoteActionabilityStoreError
                artifacts.append(artifact)
            return tuple(artifacts)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipQuoteActionabilityStoreError from None

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise AlpacaSipQuoteActionabilityStoreError
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            if existed:
                _require_private_file(self.path)
            connection = sqlite3.connect(self.path)
            if not existed:
                os.chmod(self.path, 0o600)
            _require_private_file(self.path)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
        else:
            _require_private_file(self.path)
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') AND name NOT LIKE 'sqlite_%'"
            )
        }
        if connection.execute("PRAGMA user_version").fetchone() != (1,) or objects != _OBJECTS:
            connection.close()
            raise AlpacaSipQuoteActionabilityStoreError
        return connection


def _row(
    artifact: AlpacaSipQuoteActionabilityArtifact,
    payload: bytes,
) -> _ArtifactRow:
    return (
        artifact.artifact_id,
        artifact.base_publication.signal.signal_id,
        artifact.assessment.scan_started_at.isoformat(),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AlpacaSipQuoteActionabilityStoreError


__all__ = (
    "AlpacaSipQuoteActionabilityStore",
    "AlpacaSipQuoteActionabilityStoreError",
)
