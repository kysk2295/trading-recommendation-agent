from __future__ import annotations

from typing import Final

LANE_REVIEW_SCHEMA_VERSION: Final = 1

CREATE_LANE_REVIEW_SCHEMA: Final = """
CREATE TABLE lane_review_events (
  event_key TEXT PRIMARY KEY
    CHECK(length(event_key) = 64 AND event_key NOT GLOB '*[^0-9a-f]*'),
  lane_id TEXT NOT NULL
    CHECK(lane_id IN ('intraday_momentum', 'swing_momentum', 'market_regime')),
  session_date TEXT NOT NULL,
  snapshot_key TEXT NOT NULL
    CHECK(length(snapshot_key) = 64 AND snapshot_key NOT GLOB '*[^0-9a-f]*'),
  experiment_scope_key TEXT NOT NULL
    CHECK(length(experiment_scope_key) = 64
      AND experiment_scope_key NOT GLOB '*[^0-9a-f]*'),
  reviewer_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(snapshot_key, experiment_scope_key, reviewer_version)
);
CREATE INDEX lane_review_events_by_lane_date
ON lane_review_events(lane_id, session_date);

CREATE TRIGGER lane_review_events_no_update
BEFORE UPDATE ON lane_review_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER lane_review_events_no_delete
BEFORE DELETE ON lane_review_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
