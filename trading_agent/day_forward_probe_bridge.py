from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_strategy_capsule import (
    generated_evaluator_bundle_sha256,
    generated_protocol_bundle_sha256,
)
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.generated_strategy_protocol import BarFrame
from trading_agent.models import StrategySignal
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    QuoteValidation,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)

_TARGET_LABEL: Final = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_US_SYMBOL: Final = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,19}$")
_KR_SYMBOL: Final = re.compile(r"^[0-9]{6}$")
_HOST_EVIDENCE_NAMESPACES: Final = frozenset(
    {"day/cost_model", "day/strategy_capsule", "market/completed_bar"}
)


class InvalidDayForwardProbeBridgeInput(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


class DayTargetRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    label: str
    reward_risk_multiple: Decimal

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        if (
            _TARGET_LABEL.fullmatch(self.label) is None
            or not self.reward_risk_multiple.is_finite()
            or not Decimal("0") < self.reward_risk_multiple <= Decimal("10")
        ):
            raise InvalidDayForwardProbeBridgeInput("target_rule_invalid")
        return self


class DayTargetProjectionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    rules: tuple[DayTargetRule, ...] = Field(min_length=1, max_length=4)
    valid_for: dt.timedelta
    entry_type: SignalEntryType = SignalEntryType.STOP_TRIGGER

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        labels = tuple(rule.label for rule in self.rules)
        multiples = tuple(rule.reward_risk_multiple for rule in self.rules)
        if (
            labels != tuple(sorted(set(labels)))
            or multiples != tuple(sorted(set(multiples)))
            or not dt.timedelta(seconds=1) <= self.valid_for <= dt.timedelta(minutes=15)
        ):
            raise InvalidDayForwardProbeBridgeInput("target_policy_invalid")
        return self


class DayCompletedBarLineage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    market_id: MarketId
    bar: BarFrame
    valid_until: AwareDatetime
    record_id: str

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if (
            self.valid_until <= self.bar.timestamp
            or not self.record_id
            or self.record_id != self.record_id.strip()
            or len(self.record_id) > 512
            or any(character in self.record_id for character in "\r\n\t")
        ):
            raise InvalidDayForwardProbeBridgeInput("completed_bar_lineage_invalid")
        return self


class DaySignalBlockReason(StrEnum):
    BAR_FUTURE = "bar_future"
    BAR_STALE = "bar_stale"
    BAR_IDENTITY_MISMATCH = "bar_identity_mismatch"
    SYMBOL_INVALID = "symbol_invalid"
    CANDIDATE_INVALID = "candidate_invalid"
    CAPSULE_MARKET_MISMATCH = "capsule_market_mismatch"
    CAPSULE_NOT_ACTIVE = "capsule_not_active"
    CAPSULE_HOST_BUNDLE_MISMATCH = "capsule_host_bundle_mismatch"
    QUOTE_STALE = "quote_stale"
    SPREAD_TOO_WIDE = "spread_too_wide"
    EVIDENCE_INVALID = "evidence_invalid"
    TARGET_INVALID = "target_invalid"


class DaySignalBlocked(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    reason: DaySignalBlockReason


class DayTradeSignalProjectionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    capsule: StrategyCapsule
    candidate: StrategySignal
    completed_bar: DayCompletedBarLineage
    observed_at: AwareDatetime
    quote_validation: QuoteValidation
    target_policy: DayTargetProjectionPolicy
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)


type DayTradeSignalProjection = TradeSignalEnvelope | DaySignalBlocked


def project_day_trade_signal(
    request: DayTradeSignalProjectionRequest,
) -> DayTradeSignalProjection:
    capsule = request.capsule
    lineage = request.completed_bar
    bar = lineage.bar
    candidate = request.candidate
    quote = request.quote_validation
    if capsule.market_id is not lineage.market_id:
        return DaySignalBlocked(reason=DaySignalBlockReason.CAPSULE_MARKET_MISMATCH)
    if capsule.published_at >= bar.timestamp:
        return DaySignalBlocked(reason=DaySignalBlockReason.CAPSULE_NOT_ACTIVE)
    if (
        capsule.protocol_version != 1
        or capsule.protocol_sha256 != generated_protocol_bundle_sha256()
        or capsule.evaluator_sha256 != generated_evaluator_bundle_sha256()
    ):
        return DaySignalBlocked(reason=DaySignalBlockReason.CAPSULE_HOST_BUNDLE_MISMATCH)
    if bar.timestamp > request.observed_at:
        return DaySignalBlocked(reason=DaySignalBlockReason.BAR_FUTURE)
    if request.observed_at > lineage.valid_until:
        return DaySignalBlocked(reason=DaySignalBlockReason.BAR_STALE)
    if candidate.symbol != bar.symbol or candidate.timestamp != bar.timestamp:
        return DaySignalBlocked(reason=DaySignalBlockReason.BAR_IDENTITY_MISMATCH)
    if (
        not math.isfinite(candidate.entry)
        or not math.isfinite(candidate.stop)
        or candidate.entry <= candidate.stop
        or not candidate.strategy
        or candidate.strategy != candidate.strategy.strip()
        or len(candidate.strategy) > 256
        or not candidate.rationale
        or candidate.rationale != candidate.rationale.strip()
        or len(candidate.rationale) > 2_000
        or any(character in candidate.strategy + candidate.rationale for character in "\r\n\t")
    ):
        return DaySignalBlocked(reason=DaySignalBlockReason.CANDIDATE_INVALID)
    match lineage.market_id:
        case MarketId.US_EQUITIES:
            symbol_valid = _US_SYMBOL.fullmatch(bar.symbol) is not None
        case MarketId.KR_EQUITIES:
            symbol_valid = _KR_SYMBOL.fullmatch(bar.symbol) is not None
        case unreachable:
            assert_never(unreachable)
    if not symbol_valid:
        return DaySignalBlocked(reason=DaySignalBlockReason.SYMBOL_INVALID)
    if not quote.observed_at <= request.observed_at <= quote.valid_until:
        return DaySignalBlocked(reason=DaySignalBlockReason.QUOTE_STALE)
    if quote.spread_bps > quote.max_slippage_bps:
        return DaySignalBlocked(reason=DaySignalBlockReason.SPREAD_TOO_WIDE)
    evidence_ids = tuple(reference.canonical_id for reference in request.evidence_refs)
    if (
        evidence_ids != tuple(sorted(set(evidence_ids)))
        or any(reference.observed_at > bar.timestamp for reference in request.evidence_refs)
        or any(reference.namespace in _HOST_EVIDENCE_NAMESPACES for reference in request.evidence_refs)
    ):
        return DaySignalBlocked(reason=DaySignalBlockReason.EVIDENCE_INVALID)
    entry = Decimal(str(candidate.entry))
    risk = entry - Decimal(str(candidate.stop))
    targets = tuple(
        TradeTarget(
            label=rule.label,
            price=entry + (risk * rule.reward_risk_multiple),
        )
        for rule in request.target_policy.rules
    )
    if any(target.price <= entry for target in targets):
        return DaySignalBlocked(reason=DaySignalBlockReason.TARGET_INVALID)
    host_evidence = (
        EvidenceRef(
            namespace="day/cost_model",
            record_id=capsule.cost_model.model_id,
            observed_at=capsule.published_at,
        ),
        EvidenceRef(
            namespace="day/strategy_capsule",
            record_id=capsule.capsule_id,
            observed_at=capsule.published_at,
        ),
        EvidenceRef(
            namespace="market/completed_bar",
            record_id=lineage.record_id,
            observed_at=bar.timestamp,
        ),
    )
    evidence_refs = tuple(
        sorted((*host_evidence, *request.evidence_refs), key=lambda reference: reference.canonical_id)
    )
    signal_identity = hashlib.sha256(
        (
            f"{capsule.capsule_id}:{candidate.strategy}:{candidate.symbol}:"
            f"{candidate.timestamp.isoformat()}:{candidate.entry}:{candidate.stop}:"
            f"{request.observed_at.isoformat()}"
        ).encode()
    ).hexdigest()
    return TradeSignalEnvelope(
        signal_id=signal_identity,
        strategy_lane=StrategyLaneRef(
            market_id=lineage.market_id,
            agent_family=AgentFamily.DAY_TRADING,
            strategy_id=f"capsule_{capsule.hypothesis_version_id[:16]}",
        ),
        producer_strategy_version=capsule.hypothesis_version_id,
        symbol=bar.symbol,
        observed_at=request.observed_at,
        valid_until=min(
            request.observed_at + request.target_policy.valid_for,
            lineage.valid_until,
            quote.valid_until,
        ),
        side=SignalSide.LONG,
        entry_type=request.target_policy.entry_type,
        entry_price=entry,
        stop_price=Decimal(str(candidate.stop)),
        targets=targets,
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule=capsule.stop_rule,
        rationale=candidate.rationale,
        evidence_refs=evidence_refs,
        quote_validation=quote,
    )
