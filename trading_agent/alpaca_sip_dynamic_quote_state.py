from __future__ import annotations

import datetime as dt
import hashlib
from typing import assert_never

from trading_agent.alpaca_sip_dynamic_market_models import (
    AlpacaSipDynamicMarketKind,
    AlpacaSipDynamicWireMessage,
    AlpacaSipProjectedMarketMessage,
    AlpacaSipQuoteMessage,
    parse_alpaca_sip_dynamic_market_frame,
)
from trading_agent.alpaca_sip_dynamic_projection import project_alpaca_sip_dynamic_receipts
from trading_agent.alpaca_sip_dynamic_quote_state_models import (
    AlpacaSipDynamicLatestQuote,
    AlpacaSipDynamicQuoteState,
    AlpacaSipDynamicQuoteStateError,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan
from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeMessage,
)


def materialize_alpaca_sip_dynamic_quotes_as_of(
    store: AlpacaSipDynamicReceiptStore,
    plan: AlpacaSipDynamicSubscriptionPlan,
    connection_epoch: str,
    *,
    as_of: dt.datetime,
) -> AlpacaSipDynamicQuoteState:
    try:
        projected = project_alpaca_sip_dynamic_receipts(store, plan, connection_epoch)
        return _materialize_projected_quotes_as_of(
            projected,
            plan.plan_id,
            plan.market_date,
            (connection_epoch,),
            as_of,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipDynamicQuoteStateError from None


def _materialize_projected_quotes_as_of(
    projected: tuple[AlpacaSipProjectedMarketMessage, ...],
    plan_id: str,
    market_date: dt.date,
    connection_epochs: tuple[str, ...],
    as_of: dt.datetime,
) -> AlpacaSipDynamicQuoteState:
    if not _aware(as_of):
        raise AlpacaSipDynamicQuoteStateError
    _validate_projection_order(projected, plan_id, market_date, connection_epochs)
    latest: dict[str, AlpacaSipDynamicLatestQuote] = {}
    validated = 0
    observed = 0
    for item in projected:
        message = _parse_projected(item)
        match message:
            case AlpacaSipQuoteMessage():
                validated += 1
                if item.received_at <= as_of:
                    observed += 1
                    candidate = _latest_quote(item, message)
                    current = latest.get(candidate.instrument_id)
                    if current is None or _quote_order(candidate) > _quote_order(current):
                        latest[candidate.instrument_id] = candidate
            case AlpacaSipTradeMessage() | AlpacaSipTradeCorrectionMessage() | AlpacaSipTradeCancelMessage():
                pass
            case unreachable:
                assert_never(unreachable)
    return AlpacaSipDynamicQuoteState(
        plan_id,
        connection_epochs,
        market_date,
        as_of.astimezone(dt.UTC),
        validated,
        observed,
        tuple(latest[key] for key in sorted(latest)),
    )


def _latest_quote(
    projected: AlpacaSipProjectedMarketMessage,
    message: AlpacaSipQuoteMessage,
) -> AlpacaSipDynamicLatestQuote:
    return AlpacaSipDynamicLatestQuote(
        projected.event_id,
        projected.connection_epoch,
        projected.sequence,
        projected.message_index,
        projected.instrument_id,
        message.symbol,
        message.ask_exchange,
        message.ask_price,
        message.ask_size,
        message.bid_exchange,
        message.bid_price,
        message.bid_size,
        message.conditions,
        message.tape,
        message.timestamp,
        projected.received_at,
    )


def _quote_order(quote: AlpacaSipDynamicLatestQuote) -> tuple[dt.datetime, dt.datetime, int, int, str]:
    return (
        quote.event_time,
        quote.received_at,
        quote.source_sequence,
        quote.source_message_index,
        quote.current_event_id,
    )


def _parse_projected(item: AlpacaSipProjectedMarketMessage) -> AlpacaSipDynamicWireMessage:
    if hashlib.sha256(item.payload).hexdigest() != item.content_sha256:
        raise AlpacaSipDynamicQuoteStateError
    messages = parse_alpaca_sip_dynamic_market_frame(b"[" + item.payload + b"]")
    if len(messages) != 1:
        raise AlpacaSipDynamicQuoteStateError
    message = messages[0]
    match item.kind:
        case AlpacaSipDynamicMarketKind.QUOTE:
            valid_type = type(message) is AlpacaSipQuoteMessage
        case AlpacaSipDynamicMarketKind.TRADE:
            valid_type = type(message) is AlpacaSipTradeMessage
        case AlpacaSipDynamicMarketKind.CORRECTION:
            valid_type = type(message) is AlpacaSipTradeCorrectionMessage
        case AlpacaSipDynamicMarketKind.CANCEL:
            valid_type = type(message) is AlpacaSipTradeCancelMessage
        case unreachable:
            assert_never(unreachable)
    if not valid_type or message.symbol != item.symbol or message.timestamp != item.event_time:
        raise AlpacaSipDynamicQuoteStateError
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
        raise AlpacaSipDynamicQuoteStateError


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicLatestQuote",
    "AlpacaSipDynamicQuoteState",
    "AlpacaSipDynamicQuoteStateError",
    "materialize_alpaca_sip_dynamic_quotes_as_of",
)
