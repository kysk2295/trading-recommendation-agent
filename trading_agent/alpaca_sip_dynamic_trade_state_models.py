from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import override


class AlpacaSipDynamicTradeStateError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic trade state is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicActiveTrade:
    root_event_id: str
    current_event_id: str
    instrument_id: str
    symbol: str
    provider_root_trade_id: int
    current_trade_id: int
    trade_id_aliases: tuple[int, ...]
    exchange: str
    tape: str
    price: Decimal
    size: int
    conditions: tuple[str, ...]
    event_time: dt.datetime
    received_at: dt.datetime

    def __post_init__(self) -> None:
        if (
            len(self.root_event_id) != 64
            or len(self.current_event_id) != 64
            or not self.instrument_id
            or not self.symbol
            or self.provider_root_trade_id <= 0
            or self.current_trade_id <= 0
            or self.trade_id_aliases != tuple(sorted(set(self.trade_id_aliases)))
            or self.provider_root_trade_id not in self.trade_id_aliases
            or self.current_trade_id not in self.trade_id_aliases
            or not self.exchange
            or self.tape not in {"A", "B", "C"}
            or self.price <= 0
            or self.size <= 0
            or not _aware(self.event_time)
            or not _aware(self.received_at)
            or self.event_time > self.received_at
        ):
            raise AlpacaSipDynamicTradeStateError


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicTradeState:
    plan_id: str
    connection_epochs: tuple[str, ...]
    market_date: dt.date
    as_of: dt.datetime
    validated_trade_message_count: int
    observed_trade_message_count: int
    duplicate_trade_message_count: int
    active_trades: tuple[AlpacaSipDynamicActiveTrade, ...]

    def __post_init__(self) -> None:
        if (
            len(self.plan_id) != 64
            or not self.connection_epochs
            or any(len(epoch) != 32 for epoch in self.connection_epochs)
            or self.connection_epochs != tuple(dict.fromkeys(self.connection_epochs))
            or type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or not _aware(self.as_of)
            or self.validated_trade_message_count < 0
            or not 0 <= self.observed_trade_message_count <= self.validated_trade_message_count
            or not 0 <= self.duplicate_trade_message_count <= self.validated_trade_message_count
            or any(type(item) is not AlpacaSipDynamicActiveTrade for item in self.active_trades)
            or self.active_trades != tuple(sorted(self.active_trades, key=lambda item: item.root_event_id))
        ):
            raise AlpacaSipDynamicTradeStateError


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicActiveTrade",
    "AlpacaSipDynamicTradeState",
    "AlpacaSipDynamicTradeStateError",
)
