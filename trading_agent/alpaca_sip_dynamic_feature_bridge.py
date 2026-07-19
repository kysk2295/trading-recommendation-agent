from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, override

from trading_agent.alpaca_sip_dynamic_trade_history import (
    AlpacaSipDynamicTradeHistory,
    require_complete_alpaca_sip_dynamic_trade_history,
)
from trading_agent.alpaca_sip_dynamic_trade_state_models import AlpacaSipDynamicActiveTrade
from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus, IntradayFeatureSnapshot
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import NEW_YORK

_SEMANTIC_VERSION: Final = "alpaca_sip_dynamic_feature_confirmation_v1"
_MAX_TRADE_AGE: Final = dt.timedelta(minutes=2)


class AlpacaSipDynamicFeatureBridgeError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic feature confirmation is blocked"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicFeatureConfirmation:
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
    trade_event_id: str
    provider_trade_id: int
    source_sequence: int
    source_message_index: int
    trade_event_time: dt.datetime
    trade_received_at: dt.datetime
    last_trade_price: Decimal
    vwap: Decimal
    price_vs_vwap_bps: Decimal
    last_trade_at_or_above_vwap: bool
    complete_history: bool

    def __post_init__(self) -> None:
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
            or len(self.trade_event_id) != 64
            or self.provider_trade_id <= 0
            or self.source_sequence < 4
            or self.source_message_index < 0
            or not _aware(self.trade_event_time)
            or not _aware(self.trade_received_at)
            or type(self.last_trade_price) is not Decimal
            or not self.last_trade_price.is_finite()
            or self.last_trade_price <= 0
            or type(self.vwap) is not Decimal
            or not self.vwap.is_finite()
            or self.vwap <= 0
            or type(self.price_vs_vwap_bps) is not Decimal
            or not self.price_vs_vwap_bps.is_finite()
            or self.price_vs_vwap_bps != ((self.last_trade_price / self.vwap) - Decimal(1)) * Decimal(10_000)
            or type(self.last_trade_at_or_above_vwap) is not bool
            or self.last_trade_at_or_above_vwap != (self.last_trade_price >= self.vwap)
            or not self.bar_source_end_at <= self.trade_event_time <= self.trade_received_at <= self.observed_at
            or self.complete_history is not True
        ):
            raise AlpacaSipDynamicFeatureBridgeError


def confirm_intraday_feature_with_dynamic_trade(
    snapshot: IntradayFeatureSnapshot,
    history: AlpacaSipDynamicTradeHistory,
) -> AlpacaSipDynamicFeatureConfirmation:
    try:
        complete = require_complete_alpaca_sip_dynamic_trade_history(history)
        source_end, vwap = _validate_inputs(snapshot, complete)
        matches = tuple(item for item in complete.state.active_trades if item.instrument_id == snapshot.instrument_id)
        if not matches:
            raise AlpacaSipDynamicFeatureBridgeError
        latest = max(matches, key=_trade_order)
        if (
            latest.current_connection_epoch != complete.state.connection_epochs[0]
            or latest.event_time < source_end
            or latest.received_at < source_end
            or not dt.timedelta(0) <= snapshot.observed_at - latest.received_at <= _MAX_TRADE_AGE
        ):
            raise AlpacaSipDynamicFeatureBridgeError
        price_vs_vwap_bps = ((latest.price / vwap) - Decimal(1)) * Decimal(10_000)
        payload = _identity_payload(snapshot, complete, latest, source_end, vwap, price_vs_vwap_bps)
        return AlpacaSipDynamicFeatureConfirmation(
            hashlib.sha256(payload).hexdigest(),
            _SEMANTIC_VERSION,
            snapshot.identity.identity_sha256,
            complete.state.plan_id,
            latest.current_connection_epoch,
            complete.state.market_date,
            latest.instrument_id,
            latest.symbol,
            snapshot.observed_at.astimezone(dt.UTC),
            source_end.astimezone(dt.UTC),
            latest.current_event_id,
            latest.current_trade_id,
            latest.source_sequence,
            latest.source_message_index,
            latest.event_time.astimezone(dt.UTC),
            latest.received_at.astimezone(dt.UTC),
            latest.price,
            vwap,
            price_vs_vwap_bps,
            latest.price >= vwap,
            True,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipDynamicFeatureBridgeError from None


def _validate_inputs(
    snapshot: IntradayFeatureSnapshot,
    history: AlpacaSipDynamicTradeHistory,
) -> tuple[dt.datetime, Decimal]:
    if type(snapshot) is not IntradayFeatureSnapshot:
        raise AlpacaSipDynamicFeatureBridgeError
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
        or type(snapshot.vwap) is not Decimal
        or not snapshot.vwap.is_finite()
        or snapshot.vwap <= 0
    ):
        raise AlpacaSipDynamicFeatureBridgeError
    return source_end, snapshot.vwap


def _trade_order(trade: AlpacaSipDynamicActiveTrade) -> tuple[dt.datetime, dt.datetime, int, int, str]:
    return (
        trade.event_time,
        trade.received_at,
        trade.source_sequence,
        trade.source_message_index,
        trade.current_event_id,
    )


def _identity_payload(
    snapshot: IntradayFeatureSnapshot,
    history: AlpacaSipDynamicTradeHistory,
    trade: AlpacaSipDynamicActiveTrade,
    source_end: dt.datetime,
    vwap: Decimal,
    price_vs_vwap_bps: Decimal,
) -> bytes:
    content = (
        _SEMANTIC_VERSION,
        snapshot.identity.identity_sha256,
        history.state.plan_id,
        trade.current_connection_epoch,
        trade.current_event_id,
        str(trade.current_trade_id),
        str(trade.source_sequence),
        str(trade.source_message_index),
        snapshot.observed_at.astimezone(dt.UTC).isoformat(),
        source_end.astimezone(dt.UTC).isoformat(),
        str(trade.price),
        str(vwap),
        str(price_vs_vwap_bps),
    )
    return json.dumps(content, ensure_ascii=True, separators=(",", ":")).encode()


def _time(value: dt.datetime | None) -> dt.datetime:
    if value is None or not _aware(value):
        raise AlpacaSipDynamicFeatureBridgeError
    return value


def _aware(value: dt.datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicFeatureBridgeError",
    "AlpacaSipDynamicFeatureConfirmation",
    "confirm_intraday_feature_with_dynamic_trade",
)
