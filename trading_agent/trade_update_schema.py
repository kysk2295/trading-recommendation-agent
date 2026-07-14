from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from trading_agent.alpaca_trade_updates import AlpacaTradeUpdate
from trading_agent.paper_execution_models import (
    BrokerEventKey,
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
)

CREATE_TRADE_UPDATE_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS trade_update_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  intent_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  broker_order_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  limit_price TEXT,
  time_in_force TEXT NOT NULL,
  extended_hours INTEGER NOT NULL CHECK(extended_hours IN (0, 1)),
  broker_event_id TEXT,
  execution_id TEXT,
  order_status TEXT NOT NULL,
  order_quantity TEXT NOT NULL,
  cumulative_filled_quantity TEXT NOT NULL,
  cumulative_filled_avg_price TEXT,
  execution_quantity TEXT,
  execution_price TEXT,
  position_quantity TEXT,
  replaced_by_order_id TEXT,
  replaces_order_id TEXT,
  payload_json TEXT NOT NULL,
  connection_epoch TEXT NOT NULL,
  received_at TEXT NOT NULL,
  FOREIGN KEY(intent_id) REFERENCES order_intents(intent_id),
  CHECK(CAST(order_quantity AS REAL) > 0),
  CHECK(CAST(cumulative_filled_quantity AS REAL) >= 0),
  CHECK(execution_quantity IS NULL OR CAST(execution_quantity AS REAL) > 0),
  CHECK(execution_price IS NULL OR CAST(execution_price AS REAL) > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS trade_update_execution_id_unique
ON trade_update_events(execution_id) WHERE execution_id IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS trade_update_events_no_update
BEFORE UPDATE ON trade_update_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_update_events_no_delete
BEFORE DELETE ON trade_update_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

type TradeUpdateCoreValues = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str,
    int,
    str | None,
    str | None,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
]
type TradeUpdateInsertValues = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str,
    int,
    str | None,
    str | None,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
    str,
    str,
]
type TradeUpdateRow = tuple[
    int,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str,
    int,
    str | None,
    str | None,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str,
    str,
    str,
]


@dataclass(frozen=True, slots=True)
class StoredTradeUpdate:
    event_id: int
    event_key: BrokerEventKey
    intent_id: IntentId
    occurred_at: str
    event_type: BrokerOrderEventType
    broker_order_id: BrokerOrderId
    symbol: str
    side: PaperOrderSide
    limit_price: Decimal | None
    time_in_force: str
    extended_hours: bool
    broker_event_id: str | None
    execution_id: str | None
    order_status: str
    order_quantity: Decimal
    cumulative_filled_quantity: Decimal
    cumulative_filled_average_price: Decimal | None
    execution_quantity: Decimal | None
    execution_price: Decimal | None
    position_quantity: Decimal | None
    replaced_by_order_id: BrokerOrderId | None
    replaces_order_id: BrokerOrderId | None
    payload_json: str
    connection_epoch: str
    received_at: str


def trade_update_core_values(update: AlpacaTradeUpdate) -> TradeUpdateCoreValues:
    return (
        update.event_key,
        update.intent_id,
        update.occurred_at.isoformat(),
        update.event_type.value,
        update.broker_order_id,
        update.symbol,
        update.side.value,
        _decimal_text(update.limit_price),
        update.time_in_force,
        int(update.extended_hours),
        update.broker_event_id,
        update.execution_id,
        update.order_status,
        str(update.order_quantity),
        str(update.cumulative_filled_quantity),
        _decimal_text(update.cumulative_filled_average_price),
        _decimal_text(update.execution_quantity),
        _decimal_text(update.execution_price),
        _decimal_text(update.position_quantity),
        update.replaced_by_order_id,
        update.replaces_order_id,
        update.payload_json,
    )


def trade_update_insert_values(
    update: AlpacaTradeUpdate,
    connection_epoch: str,
    received_at: str,
) -> TradeUpdateInsertValues:
    return (*trade_update_core_values(update), connection_epoch, received_at)


def stored_trade_update(row: TradeUpdateRow) -> StoredTradeUpdate:
    return StoredTradeUpdate(
        event_id=row[0],
        event_key=BrokerEventKey(row[1]),
        intent_id=IntentId(row[2]),
        occurred_at=row[3],
        event_type=BrokerOrderEventType(row[4]),
        broker_order_id=BrokerOrderId(row[5]),
        symbol=row[6],
        side=PaperOrderSide(row[7]),
        limit_price=_optional_decimal(row[8]),
        time_in_force=row[9],
        extended_hours=bool(row[10]),
        broker_event_id=row[11],
        execution_id=row[12],
        order_status=row[13],
        order_quantity=Decimal(row[14]),
        cumulative_filled_quantity=Decimal(row[15]),
        cumulative_filled_average_price=_optional_decimal(row[16]),
        execution_quantity=_optional_decimal(row[17]),
        execution_price=_optional_decimal(row[18]),
        position_quantity=_optional_decimal(row[19]),
        replaced_by_order_id=(
            None if row[20] is None else BrokerOrderId(row[20])
        ),
        replaces_order_id=None if row[21] is None else BrokerOrderId(row[21]),
        payload_json=row[22],
        connection_epoch=row[23],
        received_at=row[24],
    )


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _optional_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)
