from __future__ import annotations

from typing import Final

LANE_REGISTRY_SCHEMA_VERSION: Final = 1

CREATE_LANE_REGISTRY_SCHEMA: Final = """
CREATE TABLE lane_manifests (
  manifest_key TEXT PRIMARY KEY
    CHECK(length(manifest_key) = 64 AND manifest_key NOT GLOB '*[^0-9a-f]*'),
  lane_id TEXT NOT NULL
    CHECK(lane_id IN ('intraday_momentum', 'swing_momentum', 'market_regime')),
  manifest_version TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(lane_id, manifest_version)
);
CREATE TABLE lane_account_bindings (
  binding_key TEXT PRIMARY KEY
    CHECK(length(binding_key) = 64 AND binding_key NOT GLOB '*[^0-9a-f]*'),
  lane_id TEXT NOT NULL UNIQUE
    CHECK(lane_id IN ('intraday_momentum', 'swing_momentum', 'market_regime')),
  account_fingerprint TEXT NOT NULL UNIQUE
    CHECK(length(account_fingerprint) = 64 AND account_fingerprint NOT GLOB '*[^0-9a-f]*'),
  execution_ledger_fingerprint TEXT NOT NULL UNIQUE
    CHECK(length(execution_ledger_fingerprint) = 64
      AND execution_ledger_fingerprint NOT GLOB '*[^0-9a-f]*'),
  payload_json TEXT NOT NULL
);
CREATE TABLE experiment_scopes (
  scope_key TEXT PRIMARY KEY
    CHECK(length(scope_key) = 64 AND scope_key NOT GLOB '*[^0-9a-f]*'),
  hypothesis_id TEXT NOT NULL UNIQUE,
  primary_lane TEXT NOT NULL
    CHECK(primary_lane IN ('intraday_momentum', 'swing_momentum', 'market_regime')),
  payload_json TEXT NOT NULL
);
CREATE TABLE lane_daily_snapshots (
  snapshot_key TEXT PRIMARY KEY
    CHECK(length(snapshot_key) = 64 AND snapshot_key NOT GLOB '*[^0-9a-f]*'),
  lane_id TEXT NOT NULL
    CHECK(lane_id IN ('intraday_momentum', 'swing_momentum', 'market_regime')),
  session_date TEXT NOT NULL,
  manifest_key TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(lane_id, session_date),
  FOREIGN KEY(manifest_key) REFERENCES lane_manifests(manifest_key)
);

CREATE TRIGGER lane_manifests_no_update
BEFORE UPDATE ON lane_manifests BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER lane_manifests_no_delete
BEFORE DELETE ON lane_manifests BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER lane_account_bindings_no_update
BEFORE UPDATE ON lane_account_bindings BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER lane_account_bindings_no_delete
BEFORE DELETE ON lane_account_bindings BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER experiment_scopes_no_update
BEFORE UPDATE ON experiment_scopes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER experiment_scopes_no_delete
BEFORE DELETE ON experiment_scopes BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER lane_daily_snapshots_no_update
BEFORE UPDATE ON lane_daily_snapshots BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER lane_daily_snapshots_no_delete
BEFORE DELETE ON lane_daily_snapshots BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
