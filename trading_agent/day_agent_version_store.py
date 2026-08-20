from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from types import TracebackType
from typing import Self, final

from trading_agent.dashboard_us_day_versions import DayAgentVersionView
from trading_agent.day_agent_version_models import (
    AgentChangeProposal,
    AgentDeploymentState,
    AgentPromotionRecommendation,
    AgentVersion,
    DayAgentVersionStoreError,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_versions (
  version_id TEXT PRIMARY KEY,
  deployment_state TEXT NOT NULL,
  parent_version_id TEXT,
  payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_initial_champion
  ON agent_versions(deployment_state) WHERE deployment_state = 'champion';
CREATE TABLE IF NOT EXISTS change_proposals (
  proposal_id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promotion_recommendations (
  recommendation_id TEXT PRIMARY KEY,
  challenger_version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
  payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS agent_versions_no_update BEFORE UPDATE ON agent_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS agent_versions_no_delete BEFORE DELETE ON agent_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_proposals_no_update BEFORE UPDATE ON change_proposals
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_proposals_no_delete BEFORE DELETE ON change_proposals
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS promotion_recommendations_no_update
BEFORE UPDATE ON promotion_recommendations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS promotion_recommendations_no_delete
BEFORE DELETE ON promotion_recommendations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


@final
class DayAgentVersionStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def writer(self) -> DayAgentVersionWriter:
        return DayAgentVersionWriter(self.path)

    def reader(self) -> DayAgentVersionReader:
        return DayAgentVersionReader(self.path)


@final
class DayAgentVersionWriter:
    __slots__ = ("_connection", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        _require_safe_parent(self._path.parent)
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        _require_safe_path(self._path, allow_missing=True)
        connection = sqlite3.connect(self._path)
        os.chmod(self._path, 0o600)
        _require_safe_path(self._path, allow_missing=False)
        _ = connection.execute("PRAGMA foreign_keys=ON")
        _ = connection.executescript(_SCHEMA)
        self._connection = connection
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def register_initial_champion(self, version: AgentVersion) -> bool:
        if version.deployment_state is not AgentDeploymentState.CHAMPION:
            raise DayAgentVersionStoreError("initial_champion_state_invalid")
        return self._insert_version(version)

    def register_challenger(self, version: AgentVersion) -> bool:
        if version.deployment_state is not AgentDeploymentState.SHADOW:
            raise DayAgentVersionStoreError("challenger_state_invalid")
        champion = self._require_connection().execute(
            "SELECT version_id FROM agent_versions WHERE deployment_state='champion'"
        ).fetchone()
        if champion is None or champion[0] != version.parent_version_id:
            raise DayAgentVersionStoreError("challenger_parent_invalid")
        return self._insert_version(version)

    def record_proposal(self, proposal: AgentChangeProposal) -> bool:
        return self._insert_artifact(
            "change_proposals",
            "proposal_id",
            proposal.proposal_id,
            proposal.version_id,
            canonical_experiment_ledger_json(proposal),
        )

    def record_recommendation(self, recommendation: AgentPromotionRecommendation) -> bool:
        return self._insert_artifact(
            "promotion_recommendations",
            "recommendation_id",
            recommendation.recommendation_id,
            recommendation.challenger_version_id,
            canonical_experiment_ledger_json(recommendation),
        )

    def _insert_version(self, version: AgentVersion) -> bool:
        connection = self._require_connection()
        payload = canonical_experiment_ledger_json(version)
        _ = connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT payload_json FROM agent_versions WHERE version_id=?", (version.version_id,)
        ).fetchone()
        if existing is not None:
            connection.rollback()
            if existing[0] == payload:
                return False
            raise DayAgentVersionStoreError("agent_version_replay_conflict")
        try:
            _ = connection.execute(
                "INSERT INTO agent_versions VALUES (?,?,?,?)",
                (version.version_id, version.deployment_state.value, version.parent_version_id, payload),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise DayAgentVersionStoreError("agent_version_registration_invalid") from error
        return True

    def _insert_artifact(
        self,
        table: str,
        identity_column: str,
        identity: str,
        challenger_id: str,
        payload: str,
    ) -> bool:
        connection = self._require_connection()
        _ = connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            f"SELECT payload_json FROM {table} WHERE {identity_column}=?", (identity,)
        ).fetchone()
        if existing is not None:
            connection.rollback()
            if existing[0] == payload:
                return False
            raise DayAgentVersionStoreError("agent_version_artifact_conflict")
        _ = connection.execute(
            f"INSERT INTO {table} VALUES (?,?,?)", (identity, challenger_id, payload)
        )
        connection.commit()
        return True

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DayAgentVersionStoreError("version_writer_closed")
        return self._connection


@final
class DayAgentVersionReader:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def champion(self) -> AgentVersion | None:
        versions = self._versions_by_state(AgentDeploymentState.CHAMPION)
        return versions[0] if versions else None

    def challenger(self, version_id: str) -> AgentVersion | None:
        version = self._version(version_id)
        return version if version is not None and version.deployment_state is AgentDeploymentState.SHADOW else None

    def challengers(self) -> tuple[AgentVersion, ...]:
        return self._versions_by_state(AgentDeploymentState.SHADOW)

    def versions(self) -> tuple[DayAgentVersionView, ...]:
        return tuple(
            DayAgentVersionView(
                version_id=item.version_id,
                deployment_state=item.deployment_state.value,
                task_id=item.task_id,
                observed_at=item.created_at,
            )
            for item in (*self._versions_by_state(AgentDeploymentState.CHAMPION), *self.challengers())
        )

    def recommendations(self, challenger_id: str) -> tuple[AgentPromotionRecommendation, ...]:
        rows = self._rows(
            "SELECT payload_json FROM promotion_recommendations WHERE challenger_version_id=? ORDER BY rowid",
            (challenger_id,),
        )
        return tuple(AgentPromotionRecommendation.model_validate_json(row[0]) for row in rows)

    def proposals(self, challenger_id: str) -> tuple[AgentChangeProposal, ...]:
        rows = self._rows(
            "SELECT payload_json FROM change_proposals WHERE version_id=? ORDER BY rowid",
            (challenger_id,),
        )
        return tuple(AgentChangeProposal.model_validate_json(row[0]) for row in rows)

    def _version(self, version_id: str) -> AgentVersion | None:
        rows = self._rows("SELECT payload_json FROM agent_versions WHERE version_id=?", (version_id,))
        return None if not rows else AgentVersion.model_validate_json(rows[0][0])

    def _versions_by_state(self, state: AgentDeploymentState) -> tuple[AgentVersion, ...]:
        rows = self._rows(
            "SELECT payload_json FROM agent_versions WHERE deployment_state=? ORDER BY rowid",
            (state.value,),
        )
        return tuple(AgentVersion.model_validate_json(row[0]) for row in rows)

    def _rows(self, query: str, parameters: tuple[str, ...]) -> tuple[tuple[str], ...]:
        if not self._path.exists():
            return ()
        _require_safe_path(self._path, allow_missing=False)
        with closing(sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)) as connection:
            _ = connection.execute("PRAGMA query_only=ON")
            return tuple(connection.execute(query, parameters).fetchall())


def _require_safe_path(path: Path, *, allow_missing: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise DayAgentVersionStoreError("version_store_missing") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_mode & 0o077:
        raise DayAgentVersionStoreError("version_store_metadata_invalid")


def _require_safe_parent(path: Path) -> None:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    metadata = candidate.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise DayAgentVersionStoreError("version_store_metadata_invalid")


__all__ = (
    "DayAgentVersionReader",
    "DayAgentVersionStore",
    "DayAgentVersionStoreError",
    "DayAgentVersionWriter",
)
