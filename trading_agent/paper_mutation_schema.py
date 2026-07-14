from typing import Final

CREATE_PAPER_MUTATION_SCHEMA_V7: Final = """
CREATE TABLE IF NOT EXISTS paper_mutation_intents (
  mutation_key TEXT PRIMARY KEY CHECK(length(mutation_key) = 64),
  account_fingerprint TEXT NOT NULL CHECK(length(account_fingerprint) = 64),
  created_at TEXT NOT NULL,
  operation TEXT NOT NULL CHECK(operation IN
    ('submit_protective_oco', 'cancel_order', 'close_position')),
  protective_plan_key TEXT,
  safety_plan_key TEXT,
  action_sequence INTEGER CHECK(action_sequence >= 0),
  request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64),
  symbol TEXT NOT NULL,
  broker_order_id TEXT,
  side TEXT CHECK(side IN ('buy', 'sell')),
  quantity TEXT,
  FOREIGN KEY(protective_plan_key) REFERENCES protective_oco_plans(plan_key),
  FOREIGN KEY(safety_plan_key) REFERENCES paper_safety_plans(plan_key),
  CHECK(
    (operation = 'submit_protective_oco' AND protective_plan_key IS NOT NULL
      AND safety_plan_key IS NULL AND action_sequence IS NULL
      AND broker_order_id IS NULL AND side IS NOT NULL AND quantity IS NOT NULL)
    OR
    (operation = 'cancel_order' AND protective_plan_key IS NULL
      AND safety_plan_key IS NOT NULL AND action_sequence IS NOT NULL
      AND broker_order_id IS NOT NULL AND side IS NULL AND quantity IS NULL)
    OR
    (operation = 'close_position' AND protective_plan_key IS NULL
      AND safety_plan_key IS NOT NULL AND action_sequence IS NOT NULL
      AND broker_order_id IS NULL AND side IS NOT NULL AND quantity IS NOT NULL)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_mutation_intent_identity
ON paper_mutation_intents(
  operation,
  IFNULL(protective_plan_key, ''),
  IFNULL(safety_plan_key, ''),
  IFNULL(action_sequence, -1)
);
CREATE TABLE IF NOT EXISTS paper_mutation_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE CHECK(length(event_key) = 64),
  mutation_key TEXT NOT NULL,
  attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK(event_type IN
    ('attempted', 'acknowledged', 'rejected', 'ambiguous',
     'recovered_acknowledged', 'recovered_absent')),
  request_id TEXT,
  status_code INTEGER,
  broker_order_id TEXT,
  evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256) = 64),
  UNIQUE(mutation_key, attempt_number, event_type),
  FOREIGN KEY(mutation_key) REFERENCES paper_mutation_intents(mutation_key)
);
CREATE TRIGGER IF NOT EXISTS paper_mutation_intents_no_update
BEFORE UPDATE ON paper_mutation_intents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_mutation_intents_no_delete
BEFORE DELETE ON paper_mutation_intents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_mutation_events_no_update
BEFORE UPDATE ON paper_mutation_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_mutation_events_no_delete
BEFORE DELETE ON paper_mutation_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CREATE_PAPER_MUTATION_SCHEMA: Final = (
    CREATE_PAPER_MUTATION_SCHEMA_V7.replace(
        "('submit_protective_oco', 'cancel_order', 'close_position')",
        "('submit_entry', 'submit_protective_oco', 'cancel_order', 'close_position')",
    )
    .replace(
        "  quantity TEXT,\n  FOREIGN KEY(protective_plan_key)",
        "  quantity TEXT,\n  entry_intent_id TEXT,\n"
        "  FOREIGN KEY(entry_intent_id) REFERENCES order_intents(intent_id),\n"
        "  FOREIGN KEY(protective_plan_key)",
    )
    .replace(
        "  CHECK(\n    (operation = 'submit_protective_oco'",
        "  CHECK(\n"
        "    (operation = 'submit_entry' AND entry_intent_id IS NOT NULL\n"
        "      AND protective_plan_key IS NULL AND safety_plan_key IS NULL\n"
        "      AND action_sequence IS NULL AND broker_order_id IS NULL\n"
        "      AND side IS NOT NULL AND quantity IS NOT NULL)\n"
        "    OR\n"
        "    (operation = 'submit_protective_oco'",
    )
    .replace(
        "AND broker_order_id IS NULL AND side IS NOT NULL AND quantity IS NOT NULL)\n    OR",
        "AND broker_order_id IS NULL AND side IS NOT NULL AND quantity IS NOT NULL\n"
        "      AND entry_intent_id IS NULL)\n    OR",
        1,
    )
    .replace(
        "AND broker_order_id IS NOT NULL AND side IS NULL AND quantity IS NULL)\n    OR",
        "AND broker_order_id IS NOT NULL AND side IS NULL AND quantity IS NULL\n"
        "      AND entry_intent_id IS NULL)\n    OR",
    )
    .replace(
        "AND broker_order_id IS NULL AND side IS NOT NULL AND quantity IS NOT NULL)\n  )",
        "AND broker_order_id IS NULL AND side IS NOT NULL AND quantity IS NOT NULL\n"
        "      AND entry_intent_id IS NULL)\n  )",
    )
    .replace(
        "  IFNULL(action_sequence, -1)\n);",
        "  IFNULL(action_sequence, -1),\n  IFNULL(entry_intent_id, '')\n);",
    )
)

MIGRATE_PAPER_MUTATION_V7_TO_V8: Final = f"""
DROP TRIGGER paper_mutation_intents_no_update;
DROP TRIGGER paper_mutation_intents_no_delete;
DROP TRIGGER paper_mutation_events_no_update;
DROP TRIGGER paper_mutation_events_no_delete;
DROP INDEX paper_mutation_intent_identity;
ALTER TABLE paper_mutation_events RENAME TO paper_mutation_events_v7;
ALTER TABLE paper_mutation_intents RENAME TO paper_mutation_intents_v7;
{CREATE_PAPER_MUTATION_SCHEMA}
INSERT INTO paper_mutation_intents (
  mutation_key, account_fingerprint, created_at, operation,
  protective_plan_key, safety_plan_key, action_sequence, request_sha256,
  symbol, broker_order_id, side, quantity, entry_intent_id
)
SELECT mutation_key, account_fingerprint, created_at, operation,
  protective_plan_key, safety_plan_key, action_sequence, request_sha256,
  symbol, broker_order_id, side, quantity, NULL
FROM paper_mutation_intents_v7;
INSERT INTO paper_mutation_events SELECT * FROM paper_mutation_events_v7;
DROP TABLE paper_mutation_events_v7;
DROP TABLE paper_mutation_intents_v7;
"""
