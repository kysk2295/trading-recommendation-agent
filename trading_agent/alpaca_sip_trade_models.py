from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_VENUE: Final = re.compile(r"^[A-Z0-9]{1,4}$")
_CONDITION: Final = re.compile(r"^[ -~]{1,8}$")


class AlpacaSipTradeHistoryError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade history could not be stored"


class AlpacaSipTradeParseError(AlpacaSipTradeHistoryError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade frame could not be parsed"


class AlpacaSipTradeProjectionError(AlpacaSipTradeHistoryError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade history could not be projected"


class AlpacaSipTradeMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["t"] = Field(alias="T")
    symbol: str = Field(alias="S")
    trade_id: int = Field(alias="i")
    exchange: str = Field(alias="x")
    price: Decimal = Field(alias="p")
    size: int = Field(alias="s")
    conditions: tuple[str, ...] = Field(alias="c")
    timestamp: AwareDatetime = Field(alias="t")
    tape: str = Field(alias="z")

    @model_validator(mode="after")
    def validate_trade(self) -> Self:
        if not _common_is_valid(self.symbol, self.exchange, self.tape, self.conditions):
            raise AlpacaSipTradeParseError
        if self.trade_id <= 0 or self.price <= 0 or self.size <= 0:
            raise AlpacaSipTradeParseError
        return self


class AlpacaSipTradeCorrectionMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["c"] = Field(alias="T")
    symbol: str = Field(alias="S")
    exchange: str = Field(alias="x")
    original_trade_id: int = Field(alias="oi")
    original_price: Decimal = Field(alias="op")
    original_size: int = Field(alias="os")
    original_conditions: tuple[str, ...] = Field(alias="oc")
    corrected_trade_id: int = Field(alias="ci")
    corrected_price: Decimal = Field(alias="cp")
    corrected_size: int = Field(alias="cs")
    corrected_conditions: tuple[str, ...] = Field(alias="cc")
    timestamp: AwareDatetime = Field(alias="t")
    tape: str = Field(alias="z")

    @model_validator(mode="after")
    def validate_correction(self) -> Self:
        conditions_valid = _conditions_are_valid(self.original_conditions) and _conditions_are_valid(
            self.corrected_conditions
        )
        if not _common_is_valid(self.symbol, self.exchange, self.tape, ()) or not conditions_valid:
            raise AlpacaSipTradeParseError
        if (
            self.original_trade_id <= 0
            or self.corrected_trade_id <= 0
            or self.original_trade_id == self.corrected_trade_id
            or self.original_price <= 0
            or self.corrected_price <= 0
            or self.original_size <= 0
            or self.corrected_size <= 0
        ):
            raise AlpacaSipTradeParseError
        return self


class AlpacaSipTradeCancelMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["x"] = Field(alias="T")
    symbol: str = Field(alias="S")
    trade_id: int = Field(alias="i")
    exchange: str = Field(alias="x")
    price: Decimal = Field(alias="p")
    size: int = Field(alias="s")
    action: Literal["C", "E"] = Field(alias="a")
    timestamp: AwareDatetime = Field(alias="t")
    tape: str = Field(alias="z")

    @model_validator(mode="after")
    def validate_cancel(self) -> Self:
        if not _common_is_valid(self.symbol, self.exchange, self.tape, ()):
            raise AlpacaSipTradeParseError
        if self.trade_id <= 0 or self.price <= 0 or self.size <= 0:
            raise AlpacaSipTradeParseError
        return self


type AlpacaSipTradeWireMessage = AlpacaSipTradeMessage | AlpacaSipTradeCorrectionMessage | AlpacaSipTradeCancelMessage
_FRAME_ADAPTER: Final = TypeAdapter(tuple[AlpacaSipTradeWireMessage, ...])


@dataclass(frozen=True, slots=True)
class AlpacaSipReceivedTradeFrame:
    market_date: dt.date
    received_at: dt.datetime
    payload: bytes

    def __post_init__(self) -> None:
        aware = self.received_at.tzinfo is not None and self.received_at.utcoffset() is not None
        if (
            type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or type(self.received_at) is not dt.datetime
            or not aware
            or type(self.payload) is not bytes
            or not self.payload
        ):
            raise AlpacaSipTradeParseError


def parse_alpaca_sip_trade_frame(payload: bytes) -> tuple[AlpacaSipTradeWireMessage, ...]:
    try:
        if type(payload) is not bytes or not payload:
            raise AlpacaSipTradeParseError
        messages = _FRAME_ADAPTER.validate_json(payload)
        if not messages:
            raise AlpacaSipTradeParseError
        return messages
    except (ValidationError, ValueError):
        raise AlpacaSipTradeParseError from None


def _common_is_valid(symbol: str, exchange: str, tape: str, conditions: tuple[str, ...]) -> bool:
    return (
        _SYMBOL.fullmatch(symbol) is not None
        and _VENUE.fullmatch(exchange) is not None
        and tape in {"A", "B", "C"}
        and _conditions_are_valid(conditions)
    )


def _conditions_are_valid(conditions: tuple[str, ...]) -> bool:
    return conditions == tuple(dict.fromkeys(conditions)) and all(
        _CONDITION.fullmatch(condition) is not None for condition in conditions
    )


__all__ = (
    "AlpacaSipReceivedTradeFrame",
    "AlpacaSipTradeCancelMessage",
    "AlpacaSipTradeCorrectionMessage",
    "AlpacaSipTradeHistoryError",
    "AlpacaSipTradeMessage",
    "AlpacaSipTradeParseError",
    "AlpacaSipTradeProjectionError",
    "AlpacaSipTradeWireMessage",
    "parse_alpaca_sip_trade_frame",
)
