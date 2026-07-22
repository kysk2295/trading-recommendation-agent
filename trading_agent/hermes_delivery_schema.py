from __future__ import annotations

import sqlite3

HERMES_DELIVERY_SCHEMA_VERSION = 1


class UnsupportedHermesDeliverySchemaError(ValueError):
    pass


CREATE_HERMES_DELIVERY_SCHEMA = """
CREATE TABLE hermes_delivery_events (
    delivery_id TEXT PRIMARY KEY, root_delivery_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
    max_attempts INTEGER NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE hermes_delivery_attempts (
    attempt_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES hermes_delivery_events(delivery_id),
    attempt_number INTEGER NOT NULL, lease_expires_at TEXT NOT NULL, payload_json TEXT NOT NULL,
    UNIQUE(delivery_id, attempt_number)
);
CREATE TABLE hermes_delivery_transitions (
    transition_id TEXT PRIMARY KEY, delivery_id TEXT NOT NULL REFERENCES hermes_delivery_events(delivery_id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES hermes_delivery_attempts(attempt_id), kind TEXT NOT NULL,
    available_at TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE hermes_delivery_acknowledgements (
    acknowledgement_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL UNIQUE REFERENCES hermes_delivery_events(delivery_id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES hermes_delivery_attempts(attempt_id),
    platform_message_id TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL
);
CREATE TRIGGER hermes_delivery_events_no_update BEFORE UPDATE ON hermes_delivery_events
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hermes_delivery_events_no_delete BEFORE DELETE ON hermes_delivery_events
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hermes_delivery_attempts_no_update BEFORE UPDATE ON hermes_delivery_attempts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hermes_delivery_attempts_no_delete BEFORE DELETE ON hermes_delivery_attempts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hermes_delivery_transitions_no_update BEFORE UPDATE ON hermes_delivery_transitions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hermes_delivery_transitions_no_delete BEFORE DELETE ON hermes_delivery_transitions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hermes_delivery_acknowledgements_no_update BEFORE UPDATE ON hermes_delivery_acknowledgements
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER hermes_delivery_acknowledgements_no_delete BEFORE DELETE ON hermes_delivery_acknowledgements
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CLAIMABLE_HERMES_DELIVERY_SQL = """
SELECT e.payload_json FROM hermes_delivery_events e
LEFT JOIN hermes_delivery_attempts a ON a.delivery_id = e.delivery_id
 AND a.attempt_number = (
    SELECT MAX(x.attempt_number) FROM hermes_delivery_attempts x WHERE x.delivery_id = e.delivery_id
 )
LEFT JOIN hermes_delivery_transitions t ON t.attempt_id = a.attempt_id
WHERE NOT EXISTS (
    SELECT 1 FROM hermes_delivery_acknowledgements ack WHERE ack.delivery_id = e.delivery_id
 )
 AND NOT EXISTS (
    SELECT 1 FROM hermes_delivery_transitions dead
    WHERE dead.delivery_id = e.delivery_id AND dead.kind = 'dead_letter'
 )
 AND (
    SELECT COUNT(*) FROM hermes_delivery_attempts count_attempt
    WHERE count_attempt.delivery_id = e.delivery_id
 ) < e.max_attempts
 AND (e.delivery_id = e.root_delivery_id OR EXISTS (
    SELECT 1 FROM hermes_delivery_acknowledgements root_ack
    WHERE root_ack.delivery_id = e.root_delivery_id
 ))
 AND (a.attempt_id IS NULL OR (t.kind = 'retry_scheduled' AND t.available_at <= ?)
      OR (t.attempt_id IS NULL AND a.lease_expires_at <= ?))
ORDER BY e.occurred_at, e.delivery_id LIMIT 1
"""


def prepare_hermes_delivery_schema(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(CREATE_HERMES_DELIVERY_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {HERMES_DELIVERY_SCHEMA_VERSION}")
        connection.commit()
    else:
        require_hermes_delivery_schema(connection)


def require_hermes_delivery_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (HERMES_DELIVERY_SCHEMA_VERSION,):
        raise UnsupportedHermesDeliverySchemaError("unsupported Hermes delivery schema")
