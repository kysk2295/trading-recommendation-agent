from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self, final

from trading_agent.day_agent_version_models import (
    AgentChangeProposal,
    AgentDeploymentState,
    AgentDeploymentTransition,
    AgentPromotionRecommendation,
    AgentVersion,
    DayAgentVersionStoreError,
)
from trading_agent.day_agent_version_reader import DayAgentVersionReader
from trading_agent.day_agent_version_store_support import (
    SCHEMA,
    current_champion_id,
    require_safe_parent,
    require_safe_path,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


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
        require_safe_parent(self._path.parent)
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        require_safe_path(self._path, allow_missing=True)
        connection = sqlite3.connect(self._path)
        os.chmod(self._path, 0o600)
        require_safe_path(self._path, allow_missing=False)
        _ = connection.execute("PRAGMA foreign_keys=ON")
        _ = connection.executescript(SCHEMA)
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
        champion_id = current_champion_id(self._require_connection())
        if champion_id is None or champion_id != version.parent_version_id:
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

    def _record_controller_recommendation(
        self,
        recommendation: AgentPromotionRecommendation,
    ) -> bool:
        return self._insert_artifact(
            "promotion_recommendations",
            "recommendation_id",
            recommendation.recommendation_id,
            recommendation.challenger_version_id,
            canonical_experiment_ledger_json(recommendation),
        )

    def _apply_promotion(
        self,
        recommendation: AgentPromotionRecommendation,
        transition: AgentDeploymentTransition,
    ) -> bool:
        connection = self._require_connection()
        _ = connection.execute("BEGIN IMMEDIATE")
        stored = connection.execute(
            "SELECT payload_json FROM promotion_recommendations WHERE recommendation_id=?",
            (recommendation.recommendation_id,),
        ).fetchone()
        current = current_champion_id(connection)
        challenger = connection.execute(
            "SELECT parent_version_id,deployment_state FROM agent_versions WHERE version_id=?",
            (recommendation.challenger_version_id,),
        ).fetchone()
        if (
            stored is None
            or stored[0] != canonical_experiment_ledger_json(recommendation)
            or current != recommendation.champion_version_id
            or challenger != (current, AgentDeploymentState.SHADOW.value)
            or transition.recommendation_id != recommendation.recommendation_id
            or transition.demoted_version_id != current
            or transition.promoted_version_id != recommendation.challenger_version_id
        ):
            connection.rollback()
            raise DayAgentVersionStoreError("deployment_recommendation_invalid")
        payload = canonical_experiment_ledger_json(transition)
        existing = connection.execute(
            "SELECT payload_json FROM deployment_transitions WHERE transition_id=?",
            (transition.transition_id,),
        ).fetchone()
        if existing is not None:
            connection.rollback()
            if existing[0] == payload:
                return False
            raise DayAgentVersionStoreError("deployment_transition_conflict")
        _ = connection.execute(
            "INSERT INTO deployment_transitions VALUES (?,?,?,?,?)",
            (
                transition.transition_id,
                transition.recommendation_id,
                transition.demoted_version_id,
                transition.promoted_version_id,
                payload,
            ),
        )
        connection.commit()
        return True

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
        _ = connection.execute(f"INSERT INTO {table} VALUES (?,?,?)", (identity, challenger_id, payload))
        connection.commit()
        return True

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DayAgentVersionStoreError("version_writer_closed")
        return self._connection


__all__ = (
    "DayAgentVersionReader",
    "DayAgentVersionStore",
    "DayAgentVersionStoreError",
    "DayAgentVersionWriter",
)
