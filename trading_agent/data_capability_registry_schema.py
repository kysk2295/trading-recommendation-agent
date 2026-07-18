from __future__ import annotations

from typing import Final

DATA_CAPABILITY_REGISTRY_SCHEMA_VERSION: Final = 1

CREATE_DATA_CAPABILITY_REGISTRY_SCHEMA: Final = """
CREATE TABLE entitlements (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  entitlement_id TEXT NOT NULL UNIQUE,
  source_id TEXT NOT NULL,
  effective_from_utc TEXT NOT NULL,
  effective_to_utc TEXT,
  payload_sha256 TEXT NOT NULL,
  payload_json BLOB NOT NULL
);
CREATE TABLE capability_assessments (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  assessment_id TEXT NOT NULL UNIQUE,
  source_id TEXT NOT NULL,
  assessed_at_utc TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json BLOB NOT NULL,
  UNIQUE(source_id, assessed_at_utc)
);
CREATE INDEX entitlements_source_idx ON entitlements(source_id);
CREATE INDEX capability_assessments_source_time_idx
ON capability_assessments(source_id, assessed_at_utc);
CREATE TRIGGER entitlements_no_update BEFORE UPDATE ON entitlements
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER entitlements_no_delete BEFORE DELETE ON entitlements
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER capability_assessments_no_update BEFORE UPDATE ON capability_assessments
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER capability_assessments_no_delete BEFORE DELETE ON capability_assessments
BEGIN SELECT RAISE(ABORT, 'append only'); END;
"""

__all__ = (
    "CREATE_DATA_CAPABILITY_REGISTRY_SCHEMA",
    "DATA_CAPABILITY_REGISTRY_SCHEMA_VERSION",
)
