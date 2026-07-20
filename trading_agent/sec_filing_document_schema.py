from __future__ import annotations

from typing import Final

SEC_FILING_DOCUMENT_SCHEMA_VERSION: Final = 1
SEC_FILING_DOCUMENT_SCHEMA: Final = """
CREATE TABLE sec_filing_document_receipts (
  receipt_id TEXT PRIMARY KEY,
  target_id TEXT NOT NULL UNIQUE,
  target_payload_sha256 TEXT NOT NULL,
  target_payload_json TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  content_type TEXT NOT NULL,
  content_encoding TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  raw_payload BLOB NOT NULL
);
CREATE TABLE sec_filing_document_runs (
  run_id TEXT PRIMARY KEY,
  target_id TEXT NOT NULL UNIQUE,
  receipt_id TEXT REFERENCES sec_filing_document_receipts(receipt_id),
  status TEXT NOT NULL,
  failure_code TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  byte_count INTEGER NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TRIGGER sec_filing_document_receipts_no_update
BEFORE UPDATE ON sec_filing_document_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_filing_document_receipts_no_delete
BEFORE DELETE ON sec_filing_document_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_filing_document_runs_no_update
BEFORE UPDATE ON sec_filing_document_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER sec_filing_document_runs_no_delete
BEFORE DELETE ON sec_filing_document_runs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
SEC_FILING_DOCUMENT_SCHEMA_OBJECTS: Final = frozenset(
    {
        "sec_filing_document_receipts",
        "sec_filing_document_runs",
        "sec_filing_document_receipts_no_update",
        "sec_filing_document_receipts_no_delete",
        "sec_filing_document_runs_no_update",
        "sec_filing_document_runs_no_delete",
    }
)
