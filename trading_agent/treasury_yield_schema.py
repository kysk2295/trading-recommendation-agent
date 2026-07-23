from typing import Final

TREASURY_YIELD_SCHEMA_VERSION: Final = 1
CREATE_TREASURY_YIELD_SCHEMA: Final = """
CREATE TABLE treasury_yield_receipts (
    request_id TEXT PRIMARY KEY,
    request_payload BLOB NOT NULL,
    receipt_id TEXT NOT NULL UNIQUE,
    received_at TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    raw_payload BLOB NOT NULL
) STRICT;
CREATE TABLE treasury_yield_runs (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    run_sha256 TEXT NOT NULL,
    run_payload BLOB NOT NULL
) STRICT;
CREATE TRIGGER treasury_yield_receipts_no_update
BEFORE UPDATE ON treasury_yield_receipts BEGIN
    SELECT RAISE(ABORT, 'append-only');
END;
CREATE TRIGGER treasury_yield_receipts_no_delete
BEFORE DELETE ON treasury_yield_receipts BEGIN
    SELECT RAISE(ABORT, 'append-only');
END;
CREATE TRIGGER treasury_yield_runs_no_update
BEFORE UPDATE ON treasury_yield_runs BEGIN
    SELECT RAISE(ABORT, 'append-only');
END;
CREATE TRIGGER treasury_yield_runs_no_delete
BEFORE DELETE ON treasury_yield_runs BEGIN
    SELECT RAISE(ABORT, 'append-only');
END;
"""

__all__ = (
    "CREATE_TREASURY_YIELD_SCHEMA",
    "TREASURY_YIELD_SCHEMA_VERSION",
)
