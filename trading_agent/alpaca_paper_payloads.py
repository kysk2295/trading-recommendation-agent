from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, model_validator

from trading_agent.paper_execution_models import (
    PaperOrderSide,
    PaperTradeActivityType,
)


class AlpacaPaperAccountPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    account_number: str
    status: str
    trading_blocked: bool
    equity: Decimal
    last_equity: Decimal
    buying_power: Decimal


class AlpacaPaperClockPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: AwareDatetime
    is_open: bool
    next_open: AwareDatetime
    next_close: AwareDatetime


class AlpacaPaperOrderPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    client_order_id: str
    symbol: str
    side: PaperOrderSide
    status: str
    qty: Decimal
    filled_qty: Decimal
    filled_avg_price: Decimal | None = None
    limit_price: Decimal | None
    time_in_force: str
    extended_hours: bool
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    submitted_at: AwareDatetime | None = None
    filled_at: AwareDatetime | None = None
    canceled_at: AwareDatetime | None = None
    failed_at: AwareDatetime | None = None
    replaced_at: AwareDatetime | None = None
    replaced_by: str | None = None
    replaces: str | None = None

    @model_validator(mode="after")
    def validate_order_state(self) -> Self:
        required_text = (
            self.id,
            self.client_order_id,
            self.symbol,
            self.status,
            self.time_in_force,
        )
        prices = (self.filled_avg_price, self.limit_price)
        if (
            any(not value or value.strip() != value for value in required_text)
            or not self.qty.is_finite()
            or self.qty <= 0
            or not self.filled_qty.is_finite()
            or self.filled_qty < 0
            or self.filled_qty > self.qty
            or any(price is not None and (not price.is_finite() or price <= 0) for price in prices)
            or (self.filled_qty > 0 and self.filled_avg_price is None)
            or (self.status == "filled" and self.filled_qty != self.qty)
            or (self.status == "partially_filled" and not Decimal(0) < self.filled_qty < self.qty)
        ):
            raise ValueError
        return self


class AlpacaPaperPositionPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    qty: Decimal
    market_value: Decimal


class AlpacaPaperTradeActivityPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_type: Literal["FILL"]
    cum_qty: Decimal
    id: str
    leaves_qty: Decimal
    price: Decimal
    qty: Decimal
    side: PaperOrderSide
    symbol: str
    transaction_time: AwareDatetime
    order_id: str
    type: PaperTradeActivityType

    @model_validator(mode="after")
    def validate_trade_activity(self) -> Self:
        required_text = (self.id, self.symbol, self.order_id)
        decimals = (self.cum_qty, self.leaves_qty, self.price, self.qty)
        if (
            any(not value or value.strip() != value for value in required_text)
            or any(not value.is_finite() for value in decimals)
            or self.cum_qty <= 0
            or self.leaves_qty < 0
            or self.price <= 0
            or self.qty <= 0
            or self.qty > self.cum_qty
            or (self.type is PaperTradeActivityType.FILL and self.leaves_qty != 0)
            or (self.type is PaperTradeActivityType.PARTIAL_FILL and self.leaves_qty <= 0)
        ):
            raise ValueError
        return self


ACCOUNT_ADAPTER: Final = TypeAdapter(AlpacaPaperAccountPayload)
CLOCK_ADAPTER: Final = TypeAdapter(AlpacaPaperClockPayload)
ORDER_ADAPTER: Final = TypeAdapter(AlpacaPaperOrderPayload)
ORDERS_ADAPTER: Final = TypeAdapter(tuple[AlpacaPaperOrderPayload, ...])
POSITIONS_ADAPTER: Final = TypeAdapter(tuple[AlpacaPaperPositionPayload, ...])
TRADE_ACTIVITIES_ADAPTER: Final = TypeAdapter(tuple[AlpacaPaperTradeActivityPayload, ...])
