from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self, override

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from trading_agent.paper_execution_models import (
    BrokerEventKey,
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
)

type JsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list[JsonValue]
    | dict[str, JsonValue]
)


class _TradeUpdateValidationError(ValueError):
    pass


class AlpacaTradeUpdateProtocolError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper trade_updates 형식이 올바르지 않습니다"


class AlpacaTradeUpdateEventType(StrEnum):
    NEW = "new"
    FILL = "fill"
    PARTIAL_FILL = "partial_fill"
    CANCELED = "canceled"
    EXPIRED = "expired"
    DONE_FOR_DAY = "done_for_day"
    REPLACED = "replaced"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING_NEW = "pending_new"
    STOPPED = "stopped"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    CALCULATED = "calculated"
    SUSPENDED = "suspended"
    ORDER_REPLACE_REJECTED = "order_replace_rejected"
    ORDER_CANCEL_REJECTED = "order_cancel_rejected"


EXPECTED_ORDER_STATUS: Final = {
    AlpacaTradeUpdateEventType.FILL: "filled",
    AlpacaTradeUpdateEventType.PARTIAL_FILL: "partially_filled",
    AlpacaTradeUpdateEventType.CANCELED: "canceled",
    AlpacaTradeUpdateEventType.EXPIRED: "expired",
    AlpacaTradeUpdateEventType.REJECTED: "rejected",
    AlpacaTradeUpdateEventType.REPLACED: "replaced",
}


class _AlpacaTradeUpdateOrder(BaseModel):
    model_config = ConfigDict(frozen=True, str_min_length=1)

    id: str
    client_order_id: str
    asset_class: str
    symbol: str
    side: PaperOrderSide
    status: str
    qty: Decimal
    filled_qty: Decimal
    filled_avg_price: Decimal | None
    limit_price: Decimal | None
    time_in_force: str
    extended_hours: bool
    updated_at: AwareDatetime
    replaced_by: str | None = None
    replaces: str | None = None

    @model_validator(mode="after")
    def validate_equity_order(self) -> Self:
        if self.asset_class != "us_equity":
            raise _TradeUpdateValidationError("only US equities are permitted")
        if self.qty <= 0 or self.filled_qty < 0 or self.filled_qty > self.qty:
            raise _TradeUpdateValidationError("invalid order quantities")
        if self.filled_avg_price is not None and self.filled_avg_price <= 0:
            raise _TradeUpdateValidationError("invalid average fill price")
        return self


class _AlpacaTradeUpdateData(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: AlpacaTradeUpdateEventType
    order: _AlpacaTradeUpdateOrder
    event_id: str | int | None = None
    execution_id: str | None = Field(default=None, min_length=1)
    timestamp: AwareDatetime | None = None
    price: Decimal | None = None
    qty: Decimal | None = None
    position_qty: Decimal | None = None

    @model_validator(mode="after")
    def validate_execution_fields(self) -> Self:
        is_execution = self.event in (
            AlpacaTradeUpdateEventType.FILL,
            AlpacaTradeUpdateEventType.PARTIAL_FILL,
        )
        execution_values = (self.timestamp, self.price, self.qty, self.position_qty)
        if is_execution and any(value is None for value in execution_values):
            raise _TradeUpdateValidationError("fill event is missing execution fields")
        if self.price is not None and self.price <= 0:
            raise _TradeUpdateValidationError("invalid execution price")
        if self.qty is not None and self.qty <= 0:
            raise _TradeUpdateValidationError("invalid execution quantity")
        if self.event is AlpacaTradeUpdateEventType.PARTIAL_FILL and not (
            Decimal(0) < self.order.filled_qty < self.order.qty
        ):
            raise _TradeUpdateValidationError("invalid cumulative partial fill")
        if (
            self.event is AlpacaTradeUpdateEventType.FILL
            and self.order.filled_qty != self.order.qty
        ):
            raise _TradeUpdateValidationError("full fill does not match order quantity")
        expected_status = EXPECTED_ORDER_STATUS.get(self.event)
        if expected_status is not None and self.order.status != expected_status:
            raise _TradeUpdateValidationError("event and order status do not match")
        return self


class _AlpacaTradeUpdateEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream: str
    data: _AlpacaTradeUpdateData

    @model_validator(mode="after")
    def validate_stream(self) -> Self:
        if self.stream != "trade_updates":
            raise _TradeUpdateValidationError("unexpected stream")
        return self


@dataclass(frozen=True, slots=True)
class AlpacaTradeUpdate:
    event_key: BrokerEventKey
    intent_id: IntentId
    occurred_at: dt.datetime
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


ENVELOPE_ADAPTER: Final = TypeAdapter(_AlpacaTradeUpdateEnvelope)
JSON_OBJECT_ADAPTER: Final = TypeAdapter(dict[str, JsonValue])


def parse_alpaca_trade_update(raw: str | bytes) -> AlpacaTradeUpdate:
    try:
        envelope = ENVELOPE_ADAPTER.validate_json(raw)
        raw_object = JSON_OBJECT_ADAPTER.validate_json(raw)
    except ValidationError as error:
        raise AlpacaTradeUpdateProtocolError from error
    data = envelope.data
    order = data.order
    event_id = None if data.event_id is None else str(data.event_id)
    payload_json = json.dumps(
        raw_object,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return AlpacaTradeUpdate(
        event_key=_event_key(data, event_id, payload_json),
        intent_id=IntentId(order.client_order_id),
        occurred_at=data.timestamp or order.updated_at,
        event_type=BrokerOrderEventType(data.event.value),
        broker_order_id=BrokerOrderId(order.id),
        symbol=order.symbol,
        side=order.side,
        limit_price=order.limit_price,
        time_in_force=order.time_in_force,
        extended_hours=order.extended_hours,
        broker_event_id=event_id,
        execution_id=data.execution_id,
        order_status=order.status,
        order_quantity=order.qty,
        cumulative_filled_quantity=order.filled_qty,
        cumulative_filled_average_price=order.filled_avg_price,
        execution_quantity=data.qty,
        execution_price=data.price,
        position_quantity=data.position_qty,
        replaced_by_order_id=(
            None if order.replaced_by is None else BrokerOrderId(order.replaced_by)
        ),
        replaces_order_id=(
            None if order.replaces is None else BrokerOrderId(order.replaces)
        ),
        payload_json=payload_json,
    )


def _event_key(
    data: _AlpacaTradeUpdateData,
    event_id: str | None,
    payload_json: str,
) -> BrokerEventKey:
    if data.execution_id is not None:
        return BrokerEventKey(f"alpaca:execution:{data.execution_id}")
    if event_id is not None:
        return BrokerEventKey(f"alpaca:event:{event_id}")
    digest = hashlib.sha256(payload_json.encode()).hexdigest()
    return BrokerEventKey(f"alpaca:state:{digest}")
