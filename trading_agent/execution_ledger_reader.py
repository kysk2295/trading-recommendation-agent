from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_agent.broker_order_evidence import BrokerOrderEvidence
from trading_agent.broker_order_projection import (
    BrokerOrderLedgerState,
    project_broker_order_state,
)
from trading_agent.execution_database import require_current_execution_schema
from trading_agent.execution_schema import (
    BrokerEventRow,
    IntentRow,
    StoredBrokerEvent,
    StoredIntent,
    stored_broker_event,
    stored_intent,
)
from trading_agent.paper_account_activity_store import (
    StoredPaperAccountActivity,
    read_paper_account_activities,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    IntentId,
)
from trading_agent.paper_stream_recovery import (
    StoredPaperRecoveryOrder,
    StoredPaperStreamRecovery,
    read_paper_recovery_orders,
    read_paper_stream_recoveries,
    recovery_completed_at,
)
from trading_agent.trade_update_receipts import (
    StoredTradeUpdateReceipt,
    TradeUpdateReceiptDisposition,
    TradeUpdateReceiptKey,
    TradeUpdateReceiptReason,
    pending_trade_update_receipt_keys,
    read_trade_update_receipt_dispositions,
    read_trade_update_receipts,
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
    pending_trade_update_receipt_keys: frozenset[TradeUpdateReceiptKey] = frozenset()
    unrecovered_trade_update_quarantine_keys: frozenset[TradeUpdateReceiptKey] = frozenset()


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
        pending_receipt_keys = pending_trade_update_receipt_keys(connection)
        receipt_dispositions = read_trade_update_receipt_dispositions(connection)
        raw_receipts = read_trade_update_receipts(connection)
        recoveries = read_paper_stream_recoveries(connection)
        recovery_orders = read_paper_recovery_orders(connection)
        account_activities = read_paper_account_activities(connection)
    intents = tuple(stored_intent(row) for row in intent_rows)
    broker_events = tuple(stored_broker_event(row) for row in broker_rows)
    trade_updates = tuple(stored_trade_update(row) for row in trade_rows)
    states = tuple(
        project_broker_order_state(
            intent,
            BrokerOrderEvidence(
                _broker_events_for(intent.intent_id, broker_events),
                _trade_updates_for(intent.intent_id, trade_updates),
                _recovery_orders_for(intent.intent_id, recovery_orders),
                _account_activities_for(
                    intent.intent_id,
                    recovery_orders,
                    account_activities,
                ),
            ),
        )
        for intent in intents
    )
    return ReconciliationLedger(
        intents=intents,
        unresolved_intent_ids=frozenset(state.intent_id for state in states if not state.terminal),
        account_fingerprint=(None if fingerprint_row is None else AccountFingerprint(fingerprint_row[0])),
        filled_intent_ids=frozenset(state.intent_id for state in states if state.has_fill_evidence),
        order_states=states,
        pending_trade_update_receipt_keys=pending_receipt_keys,
        unrecovered_trade_update_quarantine_keys=frozenset(
            disposition.receipt_key
            for disposition in receipt_dispositions
            if disposition.disposition is TradeUpdateReceiptDisposition.QUARANTINED
            and not _quarantine_recovered(
                disposition.receipt_key,
                disposition.reason,
                disposition.classified_at,
                disposition.recovery_high_water,
                raw_receipts,
                recoveries,
            )
        ),
    )


def trade_update_receipt_reasons(
    ledger: ReconciliationLedger,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if ledger.pending_trade_update_receipt_keys:
        reasons.append("분류되지 않은 trade update raw receipt가 있습니다")
    if ledger.unrecovered_trade_update_quarantine_keys:
        reasons.append("REST 복구되지 않은 trade update raw receipt 격리가 있습니다")
    return tuple(reasons)


def _quarantine_recovered(
    receipt_key: TradeUpdateReceiptKey,
    reason: TradeUpdateReceiptReason | None,
    classified_at: str,
    recovery_high_water: int,
    receipts: tuple[StoredTradeUpdateReceipt, ...],
    recoveries: tuple[StoredPaperStreamRecovery, ...],
) -> bool:
    if reason is None or reason is TradeUpdateReceiptReason.IMMUTABLE_CONFLICT:
        return False
    receipt = next(
        (candidate for candidate in receipts if candidate.receipt_key == receipt_key),
        None,
    )
    if receipt is None:
        return False
    classified = dt.datetime.fromisoformat(classified_at)
    if classified.tzinfo is None or classified.utcoffset() is None:
        return False
    return any(
        recovery.account_fingerprint == receipt.account_fingerprint
        and recovery.recovery_id > recovery_high_water
        and recovery_completed_at(recovery) > classified
        for recovery in recoveries
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


def _recovery_orders_for(
    intent_id: IntentId,
    orders: tuple[StoredPaperRecoveryOrder, ...],
) -> tuple[StoredPaperRecoveryOrder, ...]:
    return tuple(order for order in orders if order.order.client_order_id == intent_id)


def _account_activities_for(
    intent_id: IntentId,
    orders: tuple[StoredPaperRecoveryOrder, ...],
    activities: tuple[StoredPaperAccountActivity, ...],
) -> tuple[StoredPaperAccountActivity, ...]:
    broker_order_ids = frozenset(
        order.order.broker_order_id for order in orders if order.order.client_order_id == intent_id
    )
    return tuple(activity for activity in activities if activity.activity.broker_order_id in broker_order_ids)


def _reader_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    _ = connection.execute("PRAGMA query_only = ON")
    _ = connection.execute("PRAGMA foreign_keys = ON")
    require_current_execution_schema(connection, path)
    return connection
