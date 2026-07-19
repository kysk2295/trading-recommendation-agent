from __future__ import annotations

import datetime as dt
from decimal import Decimal

from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import SignalActionability, SignalEntryType, SignalSide
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_quote_actionability_models import (
    BASIS_POINTS,
    MAX_ENTRY_SLIPPAGE_BPS,
    MAX_QUOTE_SPREAD_BPS,
    QUOTE_FRESHNESS,
    InvalidUsQuoteActionabilityInputError,
    QuoteAssessmentStatus,
    UsQuoteSnapshot,
)


def snapshot_terminal_status(
    base: TradeSignalPublication,
    snapshot: UsQuoteSnapshot,
    *,
    evaluated_at: dt.datetime,
) -> QuoteAssessmentStatus:
    provider_at = snapshot.provider_observed_at.astimezone(NEW_YORK)
    received_at = snapshot.received_at.astimezone(NEW_YORK)
    evaluated = evaluated_at.astimezone(NEW_YORK)
    if provider_at > evaluated or provider_at > received_at or received_at > evaluated:
        return QuoteAssessmentStatus.FUTURE_QUOTE
    if provider_at.date() != evaluated.date() or not in_regular_session(provider_at):
        return QuoteAssessmentStatus.STALE_QUOTE
    if evaluated - provider_at >= QUOTE_FRESHNESS:
        return QuoteAssessmentStatus.STALE_QUOTE
    if snapshot.spread_bps > MAX_QUOTE_SPREAD_BPS:
        return QuoteAssessmentStatus.SPREAD_TOO_WIDE
    if snapshot.bid <= base.signal.stop_price:
        return QuoteAssessmentStatus.SETUP_INVALIDATED
    maximum_entry = base.signal.entry_price * (Decimal(1) + MAX_ENTRY_SLIPPAGE_BPS / BASIS_POINTS)
    if snapshot.ask > maximum_entry:
        return QuoteAssessmentStatus.ENTRY_SLIPPAGE_EXCEEDED
    if snapshot.ask >= base.signal.entry_price:
        return QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED
    return QuoteAssessmentStatus.VALIDATED_WAITING


def base_is_current(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> bool:
    signal = base.signal
    return (
        signal.strategy_lane.market_id is MarketId.US_EQUITIES
        and signal.side is SignalSide.LONG
        and signal.entry_type is SignalEntryType.STOP_TRIGGER
        and signal.actionability is SignalActionability.CONDITIONAL
        and signal.quote_validation is None
        and scan_started_at <= base.published_at <= evaluated_at
        and evaluated_at < signal.valid_until
    )


def validate_control_times(scan_started_at: dt.datetime, evaluated_at: dt.datetime) -> None:
    if not _aware(scan_started_at) or not _aware(evaluated_at) or scan_started_at > evaluated_at:
        raise InvalidUsQuoteActionabilityInputError


def in_regular_session(value: dt.datetime) -> bool:
    current = value.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    return bounds is not None and bounds[0] <= current < bounds[1]


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "base_is_current",
    "in_regular_session",
    "snapshot_terminal_status",
    "validate_control_times",
)
