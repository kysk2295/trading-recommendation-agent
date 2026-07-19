from __future__ import annotations

from decimal import Decimal

from trading_agent.signal_contract_models import SignalActionability
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_quote_actionability_identity import derived_signal_identity
from trading_agent.us_quote_actionability_models import (
    BASIS_POINTS,
    MAX_ENTRY_SLIPPAGE_BPS,
    MAX_QUOTE_SPREAD_BPS,
    QUOTE_FRESHNESS,
    QuoteActionabilityAssessment,
    QuoteAssessmentStatus,
    UsQuoteSnapshot,
)
from trading_agent.us_quote_actionability_projection import quote_evidence_refs
from trading_agent.us_quote_actionability_rules import (
    base_is_current,
    in_regular_session,
    snapshot_terminal_status,
)


def quote_actionability_artifacts_match(
    base: TradeSignalPublication,
    snapshot: UsQuoteSnapshot,
    assessment: QuoteActionabilityAssessment,
    publication: TradeSignalPublication,
) -> bool:
    base_signal = base.signal
    signal = publication.signal
    validation = signal.quote_validation
    expected_status = (
        QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED
        if snapshot.ask >= signal.entry_price
        else QuoteAssessmentStatus.VALIDATED_WAITING
    )
    quote_valid_until = snapshot.provider_observed_at + QUOTE_FRESHNESS
    maximum_entry = signal.entry_price * (Decimal(1) + MAX_ENTRY_SLIPPAGE_BPS / BASIS_POINTS)
    provider_at = snapshot.provider_observed_at.astimezone(NEW_YORK)
    received_at = snapshot.received_at.astimezone(NEW_YORK)
    evaluated_at = assessment.evaluated_at.astimezone(NEW_YORK)
    return (
        validation is not None
        and base_is_current(
            base,
            scan_started_at=assessment.scan_started_at,
            evaluated_at=assessment.evaluated_at,
        )
        and assessment.base_signal_id == base_signal.signal_id
        and assessment.status is expected_status
        and assessment.quote_id == snapshot.quote_id
        and assessment.derived_signal_id == signal.signal_id
        and signal.signal_id == derived_signal_identity(assessment.base_signal_id, snapshot.quote_id)
        and signal.actionability is SignalActionability.CURRENT_QUOTE_VALIDATED
        and signal.strategy_lane == base_signal.strategy_lane
        and signal.producer_strategy_version == base_signal.producer_strategy_version
        and signal.symbol == base_signal.symbol == snapshot.symbol
        and signal.side is base_signal.side
        and signal.entry_type is base_signal.entry_type
        and signal.entry_price == base_signal.entry_price
        and signal.stop_price == base_signal.stop_price
        and signal.targets == base_signal.targets
        and signal.invalidation_rule == base_signal.invalidation_rule
        and signal.rationale == base_signal.rationale
        and signal.opportunity_id == base_signal.opportunity_id
        and publication.published_at == assessment.evaluated_at
        and signal.observed_at == assessment.evaluated_at
        and validation.bid == snapshot.bid
        and validation.ask == snapshot.ask
        and validation.observed_at == snapshot.provider_observed_at
        and validation.valid_until == quote_valid_until
        and validation.spread_bps == snapshot.spread_bps
        and validation.max_slippage_bps == MAX_QUOTE_SPREAD_BPS
        and signal.valid_until == min(base_signal.valid_until, quote_valid_until)
        and provider_at <= received_at <= evaluated_at
        and provider_at.date() == evaluated_at.date()
        and in_regular_session(provider_at)
        and in_regular_session(evaluated_at)
        and evaluated_at - provider_at < QUOTE_FRESHNESS
        and snapshot.spread_bps <= MAX_QUOTE_SPREAD_BPS
        and snapshot.bid > signal.stop_price
        and snapshot.ask <= maximum_entry
        and signal.evidence_refs == quote_evidence_refs(base, snapshot)
    )


def quote_actionability_assessment_matches(
    base: TradeSignalPublication,
    snapshot: UsQuoteSnapshot | None,
    assessment: QuoteActionabilityAssessment,
    derived: TradeSignalPublication | None,
) -> bool:
    if assessment.base_signal_id != base.signal.signal_id:
        return False
    if not base_is_current(
        base,
        scan_started_at=assessment.scan_started_at,
        evaluated_at=assessment.evaluated_at,
    ):
        return (
            assessment.status is QuoteAssessmentStatus.SETUP_INVALIDATED
            and snapshot is None
            and derived is None
            and assessment.quote_id is None
            and assessment.derived_signal_id is None
        )
    if not in_regular_session(assessment.evaluated_at):
        return (
            assessment.status is QuoteAssessmentStatus.MARKET_CLOSED
            and snapshot is None
            and derived is None
            and assessment.quote_id is None
            and assessment.derived_signal_id is None
        )
    if snapshot is None:
        return (
            assessment.status is QuoteAssessmentStatus.PROVIDER_FAILED
            and derived is None
            and assessment.quote_id is None
            and assessment.derived_signal_id is None
        )
    if snapshot.symbol != base.signal.symbol or assessment.quote_id != snapshot.quote_id:
        return False
    expected_status = snapshot_terminal_status(base, snapshot, evaluated_at=assessment.evaluated_at)
    if assessment.status is not expected_status:
        return False
    if expected_status in {
        QuoteAssessmentStatus.VALIDATED_WAITING,
        QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED,
    }:
        return derived is not None and quote_actionability_artifacts_match(base, snapshot, assessment, derived)
    return derived is None and assessment.derived_signal_id is None


__all__ = (
    "quote_actionability_artifacts_match",
    "quote_actionability_assessment_matches",
)
