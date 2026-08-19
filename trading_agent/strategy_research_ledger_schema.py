from __future__ import annotations

_APPEND_ONLY_TABLES = (
    "strategy_research_preregistrations",
    "strategy_research_holdout_seals",
    "strategy_research_attempts",
    "strategy_research_agent_state_events",
    "strategy_research_holdout_reveals",
)

CREATE_STRATEGY_RESEARCH_LEDGER_SCHEMA_V9 = """
CREATE TABLE strategy_research_preregistrations (
  registration_key TEXT PRIMARY KEY CHECK(length(registration_key)=64), hypothesis_id TEXT NOT NULL UNIQUE,
  parent_hypothesis_id TEXT, search_family_id TEXT NOT NULL, agent_id TEXT NOT NULL,
  protocol_version TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(hypothesis_id,search_family_id)
);
CREATE TABLE strategy_research_holdout_seals (
  seal_id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL UNIQUE,
  commitment_sha256 TEXT NOT NULL CHECK(length(commitment_sha256)=64), payload_json TEXT NOT NULL,
  UNIQUE(hypothesis_id,seal_id),
  FOREIGN KEY(hypothesis_id) REFERENCES strategy_research_preregistrations(hypothesis_id)
);
CREATE TABLE strategy_research_attempts (
  attempt_key TEXT PRIMARY KEY CHECK(length(attempt_key)=64), attempt_id TEXT NOT NULL UNIQUE,
  hypothesis_id TEXT NOT NULL, branch_index INTEGER NOT NULL CHECK(branch_index>=0),
  status TEXT NOT NULL CHECK(status IN ('started','succeeded','failed','aborted','timed_out','cancelled','censored')),
  payload_json TEXT NOT NULL, UNIQUE(hypothesis_id,branch_index),
  FOREIGN KEY(hypothesis_id) REFERENCES strategy_research_preregistrations(hypothesis_id)
);
CREATE TABLE strategy_research_agent_state_events (
  event_key TEXT PRIMARY KEY CHECK(length(event_key)=64), event_id TEXT NOT NULL UNIQUE,
  agent_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=1), payload_json TEXT NOT NULL,
  UNIQUE(agent_id,sequence)
);
CREATE TABLE strategy_research_holdout_reveals (
  reveal_key TEXT PRIMARY KEY CHECK(length(reveal_key)=64), reveal_id TEXT NOT NULL UNIQUE,
  hypothesis_id TEXT NOT NULL UNIQUE, search_family_id TEXT NOT NULL UNIQUE, seal_id TEXT NOT NULL UNIQUE,
  owner_agent_id TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('supported','refuted','inconclusive')),
  sanitized_payload_json TEXT NOT NULL, exact_payload_json TEXT NOT NULL,
  FOREIGN KEY(hypothesis_id) REFERENCES strategy_research_preregistrations(hypothesis_id),
  FOREIGN KEY(seal_id) REFERENCES strategy_research_holdout_seals(seal_id),
  FOREIGN KEY(hypothesis_id,seal_id) REFERENCES strategy_research_holdout_seals(hypothesis_id,seal_id)
);
CREATE INDEX strategy_research_attempts_by_hypothesis ON strategy_research_attempts(hypothesis_id,branch_index);
CREATE INDEX strategy_research_agent_state_by_agent ON strategy_research_agent_state_events(agent_id,sequence);
CREATE TRIGGER strategy_research_preregistrations_parent_lineage
BEFORE INSERT ON strategy_research_preregistrations
WHEN NEW.parent_hypothesis_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM strategy_research_preregistrations parent
  WHERE parent.hypothesis_id=NEW.parent_hypothesis_id AND parent.search_family_id=NEW.search_family_id
) BEGIN SELECT RAISE(ABORT, 'lineage-parent-mismatch'); END;
""" + "\n".join(
    f"CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    for table in _APPEND_ONLY_TABLES
    for action in ("update", "delete")
)

STRATEGY_RESEARCH_SCHEMA_OBJECTS = frozenset(
    set(_APPEND_ONLY_TABLES)
    | {
        "strategy_research_attempts_by_hypothesis",
        "strategy_research_agent_state_by_agent",
        "strategy_research_preregistrations_parent_lineage",
    }
    | {f"{table}_no_{action}" for table in _APPEND_ONLY_TABLES for action in ("update", "delete")}
)

__all__ = ("CREATE_STRATEGY_RESEARCH_LEDGER_SCHEMA_V9", "STRATEGY_RESEARCH_SCHEMA_OBJECTS")
