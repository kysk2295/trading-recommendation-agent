from typing import Final

CREATE_PAPER_SAFETY_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS paper_safety_plans (
  plan_key TEXT PRIMARY KEY CHECK(length(plan_key) = 64),
  account_fingerprint TEXT NOT NULL CHECK(length(account_fingerprint) = 64),
  observed_at TEXT NOT NULL,
  session_date TEXT NOT NULL,
  phase TEXT NOT NULL CHECK(phase IN ('entry_cutoff', 'kill_switch', 'eod_flatten')),
  mark_to_market_daily_pnl TEXT NOT NULL,
  conservative_daily_pnl TEXT NOT NULL,
  actions_sha256 TEXT NOT NULL CHECK(length(actions_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS paper_safety_actions (
  plan_key TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence >= 0),
  kind TEXT NOT NULL CHECK(kind IN ('cancel_order', 'close_position')),
  broker_order_id TEXT,
  symbol TEXT NOT NULL,
  protective_oco INTEGER CHECK(protective_oco IN (0, 1)),
  side TEXT CHECK(side IN ('buy', 'sell')),
  quantity TEXT,
  PRIMARY KEY(plan_key, sequence),
  FOREIGN KEY(plan_key) REFERENCES paper_safety_plans(plan_key),
  CHECK(
    (kind = 'cancel_order' AND broker_order_id IS NOT NULL
      AND protective_oco IS NOT NULL AND side IS NULL AND quantity IS NULL)
    OR
    (kind = 'close_position' AND broker_order_id IS NULL
      AND protective_oco IS NULL AND side IS NOT NULL
      AND quantity IS NOT NULL AND CAST(quantity AS REAL) > 0)
  )
);
CREATE TRIGGER IF NOT EXISTS paper_safety_plans_no_update
BEFORE UPDATE ON paper_safety_plans BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_safety_plans_no_delete
BEFORE DELETE ON paper_safety_plans BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_safety_actions_no_update
BEFORE UPDATE ON paper_safety_actions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_safety_actions_no_delete
BEFORE DELETE ON paper_safety_actions BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
