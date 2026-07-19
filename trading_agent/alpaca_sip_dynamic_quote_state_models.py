from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import override


class AlpacaSipDynamicQuoteStateError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic quote state is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicLatestQuote:
    current_event_id: str
    current_connection_epoch: str
    source_sequence: int
    source_message_index: int
    instrument_id: str
    symbol: str
    ask_exchange: str
    ask_price: Decimal
    ask_size: int
    bid_exchange: str
    bid_price: Decimal
    bid_size: int
    conditions: tuple[str, ...]
    tape: str
    event_time: dt.datetime
    received_at: dt.datetime

    def __post_init__(self) -> None:
        if (
            len(self.current_event_id) != 64
            or len(self.current_connection_epoch) != 32
            or self.source_sequence < 4
            or self.source_message_index < 0
            or not self.instrument_id
            or not self.symbol
            or not self.ask_exchange
            or type(self.ask_price) is not Decimal
            or not self.ask_price.is_finite()
            or self.ask_price <= 0
            or self.ask_size < 0
            or not self.bid_exchange
            or type(self.bid_price) is not Decimal
            or not self.bid_price.is_finite()
            or self.bid_price <= 0
            or self.bid_price > self.ask_price
            or self.bid_size < 0
            or self.conditions != tuple(dict.fromkeys(self.conditions))
            or self.tape not in {"A", "B", "C"}
            or not _aware(self.event_time)
            or not _aware(self.received_at)
            or self.event_time > self.received_at
        ):
            raise AlpacaSipDynamicQuoteStateError


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicQuoteState:
    plan_id: str
    connection_epochs: tuple[str, ...]
    market_date: dt.date
    as_of: dt.datetime
    validated_quote_message_count: int
    observed_quote_message_count: int
    latest_quotes: tuple[AlpacaSipDynamicLatestQuote, ...]

    def __post_init__(self) -> None:
        instruments = tuple(item.instrument_id for item in self.latest_quotes)
        if (
            len(self.plan_id) != 64
            or not self.connection_epochs
            or any(len(epoch) != 32 for epoch in self.connection_epochs)
            or self.connection_epochs != tuple(dict.fromkeys(self.connection_epochs))
            or type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or not _aware(self.as_of)
            or self.validated_quote_message_count < 0
            or not 0 <= self.observed_quote_message_count <= self.validated_quote_message_count
            or any(type(item) is not AlpacaSipDynamicLatestQuote for item in self.latest_quotes)
            or instruments != tuple(sorted(set(instruments)))
        ):
            raise AlpacaSipDynamicQuoteStateError


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicLatestQuote",
    "AlpacaSipDynamicQuoteState",
    "AlpacaSipDynamicQuoteStateError",
)
