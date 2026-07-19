from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.research_identity_models import MarketId
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability_identity import assessment_identity, quote_identity

QUOTE_FRESHNESS: Final = dt.timedelta(seconds=5)
MAX_QUOTE_SPREAD_BPS: Final = Decimal("25")
MAX_ENTRY_SLIPPAGE_BPS: Final = Decimal("20")
BASIS_POINTS: Final = Decimal("10000")
_EXCHANGE: Final = re.compile(r"[A-Z0-9]{2,8}", flags=re.ASCII)
_US_SYMBOL: Final = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,19}", flags=re.ASCII)
_QUOTE_ID: Final = re.compile(r"us-quote:[0-9a-f]{64}", flags=re.ASCII)
_ASSESSMENT_ID: Final = re.compile(r"us-quote-assessment:[0-9a-f]{64}", flags=re.ASCII)
_DERIVED_SIGNAL_ID: Final = re.compile(r"us-quote-signal:[0-9a-f]{64}", flags=re.ASCII)


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


class UsQuoteSnapshotError(ValueError):
    @override
    def __str__(self) -> str:
        return "US quote snapshot is invalid"


class QuoteActionabilityAssessmentError(ValueError):
    @override
    def __str__(self) -> str:
        return "Quote actionability assessment is invalid"


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
        prices_valid = _positive_finite(self.bid) and _positive_finite(self.ask) and self.bid <= self.ask
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
            or self.spread_bps != spread_bps(self.bid, self.ask)
            or self.quote_id
            != quote_identity(
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
            raise UsQuoteSnapshotError
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
            or (self.derived_signal_id is not None and _DERIVED_SIGNAL_ID.fullmatch(self.derived_signal_id) is None)
            or not geometry_valid
            or self.assessment_id
            != assessment_identity(base_signal_id=self.base_signal_id, scan_started_at=self.scan_started_at)
        ):
            raise QuoteActionabilityAssessmentError
        return self


@dataclass(frozen=True, slots=True)
class UsQuoteActionabilityDecision:
    snapshot: UsQuoteSnapshot | None
    assessment: QuoteActionabilityAssessment
    derived_publication: TradeSignalPublication | None


def spread_bps(bid: Decimal, ask: Decimal) -> Decimal:
    return (ask - bid) / ((ask + bid) / Decimal(2)) * BASIS_POINTS


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


__all__ = (
    "BASIS_POINTS",
    "MAX_ENTRY_SLIPPAGE_BPS",
    "MAX_QUOTE_SPREAD_BPS",
    "QUOTE_FRESHNESS",
    "InvalidUsQuoteActionabilityInputError",
    "QuoteActionabilityAssessment",
    "QuoteAssessmentStatus",
    "UsQuoteActionabilityDecision",
    "UsQuoteSnapshot",
    "spread_bps",
)
