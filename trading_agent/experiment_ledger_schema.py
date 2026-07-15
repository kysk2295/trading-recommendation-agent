from __future__ import annotations

from typing import Final

EXPERIMENT_LEDGER_SCHEMA_VERSION: Final = 1

CREATE_EXPERIMENT_LEDGER_SCHEMA: Final = """
CREATE TABLE hypotheses (
  registration_key TEXT PRIMARY KEY
    CHECK(length(registration_key) = 64 AND registration_key NOT GLOB '*[^0-9a-f]*'),
  hypothesis_id TEXT NOT NULL UNIQUE,
  experiment_scope_key TEXT NOT NULL
    CHECK(length(experiment_scope_key) = 64
      AND experiment_scope_key NOT GLOB '*[^0-9a-f]*'),
  lane_id TEXT NOT NULL
    CHECK(lane_id IN ('intraday_momentum', 'swing_momentum', 'market_regime')),
  payload_json TEXT NOT NULL
);
CREATE TABLE strategy_versions (
  registration_key TEXT PRIMARY KEY
    CHECK(length(registration_key) = 64 AND registration_key NOT GLOB '*[^0-9a-f]*'),
  strategy_version TEXT NOT NULL UNIQUE,
  strategy_id TEXT NOT NULL,
  hypothesis_id TEXT NOT NULL,
  experiment_scope_key TEXT NOT NULL
    CHECK(length(experiment_scope_key) = 64
      AND experiment_scope_key NOT GLOB '*[^0-9a-f]*'),
  lane_id TEXT NOT NULL
    CHECK(lane_id IN ('intraday_momentum', 'swing_momentum', 'market_regime')),
  payload_json TEXT NOT NULL,
  FOREIGN KEY(hypothesis_id) REFERENCES hypotheses(hypothesis_id)
);
CREATE INDEX strategy_versions_by_lane
ON strategy_versions(lane_id, strategy_id);

CREATE TABLE experiment_trials (
  registration_key TEXT PRIMARY KEY
    CHECK(length(registration_key) = 64 AND registration_key NOT GLOB '*[^0-9a-f]*'),
  trial_id TEXT NOT NULL UNIQUE,
  strategy_version TEXT NOT NULL,
  experiment_scope_key TEXT NOT NULL
    CHECK(length(experiment_scope_key) = 64
      AND experiment_scope_key NOT GLOB '*[^0-9a-f]*'),
  trial_kind TEXT NOT NULL
    CHECK(trial_kind IN (
      'historical_replay', 'shadow_forward', 'broker_paper_forward',
      'equal_risk_comparison', 'cross_lane_hypothesis'
    )),
  payload_json TEXT NOT NULL,
  FOREIGN KEY(strategy_version) REFERENCES strategy_versions(strategy_version)
);

CREATE TABLE experiment_trial_events (
  event_key TEXT PRIMARY KEY
    CHECK(length(event_key) = 64 AND event_key NOT GLOB '*[^0-9a-f]*'),
  trial_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence >= 1),
  event_kind TEXT NOT NULL
    CHECK(event_kind IN ('started', 'completed', 'failed', 'censored')),
  previous_event_key TEXT,
  payload_json TEXT NOT NULL,
  UNIQUE(trial_id, sequence),
  FOREIGN KEY(trial_id) REFERENCES experiment_trials(trial_id),
  FOREIGN KEY(previous_event_key) REFERENCES experiment_trial_events(event_key)
);
CREATE INDEX experiment_trial_events_by_trial
ON experiment_trial_events(trial_id, sequence);

CREATE TABLE strategy_lifecycle_events (
  event_key TEXT PRIMARY KEY
    CHECK(length(event_key) = 64 AND event_key NOT GLOB '*[^0-9a-f]*'),
  strategy_version TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence >= 1),
  event_kind TEXT NOT NULL CHECK(event_kind IN ('registration', 'transition')),
  effective_session_date TEXT NOT NULL,
  previous_event_key TEXT,
  payload_json TEXT NOT NULL,
  UNIQUE(strategy_version, sequence),
  FOREIGN KEY(strategy_version) REFERENCES strategy_versions(strategy_version),
  FOREIGN KEY(previous_event_key) REFERENCES strategy_lifecycle_events(event_key)
);
CREATE INDEX strategy_lifecycle_events_by_version_date
ON strategy_lifecycle_events(strategy_version, effective_session_date, sequence);

CREATE TRIGGER hypotheses_no_update
BEFORE UPDATE ON hypotheses BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hypotheses_no_delete
BEFORE DELETE ON hypotheses BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER strategy_versions_no_update
BEFORE UPDATE ON strategy_versions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER strategy_versions_no_delete
BEFORE DELETE ON strategy_versions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER experiment_trials_no_update
BEFORE UPDATE ON experiment_trials BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER experiment_trials_no_delete
BEFORE DELETE ON experiment_trials BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER experiment_trial_events_no_update
BEFORE UPDATE ON experiment_trial_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER experiment_trial_events_no_delete
BEFORE DELETE ON experiment_trial_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER strategy_lifecycle_events_no_update
BEFORE UPDATE ON strategy_lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER strategy_lifecycle_events_no_delete
BEFORE DELETE ON strategy_lifecycle_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
