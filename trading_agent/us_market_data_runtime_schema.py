from __future__ import annotations

from typing import Final

MARKET_DATA_RUNTIME_SCHEMA_VERSION: Final = 1

CREATE_MARKET_DATA_RUNTIME_SCHEMA: Final = """
CREATE TABLE market_data_runtime_receipts (
  source_id TEXT NOT NULL,
  connection_epoch TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence > 0),
  receipt_id TEXT NOT NULL UNIQUE,
  received_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  raw_payload BLOB NOT NULL,
  instrument_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  bar_start_at TEXT NOT NULL,
  bar_end_at TEXT NOT NULL,
  open TEXT NOT NULL,
  high TEXT NOT NULL,
  low TEXT NOT NULL,
  close TEXT NOT NULL,
  volume INTEGER NOT NULL CHECK(volume >= 0),
  PRIMARY KEY(source_id, connection_epoch, sequence)
);
CREATE INDEX market_data_runtime_receipts_by_instrument
ON market_data_runtime_receipts(source_id, connection_epoch, instrument_id, sequence);

CREATE TABLE market_data_runtime_incidents (
  incident_key TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('sequence_gap', 'reconnect')),
  source_id TEXT NOT NULL,
  previous_epoch TEXT,
  current_epoch TEXT NOT NULL,
  expected_sequence INTEGER,
  observed_sequence INTEGER,
  recorded_at TEXT NOT NULL
);

CREATE TABLE market_data_runtime_checkpoints (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  connection_epoch TEXT NOT NULL,
  last_sequence INTEGER NOT NULL CHECK(last_sequence > 0),
  gap_blocked INTEGER NOT NULL CHECK(gap_blocked IN (0, 1)),
  recorded_at TEXT NOT NULL
);
CREATE INDEX market_data_runtime_checkpoints_latest
ON market_data_runtime_checkpoints(source_id, generation DESC);

CREATE TRIGGER market_data_runtime_receipts_no_update
BEFORE UPDATE ON market_data_runtime_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER market_data_runtime_receipts_no_delete
BEFORE DELETE ON market_data_runtime_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER market_data_runtime_incidents_no_update
BEFORE UPDATE ON market_data_runtime_incidents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER market_data_runtime_incidents_no_delete
BEFORE DELETE ON market_data_runtime_incidents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER market_data_runtime_checkpoints_no_update
BEFORE UPDATE ON market_data_runtime_checkpoints BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER market_data_runtime_checkpoints_no_delete
BEFORE DELETE ON market_data_runtime_checkpoints BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
