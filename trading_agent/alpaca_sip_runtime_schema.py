from typing import Final

ALPACA_SIP_RUNTIME_SCHEMA_VERSION: Final = 1

CREATE_ALPACA_SIP_RUNTIME_SCHEMA: Final = (
    "CREATE TABLE alpaca_sip_raw_pages ("
    "generation INTEGER PRIMARY KEY AUTOINCREMENT,"
    "receipt_id TEXT NOT NULL UNIQUE,session_date TEXT NOT NULL,symbol TEXT NOT NULL,"
    "request_start_at TEXT NOT NULL,request_end_at TEXT NOT NULL,page_index INTEGER NOT NULL,"
    "page_token TEXT,received_at TEXT NOT NULL,payload_sha256 TEXT NOT NULL,raw_response BLOB NOT NULL);"
    "CREATE TABLE alpaca_sip_projections (dataset_id TEXT PRIMARY KEY,"
    "projection_key TEXT NOT NULL UNIQUE,dataset_directory TEXT NOT NULL,"
    "identity_scope TEXT NOT NULL,recorded_at TEXT NOT NULL);"
    "CREATE TRIGGER alpaca_sip_raw_pages_no_update BEFORE UPDATE ON alpaca_sip_raw_pages "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER alpaca_sip_raw_pages_no_delete BEFORE DELETE ON alpaca_sip_raw_pages "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER alpaca_sip_projections_no_update BEFORE UPDATE ON alpaca_sip_projections "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
    "CREATE TRIGGER alpaca_sip_projections_no_delete BEFORE DELETE ON alpaca_sip_projections "
    "BEGIN SELECT RAISE(ABORT, 'append only'); END;"
)

__all__ = ("ALPACA_SIP_RUNTIME_SCHEMA_VERSION", "CREATE_ALPACA_SIP_RUNTIME_SCHEMA")
