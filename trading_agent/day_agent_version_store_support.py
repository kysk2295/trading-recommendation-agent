from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

from trading_agent.day_agent_version_models import DayAgentVersionStoreError

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS deployment_transitions (
  transition_id TEXT PRIMARY KEY,
  recommendation_id TEXT NOT NULL UNIQUE REFERENCES promotion_recommendations(recommendation_id),
  demoted_version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
  promoted_version_id TEXT NOT NULL REFERENCES agent_versions(version_id),
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
CREATE TRIGGER IF NOT EXISTS deployment_transitions_no_update
BEFORE UPDATE ON deployment_transitions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_transitions_no_delete
BEFORE DELETE ON deployment_transitions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


def require_safe_path(path: Path, *, allow_missing: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise DayAgentVersionStoreError("version_store_missing") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_mode & 0o077:
        raise DayAgentVersionStoreError("version_store_metadata_invalid")


def require_safe_parent(path: Path) -> None:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    metadata = candidate.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise DayAgentVersionStoreError("version_store_metadata_invalid")


def current_champion_id(connection: sqlite3.Connection) -> str | None:
    latest = connection.execute(
        "SELECT promoted_version_id FROM deployment_transitions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if latest is not None:
        return latest[0]
    initial = connection.execute("SELECT version_id FROM agent_versions WHERE deployment_state='champion'").fetchone()
    return None if initial is None else initial[0]


__all__ = ("SCHEMA", "current_champion_id", "require_safe_parent", "require_safe_path")
