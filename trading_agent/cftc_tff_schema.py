from typing import Final

CFTC_TFF_SCHEMA_VERSION: Final = 1

CREATE_CFTC_TFF_SCHEMA: Final = """
CREATE TABLE cftc_tff_receipts (
    request_id TEXT PRIMARY KEY NOT NULL,
    request_payload BLOB NOT NULL,
    receipt_id TEXT UNIQUE NOT NULL,
    received_at TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    raw_payload BLOB NOT NULL
) STRICT;

CREATE TABLE cftc_tff_runs (
    request_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT UNIQUE NOT NULL,
    run_sha256 TEXT NOT NULL,
    run_payload BLOB NOT NULL
) STRICT;

CREATE TRIGGER cftc_tff_receipts_no_update
BEFORE UPDATE ON cftc_tff_receipts
BEGIN
    SELECT RAISE(ABORT, 'cftc_tff_receipts are append-only');
END;

CREATE TRIGGER cftc_tff_receipts_no_delete
BEFORE DELETE ON cftc_tff_receipts
BEGIN
    SELECT RAISE(ABORT, 'cftc_tff_receipts are append-only');
END;

CREATE TRIGGER cftc_tff_runs_no_update
BEFORE UPDATE ON cftc_tff_runs
BEGIN
    SELECT RAISE(ABORT, 'cftc_tff_runs are append-only');
END;

CREATE TRIGGER cftc_tff_runs_no_delete
BEFORE DELETE ON cftc_tff_runs
BEGIN
    SELECT RAISE(ABORT, 'cftc_tff_runs are append-only');
END;
"""

__all__ = ("CFTC_TFF_SCHEMA_VERSION", "CREATE_CFTC_TFF_SCHEMA")
