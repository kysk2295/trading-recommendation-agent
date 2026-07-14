from typing import Final

EMPTY_ACTIVITY_HASH: Final = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"

CREATE_PAPER_ACCOUNT_ACTIVITY_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS paper_account_activities (
  activity_id TEXT PRIMARY KEY,
  account_fingerprint TEXT NOT NULL,
  broker_order_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  event_type TEXT NOT NULL CHECK(event_type IN ('partial_fill', 'fill')),
  quantity TEXT NOT NULL CHECK(CAST(quantity AS REAL) > 0),
  cumulative_quantity TEXT NOT NULL CHECK(CAST(cumulative_quantity AS REAL) > 0),
  leaves_quantity TEXT NOT NULL CHECK(CAST(leaves_quantity AS REAL) >= 0),
  price TEXT NOT NULL CHECK(CAST(price AS REAL) > 0),
  transaction_time TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
  CHECK(CAST(quantity AS REAL) <= CAST(cumulative_quantity AS REAL))
);
CREATE TABLE IF NOT EXISTS paper_recovery_activities (
  recovery_key TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  PRIMARY KEY(recovery_key, activity_id),
  FOREIGN KEY(recovery_key) REFERENCES paper_stream_recoveries(recovery_key),
  FOREIGN KEY(activity_id) REFERENCES paper_account_activities(activity_id)
);
CREATE TRIGGER IF NOT EXISTS paper_account_activities_no_update
BEFORE UPDATE ON paper_account_activities BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_account_activities_no_delete
BEFORE DELETE ON paper_account_activities BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_recovery_activities_no_update
BEFORE UPDATE ON paper_recovery_activities BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_recovery_activities_no_delete
BEFORE DELETE ON paper_recovery_activities BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

MIGRATE_PAPER_RECOVERY_V3_TO_V4: Final = f"""
ALTER TABLE paper_stream_recoveries
ADD COLUMN activities_sha256 TEXT NOT NULL DEFAULT '{EMPTY_ACTIVITY_HASH}'
CHECK(length(activities_sha256) = 64);
{CREATE_PAPER_ACCOUNT_ACTIVITY_SCHEMA}
"""
