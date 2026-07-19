from __future__ import annotations

from typing import Final

CREATE_MULTI_MARKET_RESEARCH_SCHEMA_V4: Final = """
CREATE TABLE multi_market_hypotheses (
  registration_key TEXT PRIMARY KEY
    CHECK(length(registration_key) = 64 AND registration_key NOT GLOB '*[^0-9a-f]*'),
  hypothesis_id TEXT NOT NULL UNIQUE,
  experiment_scope_key TEXT NOT NULL
    CHECK(length(experiment_scope_key) = 64
      AND experiment_scope_key NOT GLOB '*[^0-9a-f]*'),
  primary_lane_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities', 'kr_equities')),
  agent_family TEXT NOT NULL
    CHECK(agent_family IN (
      'opportunity_manager', 'day_trading', 'swing_trading',
      'systematic_quant', 'market_context', 'allocation_manager'
    )),
  payload_json TEXT NOT NULL
);
CREATE TABLE multi_market_strategy_versions (
  registration_key TEXT PRIMARY KEY
    CHECK(length(registration_key) = 64 AND registration_key NOT GLOB '*[^0-9a-f]*'),
  strategy_version TEXT NOT NULL UNIQUE,
  strategy_id TEXT NOT NULL,
  hypothesis_id TEXT NOT NULL,
  experiment_scope_key TEXT NOT NULL
    CHECK(length(experiment_scope_key) = 64
      AND experiment_scope_key NOT GLOB '*[^0-9a-f]*'),
  strategy_lane_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities', 'kr_equities')),
  agent_family TEXT NOT NULL
    CHECK(agent_family IN (
      'opportunity_manager', 'day_trading', 'swing_trading',
      'systematic_quant', 'market_context', 'allocation_manager'
    )),
  operating_mode TEXT NOT NULL
    CHECK(operating_mode IN ('contract_only', 'shadow', 'alpaca_paper')),
  payload_json TEXT NOT NULL,
  FOREIGN KEY(hypothesis_id) REFERENCES multi_market_hypotheses(hypothesis_id)
);
CREATE INDEX multi_market_strategy_versions_by_lane
ON multi_market_strategy_versions(strategy_lane_id, strategy_version);
CREATE TRIGGER multi_market_hypotheses_no_update
BEFORE UPDATE ON multi_market_hypotheses BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER multi_market_hypotheses_no_delete
BEFORE DELETE ON multi_market_hypotheses BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER multi_market_strategy_versions_no_update
BEFORE UPDATE ON multi_market_strategy_versions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER multi_market_strategy_versions_no_delete
BEFORE DELETE ON multi_market_strategy_versions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CREATE_MULTI_MARKET_TRIAL_SCHEMA_V5: Final = """
CREATE TABLE multi_market_trials (
  registration_key TEXT PRIMARY KEY
    CHECK(length(registration_key) = 64 AND registration_key NOT GLOB '*[^0-9a-f]*'),
  trial_id TEXT NOT NULL UNIQUE,
  strategy_version TEXT NOT NULL,
  experiment_scope_key TEXT NOT NULL
    CHECK(length(experiment_scope_key) = 64
      AND experiment_scope_key NOT GLOB '*[^0-9a-f]*'),
  strategy_lane_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities', 'kr_equities')),
  agent_family TEXT NOT NULL
    CHECK(agent_family IN (
      'opportunity_manager', 'day_trading', 'swing_trading',
      'systematic_quant', 'market_context', 'allocation_manager'
    )),
  trial_kind TEXT NOT NULL CHECK(trial_kind = 'shadow_forward'),
  payload_json TEXT NOT NULL,
  FOREIGN KEY(strategy_version) REFERENCES multi_market_strategy_versions(strategy_version)
);
CREATE TABLE multi_market_trial_events (
  event_key TEXT PRIMARY KEY
    CHECK(length(event_key) = 64 AND event_key NOT GLOB '*[^0-9a-f]*'),
  trial_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence IN (1, 2)),
  event_kind TEXT NOT NULL
    CHECK(event_kind IN ('started', 'completed', 'failed', 'censored')),
  previous_event_key TEXT,
  payload_json TEXT NOT NULL,
  UNIQUE(trial_id, sequence),
  FOREIGN KEY(trial_id) REFERENCES multi_market_trials(trial_id),
  FOREIGN KEY(previous_event_key) REFERENCES multi_market_trial_events(event_key)
);
CREATE INDEX multi_market_trials_by_lane
ON multi_market_trials(strategy_lane_id, strategy_version, trial_id);
CREATE INDEX multi_market_trial_events_by_trial
ON multi_market_trial_events(trial_id, sequence);
CREATE TRIGGER multi_market_trials_no_update
BEFORE UPDATE ON multi_market_trials BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER multi_market_trials_no_delete
BEFORE DELETE ON multi_market_trials BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER multi_market_trial_events_no_update
BEFORE UPDATE ON multi_market_trial_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER multi_market_trial_events_no_delete
BEFORE DELETE ON multi_market_trial_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CREATE_MULTI_MARKET_LIFECYCLE_SCHEMA_V6: Final = """
CREATE TABLE multi_market_lifecycle_events (
  event_key TEXT PRIMARY KEY
    CHECK(length(event_key) = 64 AND event_key NOT GLOB '*[^0-9a-f]*'),
  strategy_version TEXT NOT NULL,
  strategy_lane_id TEXT NOT NULL,
  market_id TEXT NOT NULL CHECK(market_id IN ('us_equities', 'kr_equities')),
  agent_family TEXT NOT NULL
    CHECK(agent_family IN (
      'opportunity_manager', 'day_trading', 'swing_trading',
      'systematic_quant', 'market_context', 'allocation_manager'
    )),
  sequence INTEGER NOT NULL CHECK(sequence >= 1),
  event_kind TEXT NOT NULL CHECK(event_kind IN ('registration', 'transition')),
  effective_session_date TEXT NOT NULL,
  previous_event_key TEXT,
  payload_json TEXT NOT NULL,
  UNIQUE(strategy_version, sequence),
  FOREIGN KEY(strategy_version) REFERENCES multi_market_strategy_versions(strategy_version),
  FOREIGN KEY(previous_event_key) REFERENCES multi_market_lifecycle_events(event_key)
);
CREATE INDEX multi_market_lifecycle_by_version_date
ON multi_market_lifecycle_events(strategy_version, effective_session_date, sequence);
CREATE TRIGGER multi_market_lifecycle_events_no_update
BEFORE UPDATE ON multi_market_lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER multi_market_lifecycle_events_no_delete
BEFORE DELETE ON multi_market_lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
