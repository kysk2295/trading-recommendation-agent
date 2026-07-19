from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, override

from trading_agent.alpaca_sip_dynamic_quote_history import (
    AlpacaSipDynamicQuoteHistory,
    require_complete_alpaca_sip_dynamic_quote_history,
)
from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus, IntradayFeatureSnapshot
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import NEW_YORK

_SEMANTIC_VERSION: Final = "alpaca_sip_dynamic_quote_feature_confirmation_v1"
_QUOTE_FRESHNESS: Final = dt.timedelta(seconds=5)
_BASIS_POINTS: Final = Decimal(10_000)


class AlpacaSipDynamicQuoteFeatureBridgeError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic quote feature confirmation is blocked"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicQuoteFeatureConfirmation:
    confirmation_id: str
    semantic_version: str
    research_input_identity_sha256: str
    dynamic_plan_id: str
    connection_epoch: str
    market_date: dt.date
    instrument_id: str
    symbol: str
    observed_at: dt.datetime
    bar_source_end_at: dt.datetime
    quote_event_id: str
    source_sequence: int
    source_message_index: int
    quote_event_time: dt.datetime
    quote_received_at: dt.datetime
    quote_valid_until: dt.datetime
    bid_exchange: str
    bid_price: Decimal
    bid_size: int
    ask_exchange: str
    ask_price: Decimal
    ask_size: int
    conditions: tuple[str, ...]
    tape: str
    midpoint: Decimal
    microprice: Decimal
    order_book_imbalance: Decimal
    spread_bps: Decimal
    vwap: Decimal
    midpoint_vs_vwap_bps: Decimal
    complete_history: bool

    def __post_init__(self) -> None:
        total_size = self.bid_size + self.ask_size
        if (
            len(self.confirmation_id) != 64
            or self.semantic_version != _SEMANTIC_VERSION
            or len(self.research_input_identity_sha256) != 64
            or len(self.dynamic_plan_id) != 64
            or len(self.connection_epoch) != 32
            or type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or not self.instrument_id
            or not self.symbol
            or not _aware(self.observed_at)
            or not _aware(self.bar_source_end_at)
            or len(self.quote_event_id) != 64
            or self.source_sequence < 4
            or self.source_message_index < 0
            or not _aware(self.quote_event_time)
            or not _aware(self.quote_received_at)
            or not _aware(self.quote_valid_until)
            or not self.bid_exchange
            or not _positive_decimal(self.bid_price)
            or self.bid_size < 0
            or not self.ask_exchange
            or not _positive_decimal(self.ask_price)
            or self.ask_size < 0
            or self.bid_price > self.ask_price
            or total_size <= 0
            or self.conditions != tuple(dict.fromkeys(self.conditions))
            or self.tape not in {"A", "B", "C"}
            or not _finite_decimal(self.midpoint)
            or not _finite_decimal(self.microprice)
            or not _finite_decimal(self.order_book_imbalance)
            or not _finite_decimal(self.spread_bps)
            or not _positive_decimal(self.vwap)
            or not _finite_decimal(self.midpoint_vs_vwap_bps)
            or self.midpoint != (self.bid_price + self.ask_price) / Decimal(2)
            or self.microprice != (self.ask_price * self.bid_size + self.bid_price * self.ask_size) / total_size
            or self.order_book_imbalance != Decimal(self.bid_size - self.ask_size) / total_size
            or self.spread_bps != (self.ask_price - self.bid_price) / self.midpoint * _BASIS_POINTS
            or self.midpoint_vs_vwap_bps != ((self.midpoint / self.vwap) - Decimal(1)) * _BASIS_POINTS
            or self.quote_valid_until != self.quote_event_time + _QUOTE_FRESHNESS
            or not self.bar_source_end_at
            <= self.quote_event_time
            <= self.quote_received_at
            <= self.observed_at
            < self.quote_valid_until
            or self.complete_history is not True
        ):
            raise AlpacaSipDynamicQuoteFeatureBridgeError


def confirm_intraday_feature_with_dynamic_quote(
    snapshot: IntradayFeatureSnapshot,
    history: AlpacaSipDynamicQuoteHistory,
) -> AlpacaSipDynamicQuoteFeatureConfirmation:
    try:
        complete = require_complete_alpaca_sip_dynamic_quote_history(history)
        source_end, vwap = _validate_inputs(snapshot, complete)
        matches = tuple(item for item in complete.state.latest_quotes if item.instrument_id == snapshot.instrument_id)
        if len(matches) != 1:
            raise AlpacaSipDynamicQuoteFeatureBridgeError
        quote = matches[0]
        if (
            quote.current_connection_epoch != complete.state.connection_epochs[0]
            or quote.event_time < source_end
            or quote.received_at < source_end
            or not dt.timedelta(0) <= snapshot.observed_at - quote.event_time < _QUOTE_FRESHNESS
            or quote.bid_size + quote.ask_size <= 0
        ):
            raise AlpacaSipDynamicQuoteFeatureBridgeError
        midpoint = (quote.bid_price + quote.ask_price) / Decimal(2)
        total_size = quote.bid_size + quote.ask_size
        microprice = (quote.ask_price * quote.bid_size + quote.bid_price * quote.ask_size) / total_size
        imbalance = Decimal(quote.bid_size - quote.ask_size) / total_size
        spread_bps = (quote.ask_price - quote.bid_price) / midpoint * _BASIS_POINTS
        midpoint_vs_vwap_bps = ((midpoint / vwap) - Decimal(1)) * _BASIS_POINTS
        content = (
            _SEMANTIC_VERSION,
            snapshot.identity.identity_sha256,
            complete.state.plan_id,
            quote.current_connection_epoch,
            quote.current_event_id,
            str(quote.source_sequence),
            str(quote.source_message_index),
            snapshot.observed_at.astimezone(dt.UTC).isoformat(),
            source_end.astimezone(dt.UTC).isoformat(),
            str(quote.bid_price),
            str(quote.bid_size),
            str(quote.ask_price),
            str(quote.ask_size),
            str(vwap),
            str(midpoint),
            str(microprice),
            str(imbalance),
            str(spread_bps),
            str(midpoint_vs_vwap_bps),
        )
        payload = json.dumps(content, ensure_ascii=True, separators=(",", ":")).encode()
        return AlpacaSipDynamicQuoteFeatureConfirmation(
            hashlib.sha256(payload).hexdigest(),
            _SEMANTIC_VERSION,
            snapshot.identity.identity_sha256,
            complete.state.plan_id,
            quote.current_connection_epoch,
            complete.state.market_date,
            quote.instrument_id,
            quote.symbol,
            snapshot.observed_at.astimezone(dt.UTC),
            source_end.astimezone(dt.UTC),
            quote.current_event_id,
            quote.source_sequence,
            quote.source_message_index,
            quote.event_time.astimezone(dt.UTC),
            quote.received_at.astimezone(dt.UTC),
            (quote.event_time + _QUOTE_FRESHNESS).astimezone(dt.UTC),
            quote.bid_exchange,
            quote.bid_price,
            quote.bid_size,
            quote.ask_exchange,
            quote.ask_price,
            quote.ask_size,
            quote.conditions,
            quote.tape,
            midpoint,
            microprice,
            imbalance,
            spread_bps,
            vwap,
            midpoint_vs_vwap_bps,
            True,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipDynamicQuoteFeatureBridgeError from None


def _validate_inputs(
    snapshot: IntradayFeatureSnapshot,
    history: AlpacaSipDynamicQuoteHistory,
) -> tuple[dt.datetime, Decimal]:
    if type(snapshot) is not IntradayFeatureSnapshot:
        raise AlpacaSipDynamicQuoteFeatureBridgeError
    source_end = _time(snapshot.source_end_at)
    if (
        type(snapshot.identity) is not ResearchInputIdentity
        or snapshot.status is not FeatureSnapshotStatus.READY
        or history.state.as_of != snapshot.observed_at
        or history.state.market_date != snapshot.observed_at.astimezone(NEW_YORK).date()
        or not history.terminal_observed
        or len(history.state.connection_epochs) != 1
        or source_end >= snapshot.observed_at
        or snapshot.volume_profile.instrument_id != snapshot.instrument_id
        or snapshot.volume_profile.target_session_date != history.state.market_date
        or not _positive_decimal(snapshot.vwap)
    ):
        raise AlpacaSipDynamicQuoteFeatureBridgeError
    return source_end, _decimal(snapshot.vwap)


def _time(value: dt.datetime | None) -> dt.datetime:
    if value is None or not _aware(value):
        raise AlpacaSipDynamicQuoteFeatureBridgeError
    return value


def _decimal(value: Decimal | None) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise AlpacaSipDynamicQuoteFeatureBridgeError
    return value


def _positive_decimal(value: Decimal | None) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _finite_decimal(value: Decimal | None) -> bool:
    return type(value) is Decimal and value.is_finite()


def _aware(value: dt.datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicQuoteFeatureBridgeError",
    "AlpacaSipDynamicQuoteFeatureConfirmation",
    "confirm_intraday_feature_with_dynamic_quote",
)
