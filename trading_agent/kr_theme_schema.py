from __future__ import annotations

from typing import Final

KR_THEME_SCHEMA_VERSION: Final = 1

CREATE_KR_THEME_SCHEMA: Final = """
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
