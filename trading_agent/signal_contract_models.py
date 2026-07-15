from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9_./-]{0,127}$")
_FEATURE_NAME = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_US_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,19}$")
_KR_SYMBOL = re.compile(r"^[0-9]{6}$")


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: str
    record_id: str
    observed_at: dt.datetime

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if (
            _NAMESPACE.fullmatch(self.namespace) is None
            or ".." in self.namespace
            or "//" in self.namespace
            or not _canonical_text(self.record_id, max_length=512)
            or not _aware(self.observed_at)
        ):
            raise ValueError("invalid evidence reference")
        return self

    @property
    def canonical_id(self) -> str:
        return f"{self.namespace}:{self.record_id}"


class FeatureValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: str

    @model_validator(mode="after")
    def validate_feature(self) -> Self:
        if _FEATURE_NAME.fullmatch(self.name) is None or not _canonical_text(self.value, max_length=512):
            raise ValueError("invalid feature value")
        return self


class SourceCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    observed_at: dt.datetime
    record_count: int
    complete: bool
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        failure_valid = (
            self.failure_reason is None
            if self.complete
            else self.failure_reason is not None and _IDENTIFIER.fullmatch(self.failure_reason) is not None
        )
        if (
            _FEATURE_NAME.fullmatch(self.source_id) is None
            or not _aware(self.observed_at)
            or self.record_count < 0
            or not failure_valid
        ):
            raise ValueError("invalid source coverage")
        return self


class OpportunityCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    rank: int
    score: Decimal
    features: tuple[FeatureValue, ...]

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        feature_names = tuple(feature.name for feature in self.features)
        if (
            not _canonical_symbol(self.symbol)
            or self.rank < 1
            or not self.score.is_finite()
            or not self.features
            or feature_names != tuple(sorted(set(feature_names)))
        ):
            raise ValueError("invalid opportunity candidate")
        return self


class OpportunitySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    opportunity_id: str
    strategy_lane: StrategyLaneRef
    producer_strategy_version: str
    observed_at: dt.datetime
    valid_until: dt.datetime
    candidates: tuple[OpportunityCandidate, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    source_coverage: tuple[SourceCoverage, ...]

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        evidence_ids = tuple(evidence.canonical_id for evidence in self.evidence_refs)
        source_ids = tuple(source.source_id for source in self.source_coverage)
        symbols = tuple(candidate.symbol for candidate in self.candidates)
        ranks = tuple(candidate.rank for candidate in self.candidates)
        if (
            _IDENTIFIER.fullmatch(self.opportunity_id) is None
            or _IDENTIFIER.fullmatch(self.producer_strategy_version) is None
            or self.strategy_lane.agent_family is not AgentFamily.OPPORTUNITY_MANAGER
            or not _aware(self.observed_at)
            or not _aware(self.valid_until)
            or self.valid_until <= self.observed_at
            or not self.candidates
            or ranks != tuple(range(1, len(self.candidates) + 1))
            or len(symbols) != len(set(symbols))
            or not all(_symbol_valid_for_market(symbol, self.strategy_lane.market_id) for symbol in symbols)
            or not self.evidence_refs
            or evidence_ids != tuple(sorted(set(evidence_ids)))
            or any(evidence.observed_at > self.observed_at for evidence in self.evidence_refs)
            or not self.source_coverage
            or source_ids != tuple(sorted(set(source_ids)))
            or any(
                not source.complete or source.observed_at > self.observed_at
                for source in self.source_coverage
            )
        ):
            raise ValueError("invalid opportunity snapshot")
        return self


class SignalSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class SignalEntryType(StrEnum):
    STOP_TRIGGER = "stop_trigger"
    LIMIT = "limit"


class SignalActionability(StrEnum):
    CONDITIONAL = "conditional"
    CURRENT_QUOTE_VALIDATED = "current_quote_validated"


class TradeTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    price: Decimal

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if _FEATURE_NAME.fullmatch(self.label) is None or not _positive_finite(self.price):
            raise ValueError("invalid trade target")
        return self


class QuoteValidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bid: Decimal
    ask: Decimal
    observed_at: dt.datetime
    valid_until: dt.datetime
    spread_bps: Decimal
    max_slippage_bps: Decimal

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        prices_valid = _positive_finite(self.bid) and _positive_finite(self.ask) and self.bid <= self.ask
        bounds_valid = (
            self.spread_bps.is_finite()
            and self.spread_bps >= 0
            and self.max_slippage_bps.is_finite()
            and self.max_slippage_bps > 0
        )
        calculated_spread = (
            (self.ask - self.bid) / ((self.ask + self.bid) / Decimal(2)) * Decimal(10_000)
            if prices_valid
            else Decimal("NaN")
        )
        if (
            not prices_valid
            or not bounds_valid
            or not _aware(self.observed_at)
            or not _aware(self.valid_until)
            or self.valid_until <= self.observed_at
            or abs(calculated_spread - self.spread_bps) > Decimal("0.05")
        ):
            raise ValueError("invalid quote validation")
        return self


class TradeSignalEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    signal_id: str
    strategy_lane: StrategyLaneRef
    producer_strategy_version: str
    symbol: str
    observed_at: dt.datetime
    valid_until: dt.datetime
    side: SignalSide
    entry_type: SignalEntryType
    entry_price: Decimal
    stop_price: Decimal
    targets: tuple[TradeTarget, ...]
    actionability: SignalActionability
    invalidation_rule: str
    rationale: str
    evidence_refs: tuple[EvidenceRef, ...]
    quote_validation: QuoteValidation | None = None
    opportunity_id: str | None = None

    @model_validator(mode="after")
    def validate_signal(self) -> Self:
        evidence_ids = tuple(evidence.canonical_id for evidence in self.evidence_refs)
        target_labels = tuple(target.label for target in self.targets)
        trading_families = {
            AgentFamily.DAY_TRADING,
            AgentFamily.SWING_TRADING,
            AgentFamily.SYSTEMATIC_QUANT,
        }
        directional_prices_valid = (
            self.stop_price < self.entry_price
            and all(target.price > self.entry_price for target in self.targets)
            if self.side is SignalSide.LONG
            else self.stop_price > self.entry_price
            and all(target.price < self.entry_price for target in self.targets)
        )
        identity_valid = (
            _canonical_text(self.signal_id, max_length=512)
            and _IDENTIFIER.fullmatch(self.producer_strategy_version) is not None
            and (self.opportunity_id is None or _canonical_text(self.opportunity_id, max_length=512))
        )
        quote_valid = self._quote_actionability_valid()
        if (
            not identity_valid
            or self.strategy_lane.agent_family not in trading_families
            or not _aware(self.observed_at)
            or not _aware(self.valid_until)
            or self.valid_until <= self.observed_at
            or not _symbol_valid_for_market(self.symbol, self.strategy_lane.market_id)
            or not _positive_finite(self.entry_price)
            or not _positive_finite(self.stop_price)
            or not self.targets
            or target_labels != tuple(sorted(set(target_labels)))
            or not directional_prices_valid
            or not _canonical_text(self.invalidation_rule, max_length=2000)
            or not _canonical_text(self.rationale, max_length=2000)
            or not self.evidence_refs
            or evidence_ids != tuple(sorted(set(evidence_ids)))
            or any(evidence.observed_at > self.observed_at for evidence in self.evidence_refs)
            or not quote_valid
        ):
            raise ValueError("invalid trade signal envelope")
        return self

    def _quote_actionability_valid(self) -> bool:
        quote = self.quote_validation
        if self.actionability is SignalActionability.CONDITIONAL:
            return quote is None
        return (
            quote is not None
            and quote.observed_at <= self.observed_at <= quote.valid_until
            and quote.spread_bps <= quote.max_slippage_bps
        )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(character in value for character in "\r\n\t")
    )


def _canonical_symbol(symbol: str) -> bool:
    return symbol == symbol.strip() and _US_SYMBOL.fullmatch(symbol) is not None


def _symbol_valid_for_market(symbol: str, market_id: MarketId) -> bool:
    pattern = _US_SYMBOL if market_id is MarketId.US_EQUITIES else _KR_SYMBOL
    return pattern.fullmatch(symbol) is not None


def _positive_finite(value: Decimal) -> bool:
    return value.is_finite() and value > 0
