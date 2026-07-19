from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.signal_contract_models import EvidenceRef
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability_models import (
    QuoteActionabilityAssessment,
    UsQuoteSnapshot,
    spread_bps,
)

_QUOTE_ID = re.compile(r"us-quote:[0-9a-f]{64}", flags=re.ASCII)
_US_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,19}", flags=re.ASCII)


class UsQuotePolicyEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "US quote policy evidence is invalid"


class UsQuotePolicyEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    quote_id: str
    evidence_ref: EvidenceRef
    symbol: str
    provider_observed_at: dt.datetime
    received_at: dt.datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int
    spread_bps: Decimal

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        prices_valid = _positive(self.bid) and _positive(self.ask) and self.bid <= self.ask
        if (
            _QUOTE_ID.fullmatch(self.quote_id) is None
            or _US_SYMBOL.fullmatch(self.symbol) is None
            or not _aware(self.provider_observed_at)
            or not _aware(self.received_at)
            or not prices_valid
            or self.bid_size < 0
            or self.ask_size < 0
            or not self.spread_bps.is_finite()
            or self.spread_bps < 0
            or self.spread_bps != spread_bps(self.bid, self.ask)
            or self.evidence_ref.observed_at != self.provider_observed_at
        ):
            raise UsQuotePolicyEvidenceError
        return self


@dataclass(frozen=True, slots=True)
class UsQuotePolicyDecision:
    evidence: UsQuotePolicyEvidence | None
    assessment: QuoteActionabilityAssessment
    derived_publication: TradeSignalPublication | None


def evidence_from_kis_snapshot(snapshot: UsQuoteSnapshot) -> UsQuotePolicyEvidence:
    return UsQuotePolicyEvidence(
        quote_id=snapshot.quote_id,
        evidence_ref=EvidenceRef(
            namespace="quote/snapshot",
            record_id=snapshot.quote_id,
            observed_at=snapshot.provider_observed_at,
        ),
        symbol=snapshot.symbol,
        provider_observed_at=snapshot.provider_observed_at,
        received_at=snapshot.received_at,
        bid=snapshot.bid,
        ask=snapshot.ask,
        bid_size=snapshot.bid_size,
        ask_size=snapshot.ask_size,
        spread_bps=snapshot.spread_bps,
    )


def _positive(value: Decimal) -> bool:
    return value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "UsQuotePolicyDecision",
    "UsQuotePolicyEvidence",
    "evidence_from_kis_snapshot",
)
