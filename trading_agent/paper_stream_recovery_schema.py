from typing import Final

CREATE_PAPER_STREAM_RECOVERY_SCHEMA_V3: Final = """
CREATE TABLE IF NOT EXISTS paper_stream_recoveries (
  recovery_key TEXT PRIMARY KEY,
  account_fingerprint TEXT NOT NULL,
  connection_epoch TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256) = 64),
  orders_sha256 TEXT NOT NULL CHECK(length(orders_sha256) = 64),
  execution_detail_complete INTEGER NOT NULL
    CHECK(execution_detail_complete IN (0, 1)),
  UNIQUE(account_fingerprint, connection_epoch, started_at, completed_at)
);
CREATE TABLE IF NOT EXISTS paper_recovery_orders (
  recovery_key TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('open', 'targeted', 'recent')),
  broker_order_id TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  status TEXT NOT NULL,
  quantity TEXT NOT NULL CHECK(CAST(quantity AS REAL) > 0),
  filled_quantity TEXT NOT NULL CHECK(CAST(filled_quantity AS REAL) >= 0),
  filled_average_price TEXT,
  limit_price TEXT,
  time_in_force TEXT NOT NULL,
  extended_hours INTEGER NOT NULL CHECK(extended_hours IN (0, 1)),
  created_at TEXT,
  updated_at TEXT,
  submitted_at TEXT,
  filled_at TEXT,
  canceled_at TEXT,
  failed_at TEXT,
  replaced_at TEXT,
  replaced_by_order_id TEXT,
  replaces_order_id TEXT,
  PRIMARY KEY(recovery_key, broker_order_id),
  FOREIGN KEY(recovery_key) REFERENCES paper_stream_recoveries(recovery_key),
  CHECK(CAST(filled_quantity AS REAL) <= CAST(quantity AS REAL))
);
CREATE TRIGGER IF NOT EXISTS paper_stream_recoveries_no_update
BEFORE UPDATE ON paper_stream_recoveries BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_stream_recoveries_no_delete
BEFORE DELETE ON paper_stream_recoveries BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_recovery_orders_no_update
BEFORE UPDATE ON paper_recovery_orders BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_recovery_orders_no_delete
BEFORE DELETE ON paper_recovery_orders BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CREATE_PAPER_STREAM_RECOVERY_SCHEMA: Final = (
    CREATE_PAPER_STREAM_RECOVERY_SCHEMA_V3 + "\nALTER TABLE paper_stream_recoveries "
    "ADD COLUMN activities_sha256 TEXT NOT NULL DEFAULT "
    "'4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945' "
    "CHECK(length(activities_sha256) = 64);"
)
