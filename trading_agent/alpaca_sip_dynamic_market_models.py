from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeMessage,
)

_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_VENUE: Final = re.compile(r"^[A-Z0-9]{1,4}$")
_CONDITION: Final = re.compile(r"^[ -~]{1,8}$")
_INSTRUMENT = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AlpacaSipDynamicMarketError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic market frame is invalid"


class AlpacaSipDynamicMarketKind(StrEnum):
    QUOTE = "quote"
    TRADE = "trade"
    CORRECTION = "correction"
    CANCEL = "cancel"


class AlpacaSipQuoteMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["q"] = Field(alias="T")
    symbol: str = Field(alias="S")
    ask_exchange: str = Field(alias="ax")
    ask_price: Decimal = Field(alias="ap")
    ask_size: int = Field(alias="as")
    bid_exchange: str = Field(alias="bx")
    bid_price: Decimal = Field(alias="bp")
    bid_size: int = Field(alias="bs")
    conditions: tuple[str, ...] = Field(alias="c")
    timestamp: AwareDatetime = Field(alias="t")
    tape: str = Field(alias="z")

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or _VENUE.fullmatch(self.ask_exchange) is None
            or _VENUE.fullmatch(self.bid_exchange) is None
            or self.ask_price <= 0
            or self.bid_price <= 0
            or self.ask_size < 0
            or self.bid_size < 0
            or self.tape not in {"A", "B", "C"}
            or self.conditions != tuple(dict.fromkeys(self.conditions))
            or any(_CONDITION.fullmatch(value) is None for value in self.conditions)
        ):
            raise AlpacaSipDynamicMarketError
        return self


type AlpacaSipDynamicWireMessage = (
    AlpacaSipQuoteMessage | AlpacaSipTradeMessage | AlpacaSipTradeCorrectionMessage | AlpacaSipTradeCancelMessage
)
_ADAPTER: Final = TypeAdapter(tuple[AlpacaSipDynamicWireMessage, ...])


@dataclass(frozen=True, slots=True)
class AlpacaSipProjectedMarketMessage:
    event_id: str
    content_sha256: str
    raw_receipt_id: str
    plan_id: str
    connection_epoch: str
    sequence: int
    message_index: int
    market_date: dt.date
    instrument_id: str
    symbol: str
    kind: AlpacaSipDynamicMarketKind
    event_time: dt.datetime
    received_at: dt.datetime
    payload: bytes

    def __post_init__(self) -> None:
        if (
            any(
                _HEX64.fullmatch(value) is None
                for value in (self.event_id, self.content_sha256, self.raw_receipt_id, self.plan_id)
            )
            or _EPOCH.fullmatch(self.connection_epoch) is None
            or self.sequence < 4
            or self.message_index < 0
            or type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or _INSTRUMENT.fullmatch(self.instrument_id) is None
            or _SYMBOL.fullmatch(self.symbol) is None
            or type(self.kind) is not AlpacaSipDynamicMarketKind
            or not _aware(self.event_time)
            or not _aware(self.received_at)
            or self.event_time > self.received_at
            or type(self.payload) is not bytes
            or not self.payload
        ):
            raise AlpacaSipDynamicMarketError


def parse_alpaca_sip_dynamic_market_frame(payload: bytes) -> tuple[AlpacaSipDynamicWireMessage, ...]:
    try:
        if type(payload) is not bytes or not payload:
            raise AlpacaSipDynamicMarketError
        messages = _ADAPTER.validate_json(payload)
        if not messages:
            raise AlpacaSipDynamicMarketError
        return messages
    except (ValidationError, ValueError):
        raise AlpacaSipDynamicMarketError from None


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicMarketError",
    "AlpacaSipDynamicMarketKind",
    "AlpacaSipDynamicWireMessage",
    "AlpacaSipProjectedMarketMessage",
    "AlpacaSipQuoteMessage",
    "parse_alpaca_sip_dynamic_market_frame",
)
