from __future__ import annotations

KIS_OVERSEAS_FUTURES_SCHEMA_VERSION = 1
CREATE_KIS_OVERSEAS_FUTURES_SCHEMA = """
CREATE TABLE kis_futures_quote_receipts(
 request_id TEXT NOT NULL,
 request_payload BLOB NOT NULL,
 symbol TEXT NOT NULL,
 receipt_id TEXT NOT NULL UNIQUE,
 received_at TEXT NOT NULL,
 status_code INTEGER NOT NULL,
 content_type TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,
 raw_payload BLOB NOT NULL,
 PRIMARY KEY(request_id, symbol)
);
CREATE TABLE kis_futures_quote_runs(
 request_id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL UNIQUE,
 run_sha256 TEXT NOT NULL,
 run_payload BLOB NOT NULL
);
CREATE TRIGGER kis_futures_quote_receipts_no_update
BEFORE UPDATE ON kis_futures_quote_receipts BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER kis_futures_quote_receipts_no_delete
BEFORE DELETE ON kis_futures_quote_receipts BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER kis_futures_quote_runs_no_update
BEFORE UPDATE ON kis_futures_quote_runs BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER kis_futures_quote_runs_no_delete
BEFORE DELETE ON kis_futures_quote_runs BEGIN SELECT RAISE(ABORT,'immutable'); END;
"""

__all__ = (
    "CREATE_KIS_OVERSEAS_FUTURES_SCHEMA",
    "KIS_OVERSEAS_FUTURES_SCHEMA_VERSION",
)
