from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.broker_order_projection import (
    BrokerOrderLedgerState,
    project_broker_order_state,
)
from trading_agent.execution_schema import (
    BrokerEventRow,
    IntentRow,
    StoredBrokerEvent,
    StoredIntent,
    stored_broker_event,
    stored_intent,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    IntentId,
)
from trading_agent.trade_update_schema import (
    StoredTradeUpdate,
    TradeUpdateRow,
    stored_trade_update,
)


@dataclass(frozen=True, slots=True)
class ReconciliationLedger:
    intents: tuple[StoredIntent, ...]
    unresolved_intent_ids: frozenset[IntentId]
    account_fingerprint: AccountFingerprint | None
    filled_intent_ids: frozenset[IntentId] = frozenset()
    order_states: tuple[BrokerOrderLedgerState, ...] = ()


def read_reconciliation_ledger(path: Path) -> ReconciliationLedger:
    if not path.is_file():
        return ReconciliationLedger((), frozenset(), None)
    with _reader_connection(path) as connection:
        _ = connection.execute("BEGIN")
        intent_rows: list[IntentRow] = connection.execute(
            "SELECT * FROM order_intents ORDER BY created_at, intent_id"
        ).fetchall()
        broker_rows: list[BrokerEventRow] = connection.execute(
            """SELECT event_id, event_key, intent_id, occurred_at, event_type,
            broker_order_id, payload_json FROM broker_order_events ORDER BY event_id"""
        ).fetchall()
        trade_rows: list[TradeUpdateRow] = connection.execute(
            """SELECT event_id, event_key, intent_id, occurred_at, event_type,
            broker_order_id, symbol, side, limit_price, time_in_force,
            extended_hours, broker_event_id, execution_id, order_status,
            order_quantity, cumulative_filled_quantity,
            cumulative_filled_avg_price, execution_quantity, execution_price,
            position_quantity, replaced_by_order_id, replaces_order_id,
            payload_json, connection_epoch, received_at
            FROM trade_update_events ORDER BY event_id"""
        ).fetchall()
        fingerprint_row: tuple[str] | None = connection.execute(
            "SELECT account_fingerprint FROM account_binding WHERE binding_id = 1"
        ).fetchone()
    intents = tuple(stored_intent(row) for row in intent_rows)
    broker_events = tuple(stored_broker_event(row) for row in broker_rows)
    trade_updates = tuple(stored_trade_update(row) for row in trade_rows)
    states = tuple(
        project_broker_order_state(
            intent,
            _broker_events_for(intent.intent_id, broker_events),
            _trade_updates_for(intent.intent_id, trade_updates),
        )
        for intent in intents
    )
    return ReconciliationLedger(
        intents=intents,
        unresolved_intent_ids=frozenset(
            state.intent_id for state in states if not state.terminal
        ),
        account_fingerprint=(
            None
            if fingerprint_row is None
            else AccountFingerprint(fingerprint_row[0])
        ),
        filled_intent_ids=frozenset(
            state.intent_id for state in states if state.has_fill_evidence
        ),
        order_states=states,
    )


def _broker_events_for(
    intent_id: IntentId,
    events: tuple[StoredBrokerEvent, ...],
) -> tuple[StoredBrokerEvent, ...]:
    return tuple(event for event in events if event.intent_id == intent_id)


def _trade_updates_for(
    intent_id: IntentId,
    events: tuple[StoredTradeUpdate, ...],
) -> tuple[StoredTradeUpdate, ...]:
    return tuple(event for event in events if event.intent_id == intent_id)


def _reader_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    _ = connection.execute("PRAGMA query_only = ON")
    _ = connection.execute("PRAGMA foreign_keys = ON")
    return connection
