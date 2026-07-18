from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import re
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_sip_trade_models import AlpacaSipReceivedTradeFrame

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_INSTRUMENT_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class AlpacaSipTradeFixtureError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade fixture is invalid"


class AlpacaSipTradeFixtureFrame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    received_at: AwareDatetime
    payload_sha256: str
    payload_base64: str = Field(repr=False)

    @model_validator(mode="after")
    def validate_frame(self) -> Self:
        try:
            payload = base64.b64decode(self.payload_base64, validate=True)
        except (binascii.Error, ValueError):
            raise AlpacaSipTradeFixtureError from None
        if (
            _SHA256.fullmatch(self.payload_sha256) is None
            or not payload
            or hashlib.sha256(payload).hexdigest() != self.payload_sha256
        ):
            raise AlpacaSipTradeFixtureError
        return self

    def to_received_frame(self, market_date: dt.date) -> AlpacaSipReceivedTradeFrame:
        try:
            payload = base64.b64decode(self.payload_base64, validate=True)
        except (binascii.Error, ValueError):
            raise AlpacaSipTradeFixtureError from None
        return AlpacaSipReceivedTradeFrame(market_date, self.received_at, payload)


class AlpacaSipTradeHistoryFixture(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    market_date: dt.date
    symbol: str
    instrument_id: str
    frames: tuple[AlpacaSipTradeFixtureFrame, ...]

    @model_validator(mode="after")
    def validate_fixture(self) -> Self:
        received = tuple(frame.received_at for frame in self.frames)
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or _INSTRUMENT_ID.fullmatch(self.instrument_id) is None
            or not self.frames
            or received != tuple(sorted(received))
        ):
            raise AlpacaSipTradeFixtureError
        return self


__all__ = (
    "AlpacaSipTradeFixtureError",
    "AlpacaSipTradeFixtureFrame",
    "AlpacaSipTradeHistoryFixture",
)
