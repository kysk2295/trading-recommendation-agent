from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import assert_never, final, override

from trading_agent.alpaca_sip_dynamic_market_models import (
    AlpacaSipDynamicMarketKind,
    AlpacaSipProjectedMarketMessage,
    AlpacaSipQuoteMessage,
    parse_alpaca_sip_dynamic_market_frame,
)
from trading_agent.alpaca_sip_dynamic_projection import project_alpaca_sip_dynamic_receipts
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan
from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeMessage,
)


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
    connection_epoch: str
    market_date: dt.date
    as_of: dt.datetime
    validated_trade_message_count: int
    observed_trade_message_count: int
    active_trades: tuple[AlpacaSipDynamicActiveTrade, ...]

    def __post_init__(self) -> None:
        if (
            len(self.plan_id) != 64
            or len(self.connection_epoch) != 32
            or type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or not _aware(self.as_of)
            or self.validated_trade_message_count < 0
            or not 0 <= self.observed_trade_message_count <= self.validated_trade_message_count
            or any(type(item) is not AlpacaSipDynamicActiveTrade for item in self.active_trades)
            or self.active_trades != tuple(sorted(self.active_trades, key=lambda item: item.root_event_id))
        ):
            raise AlpacaSipDynamicTradeStateError


@final
class _TradeState:
    __slots__ = ("_aliases", "_roots", "_seen")

    def __init__(self) -> None:
        self._aliases: dict[tuple[str, int], AlpacaSipDynamicActiveTrade] = {}
        self._roots: dict[str, AlpacaSipDynamicActiveTrade] = {}
        self._seen: set[tuple[str, int]] = set()

    def apply(
        self,
        projected: AlpacaSipProjectedMarketMessage,
        message: AlpacaSipTradeMessage | AlpacaSipTradeCorrectionMessage | AlpacaSipTradeCancelMessage,
    ) -> None:
        match message:
            case AlpacaSipTradeMessage():
                self._original(projected, message)
            case AlpacaSipTradeCorrectionMessage():
                self._correction(projected, message)
            case AlpacaSipTradeCancelMessage():
                self._cancel(message)
            case unreachable:
                assert_never(unreachable)

    def active(self) -> tuple[AlpacaSipDynamicActiveTrade, ...]:
        return tuple(sorted(self._roots.values(), key=lambda item: item.root_event_id))

    def _original(self, projected: AlpacaSipProjectedMarketMessage, message: AlpacaSipTradeMessage) -> None:
        alias = (message.symbol, message.trade_id)
        if alias in self._seen:
            raise AlpacaSipDynamicTradeStateError
        active = AlpacaSipDynamicActiveTrade(
            projected.event_id,
            projected.event_id,
            projected.instrument_id,
            message.symbol,
            message.trade_id,
            message.trade_id,
            (message.trade_id,),
            message.exchange,
            message.tape,
            message.price,
            message.size,
            message.conditions,
            message.timestamp,
            projected.received_at,
        )
        self._aliases[alias] = active
        self._roots[active.root_event_id] = active
        self._seen.add(alias)

    def _correction(
        self,
        projected: AlpacaSipProjectedMarketMessage,
        message: AlpacaSipTradeCorrectionMessage,
    ) -> None:
        target = self._aliases.get((message.symbol, message.original_trade_id))
        corrected_alias = (message.symbol, message.corrected_trade_id)
        expected = (
            message.exchange,
            message.tape,
            message.original_price,
            message.original_size,
            message.original_conditions,
        )
        if target is None or _values(target) != expected or corrected_alias in self._seen:
            raise AlpacaSipDynamicTradeStateError
        active = AlpacaSipDynamicActiveTrade(
            target.root_event_id,
            projected.event_id,
            target.instrument_id,
            target.symbol,
            target.provider_root_trade_id,
            message.corrected_trade_id,
            tuple(sorted((*target.trade_id_aliases, message.corrected_trade_id))),
            message.exchange,
            message.tape,
            message.corrected_price,
            message.corrected_size,
            message.corrected_conditions,
            message.timestamp,
            projected.received_at,
        )
        for alias, current in tuple(self._aliases.items()):
            if current is target:
                self._aliases[alias] = active
        self._aliases[corrected_alias] = active
        self._roots[target.root_event_id] = active
        self._seen.add(corrected_alias)

    def _cancel(self, message: AlpacaSipTradeCancelMessage) -> None:
        target = self._aliases.get((message.symbol, message.trade_id))
        expected = (message.exchange, message.tape, message.price, message.size)
        if target is None or _values(target)[:4] != expected:
            raise AlpacaSipDynamicTradeStateError
        for alias, current in tuple(self._aliases.items()):
            if current is target:
                del self._aliases[alias]
        del self._roots[target.root_event_id]


def materialize_alpaca_sip_dynamic_trades_as_of(
    store: AlpacaSipDynamicReceiptStore,
    plan: AlpacaSipDynamicSubscriptionPlan,
    connection_epoch: str,
    *,
    as_of: dt.datetime,
) -> AlpacaSipDynamicTradeState:
    try:
        if not _aware(as_of):
            raise AlpacaSipDynamicTradeStateError
        projected = project_alpaca_sip_dynamic_receipts(store, plan, connection_epoch)
        _validate_projection_order(projected)
        state = _TradeState()
        active_as_of: tuple[AlpacaSipDynamicActiveTrade, ...] = ()
        validated = 0
        observed = 0
        for item in projected:
            message = _parse_projected(item)
            match message:
                case AlpacaSipQuoteMessage():
                    pass
                case AlpacaSipTradeMessage() | AlpacaSipTradeCorrectionMessage() | AlpacaSipTradeCancelMessage():
                    state.apply(item, message)
                    validated += 1
                    if item.received_at <= as_of:
                        observed += 1
                        active_as_of = state.active()
                case unreachable:
                    assert_never(unreachable)
        return AlpacaSipDynamicTradeState(
            plan.plan_id,
            connection_epoch,
            plan.market_date,
            as_of.astimezone(dt.UTC),
            validated,
            observed,
            active_as_of,
        )
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise AlpacaSipDynamicTradeStateError from None


def _parse_projected(item: AlpacaSipProjectedMarketMessage):
    messages = parse_alpaca_sip_dynamic_market_frame(b"[" + item.payload + b"]")
    if len(messages) != 1:
        raise AlpacaSipDynamicTradeStateError
    message = messages[0]
    expected = {
        AlpacaSipDynamicMarketKind.QUOTE: AlpacaSipQuoteMessage,
        AlpacaSipDynamicMarketKind.TRADE: AlpacaSipTradeMessage,
        AlpacaSipDynamicMarketKind.CORRECTION: AlpacaSipTradeCorrectionMessage,
        AlpacaSipDynamicMarketKind.CANCEL: AlpacaSipTradeCancelMessage,
    }[item.kind]
    if type(message) is not expected:
        raise AlpacaSipDynamicTradeStateError
    return message


def _validate_projection_order(projected: tuple[AlpacaSipProjectedMarketMessage, ...]) -> None:
    order = tuple((item.sequence, item.message_index) for item in projected)
    received = tuple(item.received_at for item in projected)
    if order != tuple(sorted(set(order))) or received != tuple(sorted(received)):
        raise AlpacaSipDynamicTradeStateError


def _values(active: AlpacaSipDynamicActiveTrade) -> tuple[str, str, Decimal, int, tuple[str, ...]]:
    return active.exchange, active.tape, active.price, active.size, active.conditions


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicActiveTrade",
    "AlpacaSipDynamicTradeState",
    "AlpacaSipDynamicTradeStateError",
    "materialize_alpaca_sip_dynamic_trades_as_of",
)
