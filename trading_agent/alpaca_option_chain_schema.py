from typing import Final

ALPACA_OPTION_CHAIN_SCHEMA_VERSION: Final = 1

CREATE_ALPACA_OPTION_CHAIN_SCHEMA: Final = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE alpaca_option_chain_receipts (
  receipt_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  request_payload BLOB NOT NULL,
  page_index INTEGER NOT NULL,
  page_token TEXT,
  received_at TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  content_type TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  raw_payload BLOB NOT NULL,
  UNIQUE(request_id, page_index)
);
CREATE TABLE alpaca_option_chain_runs (
  request_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  payload_sha256 TEXT NOT NULL,
  run_payload BLOB NOT NULL
);
CREATE TRIGGER alpaca_option_chain_receipts_no_update
BEFORE UPDATE ON alpaca_option_chain_receipts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER alpaca_option_chain_receipts_no_delete
BEFORE DELETE ON alpaca_option_chain_receipts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER alpaca_option_chain_runs_no_update
BEFORE UPDATE ON alpaca_option_chain_runs
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER alpaca_option_chain_runs_no_delete
BEFORE DELETE ON alpaca_option_chain_runs
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

__all__ = (
    "ALPACA_OPTION_CHAIN_SCHEMA_VERSION",
    "CREATE_ALPACA_OPTION_CHAIN_SCHEMA",
)
