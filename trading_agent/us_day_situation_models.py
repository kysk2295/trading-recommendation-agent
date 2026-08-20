from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import EvidenceRef

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9./-]{0,19}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_QUOTE_ID = re.compile(r"^us-quote:[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ALLOWED_INFERENCE_RULES = frozenset(
    {
        "bar_quote_absorption_proxy_v1",
        "cross_symbol_relative_strength_v1",
    }
)
_UNOBSERVED_FLOW_PHRASES = (
    "accumulation",
    "distribution",
    "institutional",
    "smart money",
    "buying pressure",
    "selling pressure",
    "whale",
)


class ThemeState(StrEnum):
    EMERGING = "emerging"
    ACTIVE = "active"
    AGING = "aging"


class FlowObservationKind(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"


class EvidenceBoundClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    observation_kind: FlowObservationKind
    inference_rule: str | None = Field(default=None, min_length=1, max_length=500)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        lowered = self.text.casefold()
        makes_unobserved_flow_claim = any(item in lowered for item in _UNOBSERVED_FLOW_PHRASES)
        labeled_inference = "inferred" in lowered or "proxy" in lowered
        if (
            not _inference_valid(self.observation_kind, self.inference_rule)
            or (makes_unobserved_flow_claim and self.observation_kind is FlowObservationKind.OBSERVED)
            or (makes_unobserved_flow_claim and not labeled_inference)
        ):
            raise ValueError("invalid evidence-bound claim")
        _require_canonical_refs(self.evidence_refs)
        return self


class CatalystEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    headline: str = Field(min_length=1, max_length=1_000)
    source: str = Field(min_length=1, max_length=64)
    symbols: tuple[str, ...] = Field(min_length=1, max_length=64)
    published_at: AwareDatetime
    received_at: AwareDatetime
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @field_validator("symbols", mode="before")
    @classmethod
    def canonical_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_catalyst(self) -> Self:
        if (
            self.headline != self.headline.strip()
            or any(_SYMBOL.fullmatch(item) is None for item in self.symbols)
            or self.published_at > self.received_at
        ):
            raise ValueError("invalid catalyst evidence")
        _require_canonical_refs(self.evidence_refs)
        return self


class ObservableFlow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    observation_kind: FlowObservationKind
    relative_volume: Decimal = Field(ge=0)
    dollar_volume: Decimal = Field(ge=0)
    spread_bps: Decimal = Field(ge=0)
    bid_size: int = Field(ge=0)
    ask_size: int = Field(ge=0)
    vwap_relation: Literal["above", "below", "crossing", "unavailable"]
    breakout_absorption_proxy: Decimal | None = Field(default=None, ge=0)
    cross_symbol_relative_strength: Decimal | None = None
    inference_rule: str | None = Field(default=None, min_length=1, max_length=500)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_flow(self) -> Self:
        finite = (
            self.relative_volume.is_finite()
            and self.dollar_volume.is_finite()
            and self.spread_bps.is_finite()
            and (self.breakout_absorption_proxy is None or self.breakout_absorption_proxy.is_finite())
            and (self.cross_symbol_relative_strength is None or self.cross_symbol_relative_strength.is_finite())
        )
        if not finite or not _inference_valid(self.observation_kind, self.inference_rule):
            raise ValueError("invalid observable flow")
        _require_canonical_refs(self.evidence_refs)
        return self


class LeaderCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str
    rank: int = Field(ge=1)
    leader_score: Decimal
    flow: ObservableFlow
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_leader(self) -> Self:
        if _SYMBOL.fullmatch(self.symbol) is None or not self.leader_score.is_finite():
            raise ValueError("invalid leader candidate")
        _require_canonical_refs(self.evidence_refs)
        return self


class ThemeMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: ThemeState
    symbols: tuple[str, ...] = Field(min_length=1)
    keywords: tuple[str, ...] = Field(min_length=1, max_length=12)
    catalysts: tuple[CatalystEvidence, ...] = Field(min_length=1)
    leaders: tuple[LeaderCandidate, ...] = Field(min_length=1)
    claims: tuple[EvidenceBoundClaim, ...] = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_theme(self) -> Self:
        ranks = tuple(item.rank for item in self.leaders)
        if (
            self.symbols != tuple(sorted(set(self.symbols)))
            or any(_SYMBOL.fullmatch(item) is None for item in self.symbols)
            or self.keywords != tuple(sorted(set(self.keywords)))
            or ranks != tuple(range(1, len(self.leaders) + 1))
            or any(item.symbol not in self.symbols for item in self.leaders)
        ):
            raise ValueError("invalid theme map")
        _require_canonical_refs(self.evidence_refs)
        return self


class UsDaySituationMap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    market_id: Literal[MarketId.US_EQUITIES] = MarketId.US_EQUITIES
    session_id: str = Field(pattern=r"^XNYS-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    session_date: dt.date
    completed_bar_at: AwareDatetime
    evaluated_at: AwareDatetime
    themes: tuple[ThemeMap, ...] = Field(min_length=1)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    order_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_map(self) -> Self:
        theme_ids = tuple(item.theme_id for item in self.themes)
        if (
            self.session_id != f"XNYS-{self.session_date.isoformat()}"
            or self.completed_bar_at >= self.evaluated_at
            or theme_ids != tuple(sorted(set(theme_ids)))
        ):
            raise ValueError("invalid US day situation map")
        _require_canonical_refs(self.evidence_refs)
        return self


def _inference_valid(kind: FlowObservationKind, rule: str | None) -> bool:
    return (kind is FlowObservationKind.OBSERVED and rule is None) or (
        kind is FlowObservationKind.INFERRED and rule in _ALLOWED_INFERENCE_RULES
    )


def _require_canonical_refs(refs: tuple[EvidenceRef, ...]) -> None:
    identities = tuple(item.canonical_id for item in refs)
    if identities != tuple(sorted(set(identities))) or any(not _known_evidence_ref(item) for item in refs):
        raise ValueError("evidence references are not canonical")


def _known_evidence_ref(ref: EvidenceRef) -> bool:
    match ref.namespace:
        case "alpaca/news/article" | "research/current_bar":
            return _HEX64.fullmatch(ref.record_id) is not None
        case "quote/snapshot":
            return _QUOTE_ID.fullmatch(ref.record_id) is not None
        case "scanner/opportunity" | "market/context":
            return _OPAQUE_ID.fullmatch(ref.record_id) is not None
        case _:
            return False


__all__ = (
    "CatalystEvidence",
    "EvidenceBoundClaim",
    "FlowObservationKind",
    "LeaderCandidate",
    "ObservableFlow",
    "ThemeMap",
    "ThemeState",
    "UsDaySituationMap",
)
