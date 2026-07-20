from __future__ import annotations

from typing import Final

SEC_EDGAR_SCHEMA_VERSION: Final = 1
SEC_EDGAR_SCHEMA: Final = """
CREATE TABLE sec_submission_receipts (
  receipt_id TEXT PRIMARY KEY,
  collection_id TEXT NOT NULL,
  cik TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  content_type TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  raw_payload BLOB NOT NULL,
  UNIQUE(collection_id, cik)
);
CREATE TABLE sec_filing_versions (
  version_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  cik TEXT NOT NULL,
  accession_number TEXT NOT NULL,
  previous_version_id TEXT REFERENCES sec_filing_versions(version_id),
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX sec_filing_versions_by_accession
ON sec_filing_versions(cik, accession_number);
CREATE TABLE sec_submission_runs (
  run_id TEXT PRIMARY KEY,
  collection_id TEXT NOT NULL,
  cik TEXT NOT NULL,
  receipt_id TEXT REFERENCES sec_submission_receipts(receipt_id),
  status TEXT NOT NULL,
  failure_code TEXT,
  filing_count INTEGER NOT NULL,
  additional_history_file_count INTEGER NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE(collection_id, cik)
);
CREATE TABLE sec_filing_observations (
  run_id TEXT NOT NULL REFERENCES sec_submission_runs(run_id),
  receipt_id TEXT NOT NULL REFERENCES sec_submission_receipts(receipt_id),
  version_id TEXT NOT NULL REFERENCES sec_filing_versions(version_id),
  item_index INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  PRIMARY KEY(run_id, item_index),
  UNIQUE(run_id, version_id)
);
CREATE INDEX sec_filing_observations_by_version
ON sec_filing_observations(version_id, run_id);
CREATE TRIGGER sec_submission_receipts_no_update
BEFORE UPDATE ON sec_submission_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_submission_receipts_no_delete
BEFORE DELETE ON sec_submission_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_filing_versions_no_update
BEFORE UPDATE ON sec_filing_versions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_filing_versions_no_delete
BEFORE DELETE ON sec_filing_versions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_submission_runs_no_update
BEFORE UPDATE ON sec_submission_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_submission_runs_no_delete
BEFORE DELETE ON sec_submission_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_filing_observations_no_update
BEFORE UPDATE ON sec_filing_observations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_filing_observations_no_delete
BEFORE DELETE ON sec_filing_observations BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

SEC_EDGAR_SCHEMA_OBJECTS: Final = frozenset(
    {
        "sec_submission_receipts",
        "sec_filing_versions",
        "sec_filing_versions_by_accession",
        "sec_submission_runs",
        "sec_filing_observations",
        "sec_filing_observations_by_version",
        "sec_submission_receipts_no_update",
        "sec_submission_receipts_no_delete",
        "sec_filing_versions_no_update",
        "sec_filing_versions_no_delete",
        "sec_submission_runs_no_update",
        "sec_submission_runs_no_delete",
        "sec_filing_observations_no_update",
        "sec_filing_observations_no_delete",
    }
)
