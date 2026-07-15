from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kis_us_quote import KisUsLevelOneQuote
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import (
    EvidenceRef,
    QuoteValidation,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

QUOTE_FRESHNESS: Final = dt.timedelta(seconds=5)
MAX_QUOTE_SPREAD_BPS: Final = Decimal("25")
MAX_ENTRY_SLIPPAGE_BPS: Final = Decimal("20")
BASIS_POINTS: Final = Decimal("10000")
_EXCHANGE: Final = re.compile(r"[A-Z0-9]{2,8}", flags=re.ASCII)
_US_SYMBOL: Final = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,19}", flags=re.ASCII)
_QUOTE_ID: Final = re.compile(r"us-quote:[0-9a-f]{64}", flags=re.ASCII)
_ASSESSMENT_ID: Final = re.compile(
    r"us-quote-assessment:[0-9a-f]{64}",
    flags=re.ASCII,
)
_DERIVED_SIGNAL_ID: Final = re.compile(
    r"us-quote-signal:[0-9a-f]{64}",
    flags=re.ASCII,
)


class QuoteAssessmentStatus(StrEnum):
    VALIDATED_WAITING = "validated_waiting"
    VALIDATED_TRIGGER_REACHED = "validated_trigger_reached"
    MARKET_CLOSED = "market_closed"
    PROVIDER_FAILED = "provider_failed"
    INVALID_QUOTE = "invalid_quote"
    FUTURE_QUOTE = "future_quote"
    STALE_QUOTE = "stale_quote"
    SPREAD_TOO_WIDE = "spread_too_wide"
    SETUP_INVALIDATED = "setup_invalidated"
    ENTRY_SLIPPAGE_EXCEEDED = "entry_slippage_exceeded"


class InvalidUsQuoteActionabilityInputError(ValueError):
    @override
    def __str__(self) -> str:
        return "미국주식 현재 호가 평가 시각이 유효하지 않습니다"


class UsQuoteSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    quote_id: str
    provider: Literal["kis"] = "kis"
    market_id: Literal[MarketId.US_EQUITIES] = MarketId.US_EQUITIES
    exchange: str
    symbol: str
    provider_observed_at: dt.datetime
    received_at: dt.datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    spread_bps: Decimal

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        prices_valid = (
            _positive_finite(self.bid)
            and _positive_finite(self.ask)
            and self.bid <= self.ask
        )
        if (
            not prices_valid
            or self.bid_size < 0
            or self.ask_size < 0
            or _EXCHANGE.fullmatch(self.exchange) is None
            or _US_SYMBOL.fullmatch(self.symbol) is None
            or not _aware(self.provider_observed_at)
            or not _aware(self.received_at)
            or not self.spread_bps.is_finite()
            or self.spread_bps < 0
            or self.spread_bps != _spread_bps(self.bid, self.ask)
            or self.quote_id
            != _quote_identity(
                exchange=self.exchange,
                symbol=self.symbol,
                provider_observed_at=self.provider_observed_at,
                received_at=self.received_at,
                bid=self.bid,
                ask=self.ask,
                bid_size=self.bid_size,
                ask_size=self.ask_size,
            )
        ):
            raise ValueError("invalid US quote snapshot")
        return self


class QuoteActionabilityAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    assessment_id: str
    base_signal_id: str
    scan_started_at: dt.datetime
    evaluated_at: dt.datetime
    status: QuoteAssessmentStatus
    quote_id: str | None = None
    derived_signal_id: str | None = None

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        validated = {
            QuoteAssessmentStatus.VALIDATED_WAITING,
            QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED,
        }
        quote_required = {
            QuoteAssessmentStatus.FUTURE_QUOTE,
            QuoteAssessmentStatus.STALE_QUOTE,
            QuoteAssessmentStatus.SPREAD_TOO_WIDE,
            QuoteAssessmentStatus.ENTRY_SLIPPAGE_EXCEEDED,
        }
        quote_forbidden = {
            QuoteAssessmentStatus.MARKET_CLOSED,
            QuoteAssessmentStatus.PROVIDER_FAILED,
            QuoteAssessmentStatus.INVALID_QUOTE,
        }
        geometry_valid = (
            self.quote_id is not None and self.derived_signal_id is not None
            if self.status in validated
            else self.quote_id is not None and self.derived_signal_id is None
            if self.status in quote_required
            else self.quote_id is None and self.derived_signal_id is None
            if self.status in quote_forbidden
            else self.derived_signal_id is None
        )
        if (
            _ASSESSMENT_ID.fullmatch(self.assessment_id) is None
            or not _canonical_text(self.base_signal_id, max_length=512)
            or not _aware(self.scan_started_at)
            or not _aware(self.evaluated_at)
            or self.scan_started_at > self.evaluated_at
            or (self.quote_id is not None and _QUOTE_ID.fullmatch(self.quote_id) is None)
            or (
                self.derived_signal_id is not None
                and _DERIVED_SIGNAL_ID.fullmatch(self.derived_signal_id) is None
            )
            or not geometry_valid
            or self.assessment_id
            != _assessment_identity(
                base_signal_id=self.base_signal_id,
                scan_started_at=self.scan_started_at,
            )
        ):
            raise ValueError("invalid quote actionability assessment")
        return self


@dataclass(frozen=True, slots=True)
class UsQuoteActionabilityDecision:
    snapshot: UsQuoteSnapshot | None
    assessment: QuoteActionabilityAssessment
    derived_publication: TradeSignalPublication | None


def assess_us_quote(
    base: TradeSignalPublication,
    quote: KisUsLevelOneQuote,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> UsQuoteActionabilityDecision:
    preflight = preflight_quote_assessment(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
    )
    if preflight is not None:
        return UsQuoteActionabilityDecision(
            snapshot=None,
            assessment=preflight,
            derived_publication=None,
        )
    if quote.symbol != base.signal.symbol:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.INVALID_QUOTE,
        )

    snapshot: UsQuoteSnapshot | None = None
    with suppress(ArithmeticError, ValidationError, ValueError):
        snapshot = _snapshot(quote)
    if snapshot is None:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.INVALID_QUOTE,
        )

    provider_at = snapshot.provider_observed_at.astimezone(NEW_YORK)
    received_at = snapshot.received_at.astimezone(NEW_YORK)
    evaluated = evaluated_at.astimezone(NEW_YORK)
    if provider_at > evaluated or provider_at > received_at or received_at > evaluated:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.FUTURE_QUOTE,
            snapshot=snapshot,
        )
    if provider_at.date() != evaluated.date() or not _in_regular_session(provider_at):
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.STALE_QUOTE,
            snapshot=snapshot,
        )
    if evaluated - provider_at >= QUOTE_FRESHNESS:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.STALE_QUOTE,
            snapshot=snapshot,
        )
    if snapshot.spread_bps > MAX_QUOTE_SPREAD_BPS:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.SPREAD_TOO_WIDE,
            snapshot=snapshot,
        )
    if snapshot.bid <= base.signal.stop_price:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.SETUP_INVALIDATED,
            snapshot=snapshot,
        )
    maximum_entry = base.signal.entry_price * (
        Decimal(1) + MAX_ENTRY_SLIPPAGE_BPS / BASIS_POINTS
    )
    if snapshot.ask > maximum_entry:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.ENTRY_SLIPPAGE_EXCEEDED,
            snapshot=snapshot,
        )

    status = (
        QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED
        if snapshot.ask >= base.signal.entry_price
        else QuoteAssessmentStatus.VALIDATED_WAITING
    )
    derived = _derived_publication(base, snapshot, evaluated_at=evaluated_at)
    return _decision(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
        status=status,
        snapshot=snapshot,
        derived=derived,
    )


def preflight_quote_assessment(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> QuoteActionabilityAssessment | None:
    _validate_control_times(scan_started_at, evaluated_at)
    if not _base_is_current(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
    ):
        return _assessment(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.SETUP_INVALIDATED,
        )
    if not _in_regular_session(evaluated_at):
        return _assessment(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.MARKET_CLOSED,
        )
    return None


def provider_failed_assessment(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> QuoteActionabilityAssessment:
    _validate_control_times(scan_started_at, evaluated_at)
    return _assessment(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
        status=QuoteAssessmentStatus.PROVIDER_FAILED,
    )


def _decision(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
    status: QuoteAssessmentStatus,
    snapshot: UsQuoteSnapshot | None = None,
    derived: TradeSignalPublication | None = None,
) -> UsQuoteActionabilityDecision:
    return UsQuoteActionabilityDecision(
        snapshot=snapshot,
        assessment=_assessment(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=status,
            snapshot=snapshot,
            derived=derived,
        ),
        derived_publication=derived,
    )


def _assessment(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
    status: QuoteAssessmentStatus,
    snapshot: UsQuoteSnapshot | None = None,
    derived: TradeSignalPublication | None = None,
) -> QuoteActionabilityAssessment:
    quote_id = None if snapshot is None else snapshot.quote_id
    derived_signal_id = None if derived is None else derived.signal.signal_id
    return QuoteActionabilityAssessment(
        assessment_id=_assessment_identity(
            base_signal_id=base.signal.signal_id,
            scan_started_at=scan_started_at,
        ),
        base_signal_id=base.signal.signal_id,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
        status=status,
        quote_id=quote_id,
        derived_signal_id=derived_signal_id,
    )


def _snapshot(quote: KisUsLevelOneQuote) -> UsQuoteSnapshot:
    spread_bps = _spread_bps(quote.bid, quote.ask)
    return UsQuoteSnapshot(
        quote_id=_quote_identity(
            exchange=quote.exchange,
            symbol=quote.symbol,
            provider_observed_at=quote.provider_observed_at,
            received_at=quote.received_at,
            bid=quote.bid,
            ask=quote.ask,
            bid_size=quote.bid_size,
            ask_size=quote.ask_size,
        ),
        exchange=quote.exchange,
        symbol=quote.symbol,
        provider_observed_at=quote.provider_observed_at,
        received_at=quote.received_at,
        bid=quote.bid,
        ask=quote.ask,
        bid_size=quote.bid_size,
        ask_size=quote.ask_size,
        spread_bps=spread_bps,
    )


def _derived_publication(
    base: TradeSignalPublication,
    snapshot: UsQuoteSnapshot,
    *,
    evaluated_at: dt.datetime,
) -> TradeSignalPublication:
    signal = base.signal
    quote_valid_until = snapshot.provider_observed_at + QUOTE_FRESHNESS
    valid_until = min(signal.valid_until, quote_valid_until)
    evidence = {
        item.canonical_id: item
        for item in (
            *signal.evidence_refs,
            EvidenceRef(
                namespace="signal/conditional",
                record_id=signal.signal_id,
                observed_at=signal.observed_at,
            ),
            EvidenceRef(
                namespace="quote/snapshot",
                record_id=snapshot.quote_id,
                observed_at=snapshot.provider_observed_at,
            ),
        )
    }
    derived_signal = TradeSignalEnvelope(
        signal_id=_derived_signal_identity(signal.signal_id, snapshot.quote_id),
        strategy_lane=signal.strategy_lane,
        producer_strategy_version=signal.producer_strategy_version,
        symbol=signal.symbol,
        observed_at=evaluated_at,
        valid_until=valid_until,
        side=signal.side,
        entry_type=signal.entry_type,
        entry_price=signal.entry_price,
        stop_price=signal.stop_price,
        targets=signal.targets,
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule=signal.invalidation_rule,
        rationale=signal.rationale,
        evidence_refs=tuple(evidence[key] for key in sorted(evidence)),
        quote_validation=QuoteValidation(
            bid=snapshot.bid,
            ask=snapshot.ask,
            observed_at=snapshot.provider_observed_at,
            valid_until=quote_valid_until,
            spread_bps=snapshot.spread_bps,
            max_slippage_bps=MAX_QUOTE_SPREAD_BPS,
        ),
        opportunity_id=signal.opportunity_id,
    )
    return TradeSignalPublication(
        published_at=evaluated_at,
        signal=derived_signal,
    )


def _base_is_current(
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


def _validate_control_times(
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> None:
    if (
        not _aware(scan_started_at)
        or not _aware(evaluated_at)
        or scan_started_at > evaluated_at
    ):
        raise InvalidUsQuoteActionabilityInputError


def _in_regular_session(value: dt.datetime) -> bool:
    current = value.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    return bounds is not None and bounds[0] <= current < bounds[1]


def _spread_bps(bid: Decimal, ask: Decimal) -> Decimal:
    return (ask - bid) / ((ask + bid) / Decimal(2)) * BASIS_POINTS


def _quote_identity(
    *,
    exchange: str,
    symbol: str,
    provider_observed_at: dt.datetime,
    received_at: dt.datetime,
    bid: Decimal,
    ask: Decimal,
    bid_size: int,
    ask_size: int,
) -> str:
    return _identity(
        "us-quote:",
        {
            "provider": "kis",
            "exchange": exchange,
            "symbol": symbol,
            "provider_observed_at": _timestamp_text(provider_observed_at),
            "received_at": _timestamp_text(received_at),
            "bid": _decimal_text(bid),
            "ask": _decimal_text(ask),
            "bid_size": bid_size,
            "ask_size": ask_size,
        },
    )


def _assessment_identity(
    *,
    base_signal_id: str,
    scan_started_at: dt.datetime,
) -> str:
    return _identity(
        "us-quote-assessment:",
        {
            "base_signal_id": base_signal_id,
            "scan_started_at": _timestamp_text(scan_started_at),
        },
    )


def _derived_signal_identity(base_signal_id: str, quote_id: str) -> str:
    return _identity(
        "us-quote-signal:",
        {"base_signal_id": base_signal_id, "quote_id": quote_id},
    )


def _identity(prefix: str, material: dict[str, str | int | None]) -> str:
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"{prefix}{hashlib.sha256(encoded).hexdigest()}"


def _timestamp_text(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat()


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _canonical_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(character in value for character in "\r\n\t")
    )


def _positive_finite(value: Decimal) -> bool:
    return value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
