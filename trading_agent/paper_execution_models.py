from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import NewType

IntentId = NewType("IntentId", str)
BrokerOrderId = NewType("BrokerOrderId", str)
BrokerEventKey = NewType("BrokerEventKey", str)
AccountFingerprint = NewType("AccountFingerprint", str)


class PaperOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class BrokerOrderEventType(StrEnum):
    SUBMITTED = "submitted"
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING_NEW = "pending_new"
    STOPPED = "stopped"
    PENDING_CANCEL = "pending_cancel"
    PARTIAL_FILL = "partial_fill"
    FILL = "fill"
    CANCELED = "canceled"
    EXPIRED = "expired"
    DONE_FOR_DAY = "done_for_day"
    PENDING_REPLACE = "pending_replace"
    REPLACED = "replaced"
    CALCULATED = "calculated"
    SUSPENDED = "suspended"
    ORDER_REPLACE_REJECTED = "order_replace_rejected"
    ORDER_CANCEL_REJECTED = "order_cancel_rejected"


@dataclass(frozen=True, slots=True)
class PaperOrderIntent:
    intent_id: IntentId
    strategy_id: str
    strategy_version: str
    symbol: str
    created_at: dt.datetime
    side: PaperOrderSide
    entry_limit: float
    stop: float
    target_1r: float
    target_2r: float


@dataclass(frozen=True, slots=True)
class SizedPaperOrder:
    intent: PaperOrderIntent
    quantity: int
    risk_per_share: float
    planned_risk: float
    notional: float


@dataclass(frozen=True, slots=True)
class PaperAccountSnapshot:
    observed_at: dt.datetime
    status: str
    trading_blocked: bool
    equity: Decimal
    last_equity: Decimal
    buying_power: Decimal
    account_fingerprint: AccountFingerprint = field(repr=False)


@dataclass(frozen=True, slots=True)
class PaperMarketClockSnapshot:
    observed_at: dt.datetime
    market_timestamp: dt.datetime
    is_open: bool
    next_open: dt.datetime
    next_close: dt.datetime


@dataclass(frozen=True, slots=True)
class PaperOrderSnapshot:
    broker_order_id: BrokerOrderId
    client_order_id: IntentId
    symbol: str
    side: PaperOrderSide
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    time_in_force: str
    extended_hours: bool
    filled_average_price: Decimal | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    submitted_at: dt.datetime | None = None
    filled_at: dt.datetime | None = None
    canceled_at: dt.datetime | None = None
    failed_at: dt.datetime | None = None
    replaced_at: dt.datetime | None = None
    replaced_by_order_id: BrokerOrderId | None = None
    replaces_order_id: BrokerOrderId | None = None


@dataclass(frozen=True, slots=True)
class PaperPositionSnapshot:
    symbol: str
    quantity: Decimal
    market_value: Decimal


@dataclass(frozen=True, slots=True)
class PaperBrokerState:
    account: PaperAccountSnapshot
    open_orders: tuple[PaperOrderSnapshot, ...]
    positions: tuple[PaperPositionSnapshot, ...]


@dataclass(frozen=True, slots=True)
class BrokerOrderEvent:
    event_key: BrokerEventKey
    intent_id: IntentId
    occurred_at: dt.datetime
    event_type: BrokerOrderEventType
    broker_order_id: BrokerOrderId
    payload_json: str
