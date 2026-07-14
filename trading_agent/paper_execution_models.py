from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import NewType

IntentId = NewType("IntentId", str)
BrokerOrderId = NewType("BrokerOrderId", str)


class PaperOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class BrokerOrderEventType(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL_FILL = "partial_fill"
    FILL = "fill"
    CANCELED = "canceled"
    EXPIRED = "expired"


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


@dataclass(frozen=True, slots=True)
class PaperOrderSnapshot:
    broker_order_id: BrokerOrderId
    client_order_id: IntentId
    symbol: str
    side: PaperOrderSide
    status: str
    quantity: Decimal
    filled_quantity: Decimal


@dataclass(frozen=True, slots=True)
class PaperPositionSnapshot:
    symbol: str
    quantity: Decimal
    market_value: Decimal
