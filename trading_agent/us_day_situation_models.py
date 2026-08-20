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


class ThemeState(StrEnum):
    EMERGING = "emerging"
    ACTIVE = "active"
    AGING = "aging"


class FlowObservationKind(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"


class FlowInferenceKind(StrEnum):
    BREAKOUT_ABSORPTION_PROXY = "breakout_absorption_proxy"
    CROSS_SYMBOL_RELATIVE_STRENGTH = "cross_symbol_relative_strength"


class SituationClaimKind(StrEnum):
    SHARED_CURRENT_SESSION_CATALYST = "shared_current_session_catalyst"


class CatalystClaimEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbols: tuple[str, ...] = Field(min_length=1, max_length=64)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @field_validator("symbols", mode="before")
    @classmethod
    def canonical_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if any(_SYMBOL.fullmatch(item) is None for item in self.symbols) or any(
            item.namespace != "alpaca/news/article" for item in self.evidence_refs
        ):
            raise ValueError("invalid catalyst claim event")
        _require_canonical_refs(self.evidence_refs)
        return self


class EvidenceBoundClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SituationClaimKind
    events: tuple[CatalystClaimEvent, ...] = Field(min_length=1)
    observation_kind: Literal[FlowObservationKind.OBSERVED] = FlowObservationKind.OBSERVED
    inference_rule: Literal[None] = None
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        event_ids = tuple(item.event_id for item in self.events)
        expected_refs = _canonical_refs(tuple(ref for item in self.events for ref in item.evidence_refs))
        if (
            self.kind is not SituationClaimKind.SHARED_CURRENT_SESSION_CATALYST
            or event_ids != tuple(sorted(set(event_ids)))
            or len(self.symbols) < 2
            or self.evidence_refs != expected_refs
        ):
            raise ValueError("invalid evidence-bound claim")
        _require_canonical_refs(self.evidence_refs)
        return self

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({symbol for item in self.events for symbol in item.symbols}))

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(item.event_id for item in self.events)

    @property
    def text(self) -> str:
        noun = "event" if len(self.events) == 1 else "events"
        symbols = ", ".join(self.symbols)
        return f"Shared current-session catalyst links {symbols} from {len(self.events)} verified {noun}."


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
        if (
            not finite
            or not _inference_valid(self.observation_kind, self.inference_rule)
            or not _flow_inference_compatible(self)
        ):
            raise ValueError("invalid observable flow")
        _require_canonical_refs(self.evidence_refs)
        return self


class FlowInference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    kind: FlowInferenceKind
    value: Decimal
    rule: Literal["bar_quote_absorption_proxy_v1", "cross_symbol_relative_strength_v1"]
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_inference(self) -> Self:
        expected_rule = (
            "bar_quote_absorption_proxy_v1"
            if self.kind is FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY
            else "cross_symbol_relative_strength_v1"
        )
        if (
            not self.value.is_finite()
            or self.rule != expected_rule
            or (self.kind is FlowInferenceKind.BREAKOUT_ABSORPTION_PROXY and self.value < 0)
            or not _inference_evidence_valid(
                FlowObservationKind.INFERRED,
                self.rule,
                self.evidence_refs,
            )
        ):
            raise ValueError("invalid flow inference")
        _require_canonical_refs(self.evidence_refs)
        return self


class LeaderCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str
    rank: int = Field(ge=1)
    leader_score: Decimal
    flow: ObservableFlow
    inferences: tuple[FlowInference, ...] = Field(max_length=2)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_leader(self) -> Self:
        inference_kinds = tuple(item.kind.value for item in self.inferences)
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or not self.leader_score.is_finite()
            or inference_kinds != tuple(sorted(set(inference_kinds)))
        ):
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
        claim = self.claims[0] if len(self.claims) == 1 else None
        catalysts = {item.event_id: item for item in self.catalysts}
        claim_events_match = claim is not None and all(
            (catalyst := catalysts.get(item.event_id)) is not None
            and item.symbols == catalyst.symbols
            and item.evidence_refs == catalyst.evidence_refs
            for item in claim.events
        )
        if (
            self.symbols != tuple(sorted(set(self.symbols)))
            or any(_SYMBOL.fullmatch(item) is None for item in self.symbols)
            or self.keywords != tuple(sorted(set(self.keywords)))
            or ranks != tuple(range(1, len(self.leaders) + 1))
            or any(item.symbol not in self.symbols for item in self.leaders)
            or claim is None
            or claim.symbols != self.symbols
            or claim.event_ids != tuple(sorted(catalysts))
            or not claim_events_match
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


def _flow_inference_compatible(flow: ObservableFlow) -> bool:
    if flow.observation_kind is FlowObservationKind.OBSERVED:
        return (
            flow.inference_rule is None
            and flow.breakout_absorption_proxy is None
            and flow.cross_symbol_relative_strength is None
        )
    if not _inference_evidence_valid(flow.observation_kind, flow.inference_rule, flow.evidence_refs):
        return False
    match flow.inference_rule:
        case "bar_quote_absorption_proxy_v1":
            return flow.breakout_absorption_proxy is not None and flow.cross_symbol_relative_strength is None
        case "cross_symbol_relative_strength_v1":
            return flow.cross_symbol_relative_strength is not None and flow.breakout_absorption_proxy is None
        case _:
            return False


def _inference_evidence_valid(
    kind: FlowObservationKind,
    rule: str | None,
    refs: tuple[EvidenceRef, ...],
) -> bool:
    if kind is FlowObservationKind.OBSERVED:
        return True
    bar_record_ids = {item.record_id for item in refs if item.namespace == "research/current_bar"}
    quote_count = sum(item.namespace == "quote/snapshot" for item in refs)
    match rule:
        case "bar_quote_absorption_proxy_v1":
            return bool(bar_record_ids) and quote_count >= 1
        case "cross_symbol_relative_strength_v1":
            return len(bar_record_ids) >= 2
        case _:
            return False


def _require_canonical_refs(refs: tuple[EvidenceRef, ...]) -> None:
    identities = tuple(item.canonical_id for item in refs)
    if identities != tuple(sorted(set(identities))) or any(not _known_evidence_ref(item) for item in refs):
        raise ValueError("evidence references are not canonical")


def _canonical_refs(refs: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    by_id = {item.canonical_id: item for item in refs}
    return tuple(by_id[item] for item in sorted(by_id))


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
    "CatalystClaimEvent",
    "CatalystEvidence",
    "EvidenceBoundClaim",
    "FlowInference",
    "FlowInferenceKind",
    "FlowObservationKind",
    "LeaderCandidate",
    "ObservableFlow",
    "SituationClaimKind",
    "ThemeMap",
    "ThemeState",
    "UsDaySituationMap",
)
