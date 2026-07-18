from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

_EPOCH: Final = re.compile(r"^[0-9a-f]{32}$")
_RECEIPT: Final = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


class AlpacaSipTradeStreamError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade stream is unavailable"


class AlpacaSipTradeStreamEndpointError(AlpacaSipTradeStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade stream endpoint is invalid"


class AlpacaSipTradeStreamProtocolError(AlpacaSipTradeStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade stream protocol is invalid"


class AlpacaSipControlStage(StrEnum):
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    SUBSCRIBED = "subscribed"


class AlpacaSipStreamTerminalStatus(StrEnum):
    BOUNDED_COMPLETE = "bounded_complete"
    FAILED = "failed"


class _ConnectedMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["success"] = Field(alias="T")
    message: Literal["connected"] = Field(alias="msg")


class _AuthenticatedMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["success"] = Field(alias="T")
    message: Literal["authenticated"] = Field(alias="msg")


class _SubscriptionMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["subscription"] = Field(alias="T")
    trades: tuple[str, ...]
    quotes: tuple[str, ...]
    bars: tuple[str, ...]
    updated_bars: tuple[str, ...] = Field(alias="updatedBars")
    daily_bars: tuple[str, ...] = Field(alias="dailyBars")
    statuses: tuple[str, ...]
    lulds: tuple[str, ...]
    corrections: tuple[str, ...]
    cancel_errors: tuple[str, ...] = Field(alias="cancelErrors")


class _ErrorMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    message_type: Literal["error"] = Field(alias="T")
    code: int
    message: str = Field(alias="msg")


type AlpacaSipControlMessage = _ConnectedMessage | _AuthenticatedMessage | _SubscriptionMessage | _ErrorMessage
_CONTROL_ADAPTER: Final = TypeAdapter(tuple[AlpacaSipControlMessage, ...])


@dataclass(frozen=True, slots=True)
class AlpacaSipTradeStreamConfig:
    market_date: dt.date
    symbol: str

    def __post_init__(self) -> None:
        if (
            type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or _SYMBOL.fullmatch(self.symbol) is None
        ):
            raise AlpacaSipTradeStreamProtocolError


@dataclass(frozen=True, slots=True)
class AlpacaSipRawControlFrame:
    connection_epoch: str
    sequence: int
    received_at: dt.datetime
    payload: bytes

    def __post_init__(self) -> None:
        aware = self.received_at.tzinfo is not None and self.received_at.utcoffset() is not None
        if (
            _EPOCH.fullmatch(self.connection_epoch) is None
            or self.sequence <= 0
            or not aware
            or type(self.payload) is not bytes
            or not self.payload
        ):
            raise AlpacaSipTradeStreamProtocolError


@dataclass(frozen=True, slots=True)
class AlpacaSipStreamTerminalRecord:
    connection_epoch: str
    config: AlpacaSipTradeStreamConfig
    authorized_at: dt.datetime
    subscribed_at: dt.datetime
    terminal_at: dt.datetime
    status: AlpacaSipStreamTerminalStatus

    def __post_init__(self) -> None:
        times = (self.authorized_at, self.subscribed_at, self.terminal_at)
        aware = all(value.tzinfo is not None and value.utcoffset() is not None for value in times)
        if (
            _EPOCH.fullmatch(self.connection_epoch) is None
            or type(self.config) is not AlpacaSipTradeStreamConfig
            or not aware
            or not self.authorized_at <= self.subscribed_at <= self.terminal_at
            or type(self.status) is not AlpacaSipStreamTerminalStatus
        ):
            raise AlpacaSipTradeStreamProtocolError


@dataclass(frozen=True, slots=True)
class AlpacaSipBoundedTradeHistoryAttestation:
    connection_epoch: str
    config: AlpacaSipTradeStreamConfig
    authorized_at: dt.datetime
    subscribed_at: dt.datetime
    completed_at: dt.datetime
    receipt_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        times = (self.authorized_at, self.subscribed_at, self.completed_at)
        aware = all(value.tzinfo is not None and value.utcoffset() is not None for value in times)
        if (
            _EPOCH.fullmatch(self.connection_epoch) is None
            or type(self.config) is not AlpacaSipTradeStreamConfig
            or not aware
            or not self.authorized_at <= self.subscribed_at <= self.completed_at
            or not self.receipt_ids
            or self.receipt_ids != tuple(dict.fromkeys(self.receipt_ids))
            or any(_RECEIPT.fullmatch(receipt_id) is None for receipt_id in self.receipt_ids)
        ):
            raise AlpacaSipTradeStreamProtocolError


def parse_alpaca_sip_control_frame(
    payload: bytes,
    stage: AlpacaSipControlStage,
    symbol: str,
) -> None:
    try:
        messages = _CONTROL_ADAPTER.validate_json(payload)
        if len(messages) != 1:
            raise AlpacaSipTradeStreamProtocolError
        message = messages[0]
        match stage:
            case AlpacaSipControlStage.CONNECTED:
                valid = type(message) is _ConnectedMessage
            case AlpacaSipControlStage.AUTHENTICATED:
                valid = type(message) is _AuthenticatedMessage
            case AlpacaSipControlStage.SUBSCRIBED:
                valid = type(message) is _SubscriptionMessage and _subscription_is_exact(message, symbol)
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise AlpacaSipTradeStreamProtocolError
    except (TypeError, ValidationError, ValueError):
        raise AlpacaSipTradeStreamProtocolError from None


def _subscription_is_exact(message: _SubscriptionMessage, symbol: str) -> bool:
    return (
        message.trades == (symbol,)
        and message.corrections == (symbol,)
        and message.cancel_errors == (symbol,)
        and not any(
            (
                message.quotes,
                message.bars,
                message.updated_bars,
                message.daily_bars,
                message.statuses,
                message.lulds,
            )
        )
    )


__all__ = (
    "AlpacaSipBoundedTradeHistoryAttestation",
    "AlpacaSipControlStage",
    "AlpacaSipRawControlFrame",
    "AlpacaSipStreamTerminalRecord",
    "AlpacaSipStreamTerminalStatus",
    "AlpacaSipTradeStreamConfig",
    "AlpacaSipTradeStreamEndpointError",
    "AlpacaSipTradeStreamError",
    "AlpacaSipTradeStreamProtocolError",
    "parse_alpaca_sip_control_frame",
)
