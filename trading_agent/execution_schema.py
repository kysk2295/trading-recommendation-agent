from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerEventKey,
    BrokerOrderEvent,
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)

SCHEMA_VERSION: Final = 3
CREATE_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS account_binding (
  binding_id INTEGER PRIMARY KEY CHECK(binding_id = 1),
  account_fingerprint TEXT NOT NULL,
  bound_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_intents (
  intent_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL,
  symbol TEXT NOT NULL,
  created_at TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  entry_limit TEXT NOT NULL,
  stop TEXT NOT NULL,
  target_1r TEXT NOT NULL,
  target_2r TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity > 0)
);
CREATE TABLE IF NOT EXISTS broker_order_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  intent_id TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  event_type TEXT NOT NULL,
  broker_order_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(intent_id) REFERENCES order_intents(intent_id)
);
CREATE TRIGGER IF NOT EXISTS order_intents_no_update
BEFORE UPDATE ON order_intents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_intents_no_delete
BEFORE DELETE ON order_intents BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS broker_events_no_update
BEFORE UPDATE ON broker_order_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS broker_events_no_delete
BEFORE DELETE ON broker_order_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS account_binding_no_update
BEFORE UPDATE ON account_binding BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS account_binding_no_delete
BEFORE DELETE ON account_binding BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

type IntentRow = tuple[str, str, str, str, str, str, str, str, str, str, int]
type BrokerEventRow = tuple[int, str, str, str, str, str, str]
type BrokerEventValues = tuple[str, str, str, str, str, str]
type AccountBindingRow = tuple[str, str]


@dataclass(frozen=True, slots=True)
class StoredAccountBinding:
    account_fingerprint: AccountFingerprint
    bound_at: str


@dataclass(frozen=True, slots=True)
class StoredIntent:
    intent_id: IntentId
    strategy_id: str
    strategy_version: str
    symbol: str
    created_at: str
    side: PaperOrderSide
    entry_limit: Decimal
    stop: Decimal
    target_1r: Decimal
    target_2r: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class StoredBrokerEvent:
    event_id: int
    event_key: BrokerEventKey
    intent_id: IntentId
    occurred_at: str
    event_type: BrokerOrderEventType
    broker_order_id: BrokerOrderId
    payload_json: str


def intent_values(intent: PaperOrderIntent, quantity: int) -> IntentRow:
    return (
        intent.intent_id,
        intent.strategy_id,
        intent.strategy_version,
        intent.symbol,
        intent.created_at.isoformat(),
        intent.side.value,
        str(intent.entry_limit),
        str(intent.stop),
        str(intent.target_1r),
        str(intent.target_2r),
        quantity,
    )


def broker_event_values(event: BrokerOrderEvent) -> BrokerEventValues:
    return (
        event.event_key,
        event.intent_id,
        event.occurred_at.isoformat(),
        event.event_type.value,
        event.broker_order_id,
        event.payload_json,
    )


def stored_intent(row: IntentRow) -> StoredIntent:
    return StoredIntent(
        IntentId(row[0]),
        row[1],
        row[2],
        row[3],
        row[4],
        PaperOrderSide(row[5]),
        Decimal(row[6]),
        Decimal(row[7]),
        Decimal(row[8]),
        Decimal(row[9]),
        row[10],
    )


def stored_broker_event(row: BrokerEventRow) -> StoredBrokerEvent:
    return StoredBrokerEvent(
        row[0],
        BrokerEventKey(row[1]),
        IntentId(row[2]),
        row[3],
        BrokerOrderEventType(row[4]),
        BrokerOrderId(row[5]),
        row[6],
    )
