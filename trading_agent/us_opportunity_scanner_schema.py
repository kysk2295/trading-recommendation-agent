from typing import Final

US_OPPORTUNITY_SCANNER_SCHEMA_VERSION: Final = 2

CREATE_US_OPPORTUNITY_SCANNER_SCHEMA: Final = (
    "CREATE TABLE us_opportunity_scanner_raw ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL UNIQUE,"
    "opportunity_id TEXT NOT NULL UNIQUE,observed_at TEXT NOT NULL,"
    "payload_sha256 TEXT NOT NULL,raw_payload BLOB NOT NULL);"
    "CREATE TABLE us_opportunity_scanner_projections ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,dataset_id TEXT NOT NULL UNIQUE,"
    "projection_key TEXT NOT NULL UNIQUE,opportunity_id TEXT NOT NULL UNIQUE,"
    "dataset_directory TEXT NOT NULL,snapshot_payload BLOB NOT NULL,"
    "foundation_manifest_id TEXT NOT NULL,foundation_payload BLOB NOT NULL,"
    "security_master_id TEXT,recorded_at TEXT NOT NULL);"
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

MIGRATE_US_OPPORTUNITY_SCANNER_V1_TO_V2: Final = (
    "ALTER TABLE us_opportunity_scanner_projections ADD COLUMN foundation_manifest_id TEXT;"
    "ALTER TABLE us_opportunity_scanner_projections ADD COLUMN foundation_payload BLOB;"
    "ALTER TABLE us_opportunity_scanner_projections ADD COLUMN security_master_id TEXT;"
)

__all__ = (
    "CREATE_US_OPPORTUNITY_SCANNER_SCHEMA",
    "MIGRATE_US_OPPORTUNITY_SCANNER_V1_TO_V2",
    "US_OPPORTUNITY_SCANNER_SCHEMA_VERSION",
)
