from __future__ import annotations

from typing import Final

_TABLES: Final = (
    "day_hypothesis_families",
    "day_hypothesis_versions",
    "day_research_attempt_bindings",
    "day_strategy_capsules",
    "day_forward_trials",
    "day_forward_trial_events",
    "day_promotion_decisions",
    "day_execution_eligibility_events",
    "day_exploration_policies",
)

_INDEXES: Final = (
    "day_hypothesis_versions_by_family_market",
    "day_attempt_bindings_by_version_market",
    "day_capsules_by_version_market",
    "day_forward_trials_by_capsule_version_market_session",
    "day_forward_trial_events_by_trial_market_session_sequence",
    "day_promotion_decisions_by_capsule_market_session",
    "day_execution_eligibility_by_capsule_market_session",
    "day_exploration_policies_by_market_session",
)

CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10: Final = """
CREATE TABLE day_hypothesis_families (
  family_key TEXT PRIMARY KEY CHECK(length(family_key)=64 AND family_key NOT GLOB '*[^0-9a-f]*'),
  family_id TEXT NOT NULL UNIQUE CHECK(length(family_id)=64 AND family_id NOT GLOB '*[^0-9a-f]*'),
  parent_family_id TEXT,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(parent_family_id) REFERENCES day_hypothesis_families(family_id)
);
CREATE TABLE day_hypothesis_versions (
  version_key TEXT PRIMARY KEY CHECK(length(version_key)=64 AND version_key NOT GLOB '*[^0-9a-f]*'),
  hypothesis_version_id TEXT NOT NULL UNIQUE
    CHECK(length(hypothesis_version_id)=64 AND hypothesis_version_id NOT GLOB '*[^0-9a-f]*'),
  family_id TEXT NOT NULL,
  parent_version_id TEXT,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  created_at TEXT NOT NULL,
  registration_completed_bar_at TEXT NOT NULL,
  first_shadow_eligible_at TEXT NOT NULL
    CHECK(first_shadow_eligible_at > registration_completed_bar_at),
  payload_json TEXT NOT NULL,
  FOREIGN KEY(family_id) REFERENCES day_hypothesis_families(family_id),
  FOREIGN KEY(parent_version_id) REFERENCES day_hypothesis_versions(hypothesis_version_id)
);
CREATE INDEX day_hypothesis_versions_by_family_market
ON day_hypothesis_versions(family_id,market_id,created_at,first_shadow_eligible_at);

CREATE TABLE day_research_attempt_bindings (
  binding_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE,
  hypothesis_version_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  artifact_ref TEXT NOT NULL, multiple_testing_family TEXT NOT NULL,
  search_budget_debit INTEGER NOT NULL CHECK(search_budget_debit>=1),
  bound_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(hypothesis_version_id,market_id,attempt_id),
  FOREIGN KEY(hypothesis_version_id) REFERENCES day_hypothesis_versions(hypothesis_version_id)
);
CREATE INDEX day_attempt_bindings_by_version_market
ON day_research_attempt_bindings(hypothesis_version_id,market_id,bound_at);

CREATE TABLE day_strategy_capsules (
  capsule_id TEXT PRIMARY KEY, hypothesis_version_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  created_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  FOREIGN KEY(hypothesis_version_id) REFERENCES day_hypothesis_versions(hypothesis_version_id)
);
CREATE INDEX day_capsules_by_version_market
ON day_strategy_capsules(hypothesis_version_id,market_id,created_at);

CREATE TABLE day_forward_trials (
  trial_id TEXT PRIMARY KEY, capsule_id TEXT NOT NULL, hypothesis_version_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  session_date TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  FOREIGN KEY(capsule_id) REFERENCES day_strategy_capsules(capsule_id),
  FOREIGN KEY(hypothesis_version_id) REFERENCES day_hypothesis_versions(hypothesis_version_id)
);
CREATE INDEX day_forward_trials_by_capsule_version_market_session
ON day_forward_trials(capsule_id,hypothesis_version_id,market_id,session_date);

CREATE TABLE day_forward_trial_events (
  event_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  session_date TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=1),
  previous_event_id TEXT, event_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(trial_id,sequence),
  FOREIGN KEY(trial_id) REFERENCES day_forward_trials(trial_id),
  FOREIGN KEY(previous_event_id) REFERENCES day_forward_trial_events(event_id)
);
CREATE INDEX day_forward_trial_events_by_trial_market_session_sequence
ON day_forward_trial_events(trial_id,market_id,session_date,sequence);

CREATE TABLE day_promotion_decisions (
  decision_id TEXT PRIMARY KEY, capsule_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  effective_session_date TEXT NOT NULL, decided_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  FOREIGN KEY(capsule_id) REFERENCES day_strategy_capsules(capsule_id)
);
CREATE INDEX day_promotion_decisions_by_capsule_market_session
ON day_promotion_decisions(capsule_id,market_id,effective_session_date);

CREATE TABLE day_execution_eligibility_events (
  eligibility_event_id TEXT PRIMARY KEY, capsule_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  session_date TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=1),
  effective_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(capsule_id,market_id,sequence),
  FOREIGN KEY(capsule_id) REFERENCES day_strategy_capsules(capsule_id)
);
CREATE INDEX day_execution_eligibility_by_capsule_market_session
ON day_execution_eligibility_events(capsule_id,market_id,session_date,sequence);

CREATE TABLE day_exploration_policies (
  policy_id TEXT PRIMARY KEY,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities','kr_equities')),
  effective_session_date TEXT NOT NULL, effective_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(market_id,effective_session_date,policy_id)
);
CREATE INDEX day_exploration_policies_by_market_session
ON day_exploration_policies(market_id,effective_session_date);
""" + "\n".join(
    f"CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END;"
    for table in _TABLES
    for action in ("update", "delete")
)

DAY_RESEARCH_SCHEMA_OBJECTS: Final = frozenset(
    set(_TABLES) | set(_INDEXES) | {f"{table}_no_{action}" for table in _TABLES for action in ("update", "delete")}
)

__all__ = ("CREATE_DAY_RESEARCH_LEDGER_SCHEMA_V10", "DAY_RESEARCH_SCHEMA_OBJECTS")
