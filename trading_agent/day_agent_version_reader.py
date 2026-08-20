from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal, final

from trading_agent.dashboard_us_day_versions import DayAgentVersionView
from trading_agent.day_agent_version_models import (
    AgentChangeProposal,
    AgentDeploymentState,
    AgentDeploymentTransition,
    AgentPromotionRecommendation,
    AgentVersion,
)
from trading_agent.day_agent_version_store_support import require_safe_path


@final
class DayAgentVersionReader:
    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def champion(self) -> AgentVersion | None:
        states = self._effective_states()
        matches = tuple(item for item in self._all_versions() if states[item.version_id] == "champion")
        return matches[0] if matches else None

    def challenger(self, version_id: str) -> AgentVersion | None:
        version = self._version(version_id)
        states = self._effective_states()
        return version if version is not None and states.get(version_id) == "shadow" else None

    def challengers(self) -> tuple[AgentVersion, ...]:
        states = self._effective_states()
        return tuple(item for item in self._all_versions() if states[item.version_id] == "shadow")

    def versions(self) -> tuple[DayAgentVersionView, ...]:
        states = self._effective_states()
        return tuple(
            DayAgentVersionView(
                version_id=item.version_id,
                deployment_state=states[item.version_id],
                task_id=item.task_id,
                observed_at=item.created_at,
            )
            for item in self._all_versions()
        )

    def transitions(self) -> tuple[AgentDeploymentTransition, ...]:
        rows = self._rows("SELECT payload_json FROM deployment_transitions ORDER BY rowid", ())
        return tuple(AgentDeploymentTransition.model_validate_json(row[0]) for row in rows)

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

    def _all_versions(self) -> tuple[AgentVersion, ...]:
        rows = self._rows("SELECT payload_json FROM agent_versions ORDER BY rowid", ())
        return tuple(AgentVersion.model_validate_json(row[0]) for row in rows)

    def _effective_states(self) -> dict[str, Literal["champion", "shadow"]]:
        states: dict[str, Literal["champion", "shadow"]] = {
            item.version_id: ("champion" if item.deployment_state is AgentDeploymentState.CHAMPION else "shadow")
            for item in self._all_versions()
        }
        for transition in self.transitions():
            states[transition.demoted_version_id] = "shadow"
            states[transition.promoted_version_id] = "champion"
        return states

    def _rows(self, query: str, parameters: tuple[str, ...]) -> tuple[tuple[str], ...]:
        if not self._path.exists():
            return ()
        require_safe_path(self._path, allow_missing=False)
        with closing(sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)) as connection:
            _ = connection.execute("PRAGMA query_only=ON")
            return tuple(connection.execute(query, parameters).fetchall())


__all__ = ("DayAgentVersionReader",)
