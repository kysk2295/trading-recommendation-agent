from __future__ import annotations

from typing import Final

KR_THEME_SCHEMA_VERSION: Final = 2

CREATE_KR_THEME_SCHEMA_V1: Final = """
CREATE TABLE kr_catalysts (
  catalyst_id TEXT PRIMARY KEY
    CHECK(length(catalyst_id) = 64 AND catalyst_id NOT GLOB '*[^0-9a-f]*'),
  source TEXT NOT NULL
    CHECK(source IN ('news', 'dart', 'kis_ranking', 'volume_surge')),
  source_record_id TEXT NOT NULL,
  publisher_id TEXT,
  published_at TEXT,
  first_observed_at TEXT NOT NULL,
  content_type TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL
    CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  payload_blob BLOB NOT NULL
    CHECK(typeof(payload_blob) = 'blob' AND length(payload_blob) > 0),
  UNIQUE(source, source_record_id)
);

CREATE TABLE kr_catalyst_observations (
  collection_cycle_id TEXT NOT NULL,
  catalyst_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  PRIMARY KEY(collection_cycle_id, catalyst_id),
  FOREIGN KEY(catalyst_id) REFERENCES kr_catalysts(catalyst_id)
);

CREATE TABLE kr_collection_cycles (
  collection_cycle_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
  payload_json TEXT NOT NULL
);

CREATE TABLE kr_theme_classifications (
  classification_id TEXT PRIMARY KEY
    CHECK(length(classification_id) = 64
      AND classification_id NOT GLOB '*[^0-9a-f]*'),
  catalyst_id TEXT NOT NULL,
  classifier_kind TEXT NOT NULL CHECK(classifier_kind IN ('llm', 'keyword')),
  classifier_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  classification_run_id TEXT NOT NULL,
  classified_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(catalyst_id) REFERENCES kr_catalysts(catalyst_id),
  UNIQUE(
    catalyst_id,
    classifier_kind,
    classifier_version,
    prompt_version,
    classification_run_id
  )
);

CREATE TRIGGER kr_catalysts_no_update
BEFORE UPDATE ON kr_catalysts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_catalysts_no_delete
BEFORE DELETE ON kr_catalysts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_catalyst_observations_no_update
BEFORE UPDATE ON kr_catalyst_observations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_catalyst_observations_no_delete
BEFORE DELETE ON kr_catalyst_observations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_collection_cycles_no_update
BEFORE UPDATE ON kr_collection_cycles BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_collection_cycles_no_delete
BEFORE DELETE ON kr_collection_cycles BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_classifications_no_update
BEFORE UPDATE ON kr_theme_classifications BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_theme_classifications_no_delete
BEFORE DELETE ON kr_theme_classifications BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CREATE_KR_THEME_SCHEMA_V2_ADDITIONS: Final = """
CREATE TABLE kr_source_receipts (
  receipt_id TEXT PRIMARY KEY
    CHECK(length(receipt_id) = 64 AND receipt_id NOT GLOB '*[^0-9a-f]*'),
  source_run_id TEXT NOT NULL,
  source TEXT NOT NULL
    CHECK(source IN ('news', 'dart', 'kis_ranking', 'volume_surge')),
  request_key TEXT NOT NULL,
  received_at TEXT NOT NULL,
  http_status INTEGER NOT NULL CHECK(http_status BETWEEN 100 AND 599),
  content_type TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL
    CHECK(length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  payload_blob BLOB NOT NULL
    CHECK(typeof(payload_blob) = 'blob' AND length(payload_blob) > 0),
  UNIQUE(source_run_id, request_key)
);

CREATE TABLE kr_catalyst_observation_receipts (
  collection_cycle_id TEXT NOT NULL,
  catalyst_id TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  item_index INTEGER NOT NULL CHECK(item_index >= 0),
  item_payload_sha256 TEXT NOT NULL
    CHECK(length(item_payload_sha256) = 64
      AND item_payload_sha256 NOT GLOB '*[^0-9a-f]*'),
  PRIMARY KEY(collection_cycle_id, catalyst_id),
  UNIQUE(receipt_id, item_index),
  FOREIGN KEY(collection_cycle_id, catalyst_id)
    REFERENCES kr_catalyst_observations(collection_cycle_id, catalyst_id),
  FOREIGN KEY(receipt_id) REFERENCES kr_source_receipts(receipt_id)
);

CREATE TABLE kr_source_collection_runs (
  source_run_id TEXT PRIMARY KEY,
  collection_cycle_id TEXT NOT NULL,
  source TEXT NOT NULL
    CHECK(source IN ('news', 'dart', 'kis_ranking', 'volume_surge')),
  adapter_version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
  record_count INTEGER NOT NULL CHECK(record_count >= 0),
  failure_code TEXT,
  payload_json TEXT NOT NULL,
  UNIQUE(collection_cycle_id, source)
);

CREATE TRIGGER kr_source_receipts_no_update
BEFORE UPDATE ON kr_source_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_source_receipts_no_delete
BEFORE DELETE ON kr_source_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_catalyst_observation_receipts_no_update
BEFORE UPDATE ON kr_catalyst_observation_receipts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_catalyst_observation_receipts_no_delete
BEFORE DELETE ON kr_catalyst_observation_receipts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_source_collection_runs_no_update
BEFORE UPDATE ON kr_source_collection_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kr_source_collection_runs_no_delete
BEFORE DELETE ON kr_source_collection_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CREATE_KR_THEME_SCHEMA: Final = (
    CREATE_KR_THEME_SCHEMA_V1 + CREATE_KR_THEME_SCHEMA_V2_ADDITIONS
)
