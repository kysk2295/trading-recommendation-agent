from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.execution_schema import IntentRow, StoredIntent, stored_intent
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderEventType,
    IntentId,
)

TERMINAL_EVENT_VALUES = tuple(
    event.value
    for event in (
        BrokerOrderEventType.FILL,
        BrokerOrderEventType.REJECTED,
        BrokerOrderEventType.CANCELED,
        BrokerOrderEventType.EXPIRED,
    )
)


@dataclass(frozen=True, slots=True)
class ReconciliationLedger:
    intents: tuple[StoredIntent, ...]
    unresolved_intent_ids: frozenset[IntentId]
    account_fingerprint: AccountFingerprint | None
    filled_intent_ids: frozenset[IntentId] = frozenset()


def read_reconciliation_ledger(path: Path) -> ReconciliationLedger:
    if not path.is_file():
        return ReconciliationLedger((), frozenset(), None)
    with _reader_connection(path) as connection:
        _ = connection.execute("BEGIN")
        intent_rows: list[IntentRow] = connection.execute(
            "SELECT * FROM order_intents ORDER BY created_at, intent_id"
        ).fetchall()
        unresolved_rows: list[tuple[str]] = connection.execute(
            """SELECT intent.intent_id FROM order_intents AS intent
            LEFT JOIN broker_order_events AS event ON event.event_id = (
              SELECT MAX(candidate.event_id) FROM broker_order_events AS candidate
              WHERE candidate.intent_id = intent.intent_id
            )
            WHERE event.event_type IS NULL
               OR event.event_type NOT IN (?, ?, ?, ?)
            ORDER BY intent.intent_id""",
            TERMINAL_EVENT_VALUES,
        ).fetchall()
        filled_rows: list[tuple[str]] = connection.execute(
            """SELECT intent.intent_id FROM order_intents AS intent
            JOIN broker_order_events AS event ON event.event_id = (
              SELECT MAX(candidate.event_id) FROM broker_order_events AS candidate
              WHERE candidate.intent_id = intent.intent_id
            )
            WHERE event.event_type = ? ORDER BY intent.intent_id""",
            (BrokerOrderEventType.FILL.value,),
        ).fetchall()
        fingerprint_row: tuple[str] | None = connection.execute(
            "SELECT account_fingerprint FROM account_binding WHERE binding_id = 1"
        ).fetchone()
    return ReconciliationLedger(
        intents=tuple(stored_intent(row) for row in intent_rows),
        unresolved_intent_ids=frozenset(
            IntentId(row[0]) for row in unresolved_rows
        ),
        account_fingerprint=(
            None
            if fingerprint_row is None
            else AccountFingerprint(fingerprint_row[0])
        ),
        filled_intent_ids=frozenset(IntentId(row[0]) for row in filled_rows),
    )


def _reader_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    _ = connection.execute("PRAGMA query_only = ON")
    _ = connection.execute("PRAGMA foreign_keys = ON")
    return connection
