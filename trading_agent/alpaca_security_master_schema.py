from typing import Final

ALPACA_SECURITY_MASTER_SCHEMA_VERSION: Final = 1

CREATE_ALPACA_SECURITY_MASTER_SCHEMA: Final = (
    "CREATE TABLE alpaca_security_master_raw ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL UNIQUE,"
    "observed_at TEXT NOT NULL,payload_sha256 TEXT NOT NULL,raw_payload BLOB NOT NULL);"
    "CREATE TABLE alpaca_security_master_snapshots ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,snapshot_id TEXT NOT NULL UNIQUE,"
    "raw_receipt_id TEXT NOT NULL UNIQUE,observed_at TEXT NOT NULL,snapshot_payload BLOB NOT NULL);"
    "CREATE TRIGGER alpaca_security_master_raw_no_update BEFORE UPDATE "
    "ON alpaca_security_master_raw BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER alpaca_security_master_raw_no_delete BEFORE DELETE "
    "ON alpaca_security_master_raw BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER alpaca_security_master_snapshots_no_update BEFORE UPDATE "
    "ON alpaca_security_master_snapshots BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER alpaca_security_master_snapshots_no_delete BEFORE DELETE "
    "ON alpaca_security_master_snapshots BEGIN SELECT RAISE(ABORT, 'append only'); END;"
)

__all__ = (
    "ALPACA_SECURITY_MASTER_SCHEMA_VERSION",
    "CREATE_ALPACA_SECURITY_MASTER_SCHEMA",
)
