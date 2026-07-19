from __future__ import annotations

import hashlib
import sqlite3
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
from trading_agent.alpaca_sip_quote_actionability_creation import (
    AlpacaSipQuoteActionabilityAppendResult,
    AlpacaSipQuoteActionabilityCreation,
    actionability_creation_bytes,
    actionability_creation_from_bytes,
    build_actionability_creation,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import AlpacaSipQuoteActionabilityManifest
from trading_agent.alpaca_sip_quote_actionability_sqlite import open_actionability_connection
from trading_agent.trade_signal_publication import TradeSignalPublication

type _ArtifactRow = tuple[str, str, str, str, bytes]
type _CreationRow = tuple[str, str, str, str, str, bytes]


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
            row = _artifact_row(artifact, payload)
            with closing(open_actionability_connection(self.path, write=True, target_version=1)) as connection:
                if connection.execute("PRAGMA user_version").fetchone() != (1,):
                    raise AlpacaSipQuoteActionabilityStoreError
                connection.execute("BEGIN IMMEDIATE")
                appended = _insert_artifact(connection, artifact, row)
                connection.commit()
            return appended
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipQuoteActionabilityStoreError from None

    def append_for_manifest(
        self,
        manifest: AlpacaSipQuoteActionabilityManifest,
        decision: AlpacaSipDynamicQuoteActionabilityDecision,
    ) -> AlpacaSipQuoteActionabilityAppendResult:
        try:
            artifact = actionability_artifact(manifest.base_publication, decision)
            creation = build_actionability_creation(manifest, artifact)
            _ = self.records()
            _ = self.creations()
            artifact_payload = actionability_artifact_bytes(artifact)
            artifact_row = _artifact_row(artifact, artifact_payload)
            creation_payload = actionability_creation_bytes(creation)
            creation_row = _creation_row(creation, creation_payload)
            with closing(open_actionability_connection(self.path, write=True, target_version=2)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = _existing_creation(connection, artifact.artifact_id)
                if existing is not None:
                    restored = actionability_creation_from_bytes(existing[5])
                    if tuple(existing) != _creation_row(restored, existing[5]):
                        raise AlpacaSipQuoteActionabilityStoreError
                    if not _insert_artifact(connection, artifact, artifact_row):
                        return AlpacaSipQuoteActionabilityAppendResult(False, restored)
                    raise AlpacaSipQuoteActionabilityStoreError
                if not _insert_artifact(connection, artifact, artifact_row):
                    raise AlpacaSipQuoteActionabilityStoreError
                connection.execute(
                    "INSERT INTO alpaca_sip_quote_actionability_creation "
                    "(creation_id,artifact_id,manifest_id,evaluated_at,payload_sha256,payload_json) "
                    "VALUES (?,?,?,?,?,?)",
                    creation_row,
                )
                connection.commit()
            return AlpacaSipQuoteActionabilityAppendResult(True, creation)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipQuoteActionabilityStoreError from None

    def records(self) -> tuple[AlpacaSipQuoteActionabilityArtifact, ...]:
        if self.path.is_symlink():
            raise AlpacaSipQuoteActionabilityStoreError
        if not self.path.exists():
            return ()
        try:
            with closing(open_actionability_connection(self.path, write=False, target_version=1)) as connection:
                rows: list[_ArtifactRow] = connection.execute(
                    "SELECT artifact_id,base_signal_id,scan_started_at,payload_sha256,payload_json "
                    "FROM alpaca_sip_quote_actionability ORDER BY generation"
                ).fetchall()
            artifacts: list[AlpacaSipQuoteActionabilityArtifact] = []
            for row in rows:
                artifact = actionability_artifact_from_bytes(row[4])
                if tuple(row) != _artifact_row(artifact, row[4]):
                    raise AlpacaSipQuoteActionabilityStoreError
                artifacts.append(artifact)
            return tuple(artifacts)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipQuoteActionabilityStoreError from None

    def creations(self) -> tuple[AlpacaSipQuoteActionabilityCreation, ...]:
        if self.path.is_symlink():
            raise AlpacaSipQuoteActionabilityStoreError
        if not self.path.exists():
            return ()
        try:
            with closing(open_actionability_connection(self.path, write=False, target_version=1)) as connection:
                if connection.execute("PRAGMA user_version").fetchone() == (1,):
                    return ()
                rows: list[_CreationRow] = connection.execute(
                    "SELECT creation_id,artifact_id,manifest_id,evaluated_at,payload_sha256,payload_json "
                    "FROM alpaca_sip_quote_actionability_creation ORDER BY generation"
                ).fetchall()
            creations: list[AlpacaSipQuoteActionabilityCreation] = []
            for row in rows:
                creation = actionability_creation_from_bytes(row[5])
                if tuple(row) != _creation_row(creation, row[5]):
                    raise AlpacaSipQuoteActionabilityStoreError
                creations.append(creation)
            return tuple(creations)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipQuoteActionabilityStoreError from None


def _artifact_row(
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


def _insert_artifact(
    connection: sqlite3.Connection,
    artifact: AlpacaSipQuoteActionabilityArtifact,
    row: _ArtifactRow,
) -> bool:
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
        "(artifact_id,base_signal_id,scan_started_at,payload_sha256,payload_json) VALUES (?,?,?,?,?)",
        row,
    )
    return True


def _creation_row(
    creation: AlpacaSipQuoteActionabilityCreation,
    payload: bytes,
) -> _CreationRow:
    return (
        creation.creation_id,
        creation.artifact_id,
        creation.manifest_id,
        creation.evaluated_at.isoformat(),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )


def _existing_creation(connection: sqlite3.Connection, artifact_id: str) -> _CreationRow | None:
    return connection.execute(
        "SELECT creation_id,artifact_id,manifest_id,evaluated_at,payload_sha256,payload_json "
        "FROM alpaca_sip_quote_actionability_creation WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()


__all__ = (
    "AlpacaSipQuoteActionabilityStore",
    "AlpacaSipQuoteActionabilityStoreError",
)
