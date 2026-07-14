from typing import Final

CREATE_TRADE_UPDATE_RECEIPT_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS trade_update_raw_receipts (
  receipt_key TEXT PRIMARY KEY,
  raw_payload_sha256 TEXT NOT NULL CHECK(length(raw_payload_sha256) = 64),
  wire_kind TEXT NOT NULL CHECK(wire_kind IN ('text', 'binary')),
  raw_payload BLOB NOT NULL,
  account_fingerprint TEXT NOT NULL,
  connection_epoch TEXT NOT NULL,
  received_at TEXT NOT NULL,
  UNIQUE(account_fingerprint, connection_epoch, wire_kind, raw_payload_sha256)
);
CREATE TABLE IF NOT EXISTS trade_update_receipt_dispositions (
  receipt_key TEXT PRIMARY KEY,
  disposition TEXT NOT NULL CHECK(disposition IN ('accepted', 'quarantined')),
  event_key TEXT,
  reason_code TEXT,
  classified_at TEXT NOT NULL,
  recovery_high_water INTEGER NOT NULL CHECK(recovery_high_water >= 0),
  FOREIGN KEY(receipt_key) REFERENCES trade_update_raw_receipts(receipt_key),
  FOREIGN KEY(event_key) REFERENCES trade_update_events(event_key),
  CHECK(
    (disposition = 'accepted' AND event_key IS NOT NULL AND reason_code IS NULL)
    OR
    (disposition = 'quarantined' AND event_key IS NULL AND reason_code IS NOT NULL)
  )
);
CREATE TRIGGER IF NOT EXISTS trade_update_raw_receipts_no_update
BEFORE UPDATE ON trade_update_raw_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_update_raw_receipts_no_delete
BEFORE DELETE ON trade_update_raw_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_update_receipt_dispositions_no_update
BEFORE UPDATE ON trade_update_receipt_dispositions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_update_receipt_dispositions_no_delete
BEFORE DELETE ON trade_update_receipt_dispositions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
