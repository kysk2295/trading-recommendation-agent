from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import assert_never, final

from trading_agent.alpaca_sip_dynamic_market_models import (
    AlpacaSipDynamicMarketKind,
    AlpacaSipProjectedMarketMessage,
    AlpacaSipQuoteMessage,
    parse_alpaca_sip_dynamic_market_frame,
)
from trading_agent.alpaca_sip_dynamic_projection import project_alpaca_sip_dynamic_receipts
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan
from trading_agent.alpaca_sip_dynamic_trade_state_models import (
    AlpacaSipDynamicActiveTrade,
    AlpacaSipDynamicTradeState,
    AlpacaSipDynamicTradeStateError,
)
from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeMessage,
)


@final
class _TradeState:
    __slots__ = ("_aliases", "_roots", "_seen", "_seen_payloads")

    def __init__(self) -> None:
        self._aliases: dict[tuple[str, int], AlpacaSipDynamicActiveTrade] = {}
        self._roots: dict[str, AlpacaSipDynamicActiveTrade] = {}
        self._seen: set[tuple[str, int]] = set()
        self._seen_payloads: set[str] = set()

    def apply(
        self,
        projected: AlpacaSipProjectedMarketMessage,
        message: AlpacaSipTradeMessage | AlpacaSipTradeCorrectionMessage | AlpacaSipTradeCancelMessage,
    ) -> bool:
        if projected.content_sha256 in self._seen_payloads:
            return False
        self._seen_payloads.add(projected.content_sha256)
        match message:
            case AlpacaSipTradeMessage():
                self._original(projected, message)
            case AlpacaSipTradeCorrectionMessage():
                self._correction(projected, message)
            case AlpacaSipTradeCancelMessage():
                self._cancel(message)
            case unreachable:
                assert_never(unreachable)
        return True

    def active(self) -> tuple[AlpacaSipDynamicActiveTrade, ...]:
        return tuple(sorted(self._roots.values(), key=lambda item: item.root_event_id))

    def _original(self, projected: AlpacaSipProjectedMarketMessage, message: AlpacaSipTradeMessage) -> None:
        alias = (message.symbol, message.trade_id)
        if alias in self._seen:
            raise AlpacaSipDynamicTradeStateError
        active = AlpacaSipDynamicActiveTrade(
            projected.event_id,
            projected.event_id,
            projected.connection_epoch,
            projected.sequence,
            projected.message_index,
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
            projected.connection_epoch,
            projected.sequence,
            projected.message_index,
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
        projected = project_alpaca_sip_dynamic_receipts(store, plan, connection_epoch)
        return _materialize_projected_trades_as_of(
            projected,
            plan.plan_id,
            plan.market_date,
            (connection_epoch,),
            as_of,
        )
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise AlpacaSipDynamicTradeStateError from None


def _materialize_projected_trades_as_of(
    projected: tuple[AlpacaSipProjectedMarketMessage, ...],
    plan_id: str,
    market_date: dt.date,
    connection_epochs: tuple[str, ...],
    as_of: dt.datetime,
) -> AlpacaSipDynamicTradeState:
    if not _aware(as_of):
        raise AlpacaSipDynamicTradeStateError
    _validate_projection_order(projected, plan_id, market_date, connection_epochs)
    state = _TradeState()
    active_as_of: tuple[AlpacaSipDynamicActiveTrade, ...] = ()
    validated = 0
    observed = 0
    duplicates = 0
    for item in projected:
        message = _parse_projected(item)
        match message:
            case AlpacaSipQuoteMessage():
                pass
            case AlpacaSipTradeMessage() | AlpacaSipTradeCorrectionMessage() | AlpacaSipTradeCancelMessage():
                applied = state.apply(item, message)
                validated += 1
                if not applied:
                    duplicates += 1
                if item.received_at <= as_of:
                    observed += 1
                    active_as_of = state.active()
            case unreachable:
                assert_never(unreachable)
    return AlpacaSipDynamicTradeState(
        plan_id,
        connection_epochs,
        market_date,
        as_of.astimezone(dt.UTC),
        validated,
        observed,
        duplicates,
        active_as_of,
    )


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


def _validate_projection_order(
    projected: tuple[AlpacaSipProjectedMarketMessage, ...],
    plan_id: str,
    market_date: dt.date,
    connection_epochs: tuple[str, ...],
) -> None:
    positions = {epoch: index for index, epoch in enumerate(connection_epochs)}
    order = tuple(
        (item.received_at, positions.get(item.connection_epoch, -1), item.sequence, item.message_index)
        for item in projected
    )
    if (
        not connection_epochs
        or len(positions) != len(connection_epochs)
        or any(item.plan_id != plan_id or item.market_date != market_date for item in projected)
        or any(item.connection_epoch not in positions for item in projected)
        or len({item.event_id for item in projected}) != len(projected)
        or order != tuple(sorted(set(order)))
    ):
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
