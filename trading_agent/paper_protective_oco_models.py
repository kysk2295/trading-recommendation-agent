from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Literal, NewType

from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
    PaperOrderSnapshot,
)

ProtectiveOcoClientOrderId = NewType("ProtectiveOcoClientOrderId", str)


@dataclass(frozen=True, slots=True)
class ProtectiveOcoExitPlan:
    client_order_id: ProtectiveOcoClientOrderId
    parent_intent_id: IntentId
    symbol: str
    side: PaperOrderSide
    quantity: int
    take_profit_limit: Decimal
    stop_price: Decimal
    order_class: Literal["oco"] = field(init=False, default="oco")
    order_type: Literal["limit"] = field(init=False, default="limit")
    time_in_force: Literal["day"] = field(init=False, default="day")
    extended_hours: Literal[False] = field(init=False, default=False)


class ProtectiveOcoLegKind(StrEnum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"


class ProtectiveOcoOrderType(StrEnum):
    LIMIT = "limit"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class ProtectiveOcoLegSnapshot:
    kind: ProtectiveOcoLegKind
    broker_order_id: BrokerOrderId
    client_order_id: str
    symbol: str
    side: PaperOrderSide
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    order_type: ProtectiveOcoOrderType
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: str
    extended_hours: bool


@dataclass(frozen=True, slots=True)
class ProtectiveOcoSnapshot:
    observed_at: dt.datetime
    take_profit: ProtectiveOcoLegSnapshot
    stop_loss: ProtectiveOcoLegSnapshot


@dataclass(frozen=True, slots=True)
class PaperOpenOrderInventory:
    entry_orders: tuple[PaperOrderSnapshot, ...]
    protective_ocos: tuple[ProtectiveOcoSnapshot, ...]
