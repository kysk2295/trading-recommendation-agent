from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2
from trading_agent.kr_intraday_market_gate import (
    KrIntradayGateReason,
    KrIntradayGateStatus,
    KrMarketConstraintSnapshot,
    assess_kr_shadow_entry,
)
from trading_agent.kr_theme_lane import (
    KR_THEME_LEADER_VWAP_RECLAIM_LANE,
    KR_THEME_OPPORTUNITY_LANE,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    OpportunitySnapshot,
    QuoteValidation,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MARKET_VALIDITY = dt.timedelta(seconds=5)


class InvalidKrThemeDaySignalError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day shadow signal input is invalid"


class KrThemeDaySetup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    setup_id: str
    opportunity_id: str
    producer_strategy_version: str
    symbol: str
    observed_at: dt.datetime
    valid_until: dt.datetime
    stop_price: Decimal
    targets: tuple[TradeTarget, ...]
    max_slippage_bps: Decimal
    invalidation_rule: str
    rationale: str
    evidence_refs: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def validate_setup(self) -> Self:
        evidence_ids = tuple(item.canonical_id for item in self.evidence_refs)
        target_labels = tuple(item.label for item in self.targets)
        if (
            _IDENTIFIER.fullmatch(self.setup_id) is None
            or _IDENTIFIER.fullmatch(self.opportunity_id) is None
            or _IDENTIFIER.fullmatch(self.producer_strategy_version) is None
            or not is_kr_instrument_symbol_v2(self.symbol)
            or not _aware(self.observed_at)
            or not _aware(self.valid_until)
            or self.valid_until <= self.observed_at
            or not _positive(self.stop_price)
            or not self.targets
            or target_labels != tuple(sorted(set(target_labels)))
            or not _positive(self.max_slippage_bps)
            or not _canonical_text(self.invalidation_rule)
            or not _canonical_text(self.rationale)
            or not self.evidence_refs
            or evidence_ids != tuple(sorted(set(evidence_ids)))
            or any(item.observed_at > self.observed_at for item in self.evidence_refs)
        ):
            raise InvalidKrThemeDaySignalError
        return self


@dataclass(frozen=True, slots=True)
class KrThemeDaySignalDecision:
    signal: TradeSignalEnvelope | None
    gate_reasons: tuple[KrIntradayGateReason, ...]
    spread_bps: Decimal | None
    spread_eligible: bool


def project_kr_theme_day_shadow_signal(
    opportunity: OpportunitySnapshot,
    market: KrMarketConstraintSnapshot,
    setup: KrThemeDaySetup,
    *,
    evaluated_at: dt.datetime,
) -> KrThemeDaySignalDecision:
    opportunity, market, setup = _validated_inputs(opportunity, market, setup)
    _require_lineage(opportunity, market, setup, evaluated_at)
    gate = assess_kr_shadow_entry(market, evaluated_at)
    if gate.status is KrIntradayGateStatus.BLOCKED:
        return KrThemeDaySignalDecision(None, gate.reasons, None, False)
    if market.bid_price is None or market.ask_price is None:
        raise InvalidKrThemeDaySignalError
    midpoint = (market.bid_price + market.ask_price) / Decimal(2)
    spread_bps = (market.ask_price - market.bid_price) / midpoint * Decimal(10_000)
    if spread_bps > setup.max_slippage_bps:
        return KrThemeDaySignalDecision(None, (), spread_bps, False)
    valid_until = min(
        opportunity.valid_until,
        setup.valid_until,
        market.observed_at + _MARKET_VALIDITY,
    )
    if (
        valid_until <= evaluated_at
        or setup.stop_price >= market.ask_price
        or any(target.price <= market.ask_price for target in setup.targets)
    ):
        raise InvalidKrThemeDaySignalError
    evidence = _evidence(opportunity, market, setup)
    signal = TradeSignalEnvelope(
        signal_id=_signal_id(opportunity, market, setup, evaluated_at),
        strategy_lane=KR_THEME_LEADER_VWAP_RECLAIM_LANE,
        producer_strategy_version=setup.producer_strategy_version,
        symbol=setup.symbol,
        observed_at=evaluated_at,
        valid_until=valid_until,
        side=SignalSide.LONG,
        entry_type=SignalEntryType.LIMIT,
        entry_price=market.ask_price,
        stop_price=setup.stop_price,
        targets=setup.targets,
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule=setup.invalidation_rule,
        rationale=setup.rationale,
        evidence_refs=evidence,
        quote_validation=QuoteValidation(
            bid=market.bid_price,
            ask=market.ask_price,
            observed_at=market.observed_at,
            valid_until=market.observed_at + _MARKET_VALIDITY,
            spread_bps=spread_bps,
            max_slippage_bps=setup.max_slippage_bps,
        ),
        opportunity_id=opportunity.opportunity_id,
    )
    return KrThemeDaySignalDecision(signal, (), spread_bps, True)


def _validated_inputs(
    opportunity: OpportunitySnapshot,
    market: KrMarketConstraintSnapshot,
    setup: KrThemeDaySetup,
) -> tuple[OpportunitySnapshot, KrMarketConstraintSnapshot, KrThemeDaySetup]:
    try:
        return (
            OpportunitySnapshot.model_validate(opportunity.model_dump(mode="python")),
            KrMarketConstraintSnapshot.model_validate(market.model_dump(mode="python")),
            KrThemeDaySetup.model_validate(setup.model_dump(mode="python")),
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySignalError from None


def _require_lineage(
    opportunity: OpportunitySnapshot,
    market: KrMarketConstraintSnapshot,
    setup: KrThemeDaySetup,
    evaluated_at: dt.datetime,
) -> None:
    if (
        opportunity.strategy_lane != KR_THEME_OPPORTUNITY_LANE
        or opportunity.candidates[0].symbol != setup.symbol
        or opportunity.opportunity_id != setup.opportunity_id
        or market.symbol != setup.symbol
        or not _aware(evaluated_at)
        or not opportunity.observed_at <= setup.observed_at <= market.observed_at <= evaluated_at
        or evaluated_at >= opportunity.valid_until
        or evaluated_at >= setup.valid_until
    ):
        raise InvalidKrThemeDaySignalError


def _evidence(
    opportunity: OpportunitySnapshot,
    market: KrMarketConstraintSnapshot,
    setup: KrThemeDaySetup,
) -> tuple[EvidenceRef, ...]:
    evidence = (
        *opportunity.evidence_refs,
        *market.evidence_refs,
        *setup.evidence_refs,
        EvidenceRef(
            namespace="opportunity/snapshot",
            record_id=opportunity.opportunity_id,
            observed_at=opportunity.observed_at,
        ),
    )
    ids = tuple(item.canonical_id for item in evidence)
    if len(ids) != len(set(ids)):
        raise InvalidKrThemeDaySignalError
    return tuple(sorted(evidence, key=lambda item: item.canonical_id))


def _signal_id(
    opportunity: OpportunitySnapshot,
    market: KrMarketConstraintSnapshot,
    setup: KrThemeDaySetup,
    evaluated_at: dt.datetime,
) -> str:
    material = "|".join(
        (
            opportunity.opportunity_id,
            setup.setup_id,
            market.symbol,
            market.observed_at.isoformat(),
            evaluated_at.isoformat(),
        )
    )
    return f"kr-theme-shadow-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(character in value for character in "\r\n\t")


def _positive(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
