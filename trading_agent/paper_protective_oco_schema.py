from typing import Final

EMPTY_PROTECTIVE_OCO_HASH: Final = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"

CREATE_PAPER_PROTECTIVE_OCO_SCHEMA: Final = f"""
ALTER TABLE paper_stream_recoveries
ADD COLUMN protective_ocos_sha256 TEXT NOT NULL DEFAULT '{EMPTY_PROTECTIVE_OCO_HASH}'
CHECK(length(protective_ocos_sha256) = 64);
CREATE TABLE IF NOT EXISTS protective_oco_plans (
  plan_key TEXT PRIMARY KEY CHECK(length(plan_key) = 64),
  parent_intent_id TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  planned_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  take_profit_limit TEXT NOT NULL CHECK(CAST(take_profit_limit AS REAL) > 0),
  stop_price TEXT NOT NULL CHECK(CAST(stop_price AS REAL) > 0),
  time_in_force TEXT NOT NULL CHECK(time_in_force = 'day'),
  extended_hours INTEGER NOT NULL CHECK(extended_hours = 0),
  UNIQUE(client_order_id, quantity, take_profit_limit, stop_price),
  FOREIGN KEY(parent_intent_id) REFERENCES order_intents(intent_id)
);
CREATE TABLE IF NOT EXISTS paper_recovery_protective_oco_legs (
  recovery_key TEXT NOT NULL,
  plan_key TEXT NOT NULL,
  parent_broker_order_id TEXT NOT NULL,
  leg_kind TEXT NOT NULL CHECK(leg_kind IN ('take_profit', 'stop_loss')),
  broker_order_id TEXT NOT NULL,
  client_order_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  status TEXT NOT NULL,
  quantity TEXT NOT NULL CHECK(CAST(quantity AS REAL) > 0),
  filled_quantity TEXT NOT NULL CHECK(CAST(filled_quantity AS REAL) >= 0),
  order_type TEXT NOT NULL CHECK(order_type IN ('limit', 'stop')),
  limit_price TEXT,
  stop_price TEXT,
  time_in_force TEXT NOT NULL,
  extended_hours INTEGER NOT NULL CHECK(extended_hours IN (0, 1)),
  PRIMARY KEY(recovery_key, parent_broker_order_id, leg_kind),
  UNIQUE(recovery_key, broker_order_id),
  FOREIGN KEY(recovery_key) REFERENCES paper_stream_recoveries(recovery_key),
  FOREIGN KEY(plan_key) REFERENCES protective_oco_plans(plan_key),
  CHECK(CAST(filled_quantity AS REAL) <= CAST(quantity AS REAL)),
  CHECK(
    (leg_kind = 'take_profit' AND order_type = 'limit'
      AND limit_price IS NOT NULL AND stop_price IS NULL)
    OR
    (leg_kind = 'stop_loss' AND order_type = 'stop'
      AND limit_price IS NULL AND stop_price IS NOT NULL)
  )
);
CREATE TRIGGER IF NOT EXISTS protective_oco_plans_no_update
BEFORE UPDATE ON protective_oco_plans BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS protective_oco_plans_no_delete
BEFORE DELETE ON protective_oco_plans BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_recovery_protective_oco_legs_no_update
BEFORE UPDATE ON paper_recovery_protective_oco_legs
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_recovery_protective_oco_legs_no_delete
BEFORE DELETE ON paper_recovery_protective_oco_legs
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
