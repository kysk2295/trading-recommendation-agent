from typing import Final

BLS_PUBLIC_SCHEMA_VERSION: Final = 1

CREATE_BLS_PUBLIC_SCHEMA: Final = """
CREATE TABLE bls_public_receipts (
    request_id TEXT PRIMARY KEY NOT NULL,
    request_payload BLOB NOT NULL,
    receipt_id TEXT UNIQUE NOT NULL,
    received_at TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    raw_payload BLOB NOT NULL
) STRICT;

CREATE TABLE bls_public_runs (
    request_id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT UNIQUE NOT NULL,
    run_sha256 TEXT NOT NULL,
    run_payload BLOB NOT NULL
) STRICT;

CREATE TRIGGER bls_public_receipts_no_update
BEFORE UPDATE ON bls_public_receipts
BEGIN
    SELECT RAISE(ABORT, 'bls_public_receipts are append-only');
END;

CREATE TRIGGER bls_public_receipts_no_delete
BEFORE DELETE ON bls_public_receipts
BEGIN
    SELECT RAISE(ABORT, 'bls_public_receipts are append-only');
END;

CREATE TRIGGER bls_public_runs_no_update
BEFORE UPDATE ON bls_public_runs
BEGIN
    SELECT RAISE(ABORT, 'bls_public_runs are append-only');
END;

CREATE TRIGGER bls_public_runs_no_delete
BEFORE DELETE ON bls_public_runs
BEGIN
    SELECT RAISE(ABORT, 'bls_public_runs are append-only');
END;
"""

__all__ = ("BLS_PUBLIC_SCHEMA_VERSION", "CREATE_BLS_PUBLIC_SCHEMA")
