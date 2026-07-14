from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal

from trading_agent.alpaca_trade_updates import AlpacaTradeUpdate
from trading_agent.execution_errors import (
    AccountBindingConflictError,
    InvalidTradeUpdateReceiptError,
    TradeUpdateConflictError,
    TradeUpdateOrderMismatchError,
    UnboundExecutionAccountError,
    UnexpectedBrokerOrderIdError,
    UnknownTradeUpdateIntentError,
)
from trading_agent.execution_schema import IntentRow, StoredIntent, stored_intent
from trading_agent.paper_execution_models import AccountFingerprint, IntentId
from trading_agent.trade_update_schema import (
    StoredTradeUpdate,
    TradeUpdateCoreValues,
    TradeUpdateRow,
    stored_trade_update,
    trade_update_core_values,
    trade_update_insert_values,
)


def append_trade_update(
    connection: sqlite3.Connection,
    update: AlpacaTradeUpdate,
    *,
    account_fingerprint: AccountFingerprint,
    connection_epoch: str,
    received_at: dt.datetime,
) -> bool:
    _require_receipt(connection_epoch, received_at)
    _require_bound_account(connection, account_fingerprint)
    intent = _stored_intent(connection, update.intent_id)
    _require_matching_order(update, intent)
    _require_linked_broker_order(connection, update)
    core_values = trade_update_core_values(update)
    existing: TradeUpdateCoreValues | None = connection.execute(
        """SELECT event_key, intent_id, occurred_at, event_type,
        broker_order_id, symbol, side, limit_price, time_in_force,
        extended_hours, broker_event_id, execution_id, order_status,
        order_quantity, cumulative_filled_quantity,
        cumulative_filled_avg_price, execution_quantity, execution_price,
        position_quantity, replaced_by_order_id, replaces_order_id, payload_json
        FROM trade_update_events WHERE event_key = ?""",
        (update.event_key,),
    ).fetchone()
    if existing is not None:
        if existing != core_values:
            raise TradeUpdateConflictError(update.event_key)
        return False
    _ = connection.execute(
        """INSERT INTO trade_update_events
        (event_key, intent_id, occurred_at, event_type, broker_order_id,
        symbol, side, limit_price, time_in_force, extended_hours,
        broker_event_id, execution_id, order_status, order_quantity,
        cumulative_filled_quantity, cumulative_filled_avg_price,
        execution_quantity, execution_price, position_quantity,
        replaced_by_order_id, replaces_order_id, payload_json,
        connection_epoch, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        trade_update_insert_values(
            update,
            connection_epoch,
            received_at.isoformat(),
        ),
    )
    connection.commit()
    return True


def read_trade_updates(
    connection: sqlite3.Connection,
    intent_id: IntentId,
) -> tuple[StoredTradeUpdate, ...]:
    rows: list[TradeUpdateRow] = connection.execute(
        """SELECT event_id, event_key, intent_id, occurred_at, event_type,
        broker_order_id, symbol, side, limit_price, time_in_force,
        extended_hours, broker_event_id, execution_id, order_status,
        order_quantity, cumulative_filled_quantity,
        cumulative_filled_avg_price, execution_quantity, execution_price,
        position_quantity, replaced_by_order_id, replaces_order_id, payload_json,
        connection_epoch, received_at FROM trade_update_events
        WHERE intent_id = ? ORDER BY event_id""",
        (intent_id,),
    ).fetchall()
    return tuple(stored_trade_update(row) for row in rows)


def _require_bound_account(
    connection: sqlite3.Connection,
    account_fingerprint: AccountFingerprint,
) -> None:
    row: tuple[str] | None = connection.execute(
        "SELECT account_fingerprint FROM account_binding WHERE binding_id = 1"
    ).fetchone()
    if row is None:
        raise UnboundExecutionAccountError
    if row[0] != account_fingerprint:
        raise AccountBindingConflictError


def _stored_intent(
    connection: sqlite3.Connection,
    intent_id: IntentId,
) -> StoredIntent:
    row: IntentRow | None = connection.execute(
        "SELECT * FROM order_intents WHERE intent_id = ?",
        (intent_id,),
    ).fetchone()
    if row is None:
        raise UnknownTradeUpdateIntentError(intent_id)
    return stored_intent(row)


def _require_matching_order(
    update: AlpacaTradeUpdate,
    intent: StoredIntent,
) -> None:
    mismatches: list[str] = []
    replacement_snapshot = (
        update.replaced_by_order_id is not None
        or update.replaces_order_id is not None
    )
    if update.symbol != intent.symbol:
        mismatches.append("symbol")
    if update.side != intent.side:
        mismatches.append("side")
    if not replacement_snapshot and update.order_quantity != Decimal(intent.quantity):
        mismatches.append("quantity")
    if not replacement_snapshot and update.limit_price != intent.entry_limit:
        mismatches.append("limit_price")
    if update.time_in_force != "day":
        mismatches.append("time_in_force")
    if update.extended_hours:
        mismatches.append("extended_hours")
    if mismatches:
        raise TradeUpdateOrderMismatchError(intent.intent_id, tuple(mismatches))


def _require_linked_broker_order(
    connection: sqlite3.Connection,
    update: AlpacaTradeUpdate,
) -> None:
    rows: list[tuple[str, str | None, str | None]] = connection.execute(
        """SELECT DISTINCT broker_order_id, replaced_by_order_id,
        replaces_order_id FROM trade_update_events WHERE intent_id = ?""",
        (update.intent_id,),
    ).fetchall()
    existing_ids = frozenset(row[0] for row in rows)
    if not existing_ids or update.broker_order_id in existing_ids:
        return
    direct_links = (update.replaced_by_order_id, update.replaces_order_id)
    if any(link in existing_ids for link in direct_links if link is not None):
        return
    if any(
        update.broker_order_id in (row[1], row[2])
        for row in rows
    ):
        return
    raise UnexpectedBrokerOrderIdError(
        update.intent_id,
        update.broker_order_id,
    )


def _require_receipt(connection_epoch: str, received_at: dt.datetime) -> None:
    if not connection_epoch:
        raise InvalidTradeUpdateReceiptError
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise InvalidTradeUpdateReceiptError
