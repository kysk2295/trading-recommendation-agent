from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_models import AlpacaBar
from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.alpaca_news_opportunity_evidence import AlpacaNewsOpportunityEvidenceBundle
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.signal_contract_models import EvidenceRef, OpportunitySnapshot
from trading_agent.us_strategy_research_source import UsLatestQuote


class InvalidUsStrategyDayInputError(ValueError):
    @override
    def __str__(self) -> str:
        return "US strategy Day input is invalid"


class UsStrategyDayBar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)
    trade_count: int = Field(ge=0)
    vwap: float | None = None


class UsStrategyDayCandidateEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    symbol: str = Field(min_length=1)
    bars: tuple[UsStrategyDayBar, ...] = Field(min_length=2, max_length=6)
    quote: UsLatestQuote
    received_at: dt.datetime
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        timestamps = tuple(item.timestamp for item in self.bars)
        canonical_ids = tuple(item.canonical_id for item in self.evidence_refs)
        if (
            self.quote.symbol != self.symbol
            or timestamps != tuple(sorted(set(timestamps)))
            or self.received_at.tzinfo is None
            or self.received_at.utcoffset() is None
            or self.quote.observed_at > self.received_at
            or self.bars[-1].timestamp + dt.timedelta(minutes=1) > self.received_at
            or canonical_ids != tuple(sorted(set(canonical_ids)))
            or any(item.observed_at > self.received_at for item in self.evidence_refs)
        ):
            raise InvalidUsStrategyDayInputError
        return self


class UsStrategyDayInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[1] = 1
    opportunity: OpportunitySnapshot
    market_context: MarketContextSnapshot
    articles: tuple[AlpacaNewsArticle, ...]
    news_evidence: AlpacaNewsOpportunityEvidenceBundle
    candidates: tuple[UsStrategyDayCandidateEvidence, ...] = Field(min_length=1, max_length=4)
    materialized_at: dt.datetime

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        symbols = tuple(item.symbol for item in self.candidates)
        opportunity_symbols = tuple(item.symbol for item in self.opportunity.candidates)
        if (
            self.materialized_at.tzinfo is None
            or self.materialized_at.utcoffset() is None
            or symbols != opportunity_symbols
            or tuple(item.symbol for item in self.news_evidence.snapshots) != tuple(sorted(symbols))
            or self.opportunity.observed_at > self.materialized_at
            or self.market_context.observed_at > self.materialized_at
            or self.news_evidence.assessment.assessed_at > self.materialized_at.astimezone(dt.UTC)
            or any(item.received_at > self.materialized_at for item in self.candidates)
        ):
            raise InvalidUsStrategyDayInputError
        return self

    @property
    def input_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


def candidate_evidence(
    opportunity: OpportunitySnapshot,
    bars_by_symbol: dict[str, tuple[AlpacaBar, ...]],
    quotes_by_symbol: dict[str, UsLatestQuote],
    observed_at: dt.datetime,
) -> tuple[UsStrategyDayCandidateEvidence, ...]:
    result: list[UsStrategyDayCandidateEvidence] = []
    for candidate in opportunity.candidates:
        source_bars = tuple(
            item for item in bars_by_symbol[candidate.symbol] if item.timestamp + dt.timedelta(minutes=1) <= observed_at
        )[-6:]
        bars = tuple(UsStrategyDayBar.model_validate(item.model_dump(mode="python")) for item in source_bars)
        quote = quotes_by_symbol[candidate.symbol]
        material = "|".join(
            (
                candidate.symbol,
                *(item.model_dump_json(by_alias=True) for item in bars),
                quote.model_dump_json(),
            )
        )
        source_id = hashlib.sha256(material.encode()).hexdigest()
        refs = tuple(
            sorted(
                (
                    EvidenceRef(
                        namespace="bar/alpaca-sip",
                        record_id=f"{source_id}:{bars[-1].timestamp.isoformat()}",
                        observed_at=bars[-1].timestamp + dt.timedelta(minutes=1),
                    ),
                    EvidenceRef(
                        namespace="quote/alpaca-sip",
                        record_id=f"{source_id}:{quote.observed_at.isoformat()}",
                        observed_at=quote.observed_at,
                    ),
                ),
                key=lambda item: item.canonical_id,
            )
        )
        result.append(
            UsStrategyDayCandidateEvidence(
                symbol=candidate.symbol,
                bars=bars,
                quote=quote,
                received_at=observed_at,
                evidence_refs=refs,
            )
        )
    return tuple(result)


def spread_bps(evidence: UsStrategyDayCandidateEvidence) -> Decimal:
    bid = Decimal(str(evidence.quote.bid))
    ask = Decimal(str(evidence.quote.ask))
    return (ask - bid) / ((ask + bid) / Decimal(2)) * Decimal(10_000)


__all__ = (
    "InvalidUsStrategyDayInputError",
    "UsStrategyDayBar",
    "UsStrategyDayCandidateEvidence",
    "UsStrategyDayInput",
    "candidate_evidence",
    "spread_bps",
)
