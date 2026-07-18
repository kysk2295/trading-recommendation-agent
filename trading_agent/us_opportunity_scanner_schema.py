from typing import Final

US_OPPORTUNITY_SCANNER_SCHEMA_VERSION: Final = 1

CREATE_US_OPPORTUNITY_SCANNER_SCHEMA: Final = (
    "CREATE TABLE us_opportunity_scanner_raw ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL UNIQUE,"
    "opportunity_id TEXT NOT NULL UNIQUE,observed_at TEXT NOT NULL,"
    "payload_sha256 TEXT NOT NULL,raw_payload BLOB NOT NULL);"
    "CREATE TABLE us_opportunity_scanner_projections ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,dataset_id TEXT NOT NULL UNIQUE,"
    "projection_key TEXT NOT NULL UNIQUE,opportunity_id TEXT NOT NULL UNIQUE,"
    "dataset_directory TEXT NOT NULL,snapshot_payload BLOB NOT NULL,recorded_at TEXT NOT NULL);"
    "CREATE TRIGGER us_opportunity_scanner_raw_no_update BEFORE UPDATE ON us_opportunity_scanner_raw "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER us_opportunity_scanner_raw_no_delete BEFORE DELETE ON us_opportunity_scanner_raw "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER us_opportunity_scanner_projections_no_update "
    "BEFORE UPDATE ON us_opportunity_scanner_projections "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER us_opportunity_scanner_projections_no_delete "
    "BEFORE DELETE ON us_opportunity_scanner_projections "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
)

__all__ = (
    "CREATE_US_OPPORTUNITY_SCANNER_SCHEMA",
    "US_OPPORTUNITY_SCANNER_SCHEMA_VERSION",
)
