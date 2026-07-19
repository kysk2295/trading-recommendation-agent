from __future__ import annotations

import datetime as dt
from contextlib import suppress

from pydantic import ValidationError

from trading_agent.kis_us_quote import KisUsLevelOneQuote
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability_evidence import (
    UsQuotePolicyDecision,
    UsQuotePolicyEvidence,
    evidence_from_kis_snapshot,
)
from trading_agent.us_quote_actionability_identity import assessment_identity
from trading_agent.us_quote_actionability_models import (
    QuoteActionabilityAssessment,
    QuoteAssessmentStatus,
    UsQuoteActionabilityDecision,
    UsQuoteSnapshot,
)
from trading_agent.us_quote_actionability_projection import (
    derived_publication_from_evidence,
    snapshot_from_kis,
)
from trading_agent.us_quote_actionability_rules import (
    base_is_current,
    in_regular_session,
    quote_terminal_status,
    validate_control_times,
)


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
        return UsQuoteActionabilityDecision(None, preflight, None)
    if quote.symbol != base.signal.symbol:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.PROVIDER_FAILED,
        )
    snapshot: UsQuoteSnapshot | None = None
    with suppress(ArithmeticError, ValidationError, ValueError):
        snapshot = snapshot_from_kis(quote)
    if snapshot is None:
        return _decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.PROVIDER_FAILED,
        )
    generic = assess_us_quote_evidence(
        base,
        evidence_from_kis_snapshot(snapshot),
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
    )
    return UsQuoteActionabilityDecision(
        snapshot if generic.evidence is not None else None,
        generic.assessment,
        generic.derived_publication,
    )


def assess_us_quote_evidence(
    base: TradeSignalPublication,
    evidence: UsQuotePolicyEvidence,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> UsQuotePolicyDecision:
    preflight = preflight_quote_assessment(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
    )
    if preflight is not None:
        return UsQuotePolicyDecision(None, preflight, None)
    if evidence.symbol != base.signal.symbol:
        return _policy_decision(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.PROVIDER_FAILED,
        )
    status = quote_terminal_status(base, evidence, evaluated_at=evaluated_at)
    derived = (
        derived_publication_from_evidence(base, evidence, evaluated_at=evaluated_at)
        if status
        in {
            QuoteAssessmentStatus.VALIDATED_WAITING,
            QuoteAssessmentStatus.VALIDATED_TRIGGER_REACHED,
        }
        else None
    )
    return _policy_decision(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
        status=status,
        evidence=evidence,
        derived=derived,
    )


def preflight_quote_assessment(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
) -> QuoteActionabilityAssessment | None:
    validate_control_times(scan_started_at, evaluated_at)
    if not base_is_current(base, scan_started_at=scan_started_at, evaluated_at=evaluated_at):
        return _assessment(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=QuoteAssessmentStatus.SETUP_INVALIDATED,
        )
    if not in_regular_session(evaluated_at):
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
    preflight = preflight_quote_assessment(
        base,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
    )
    if preflight is not None:
        return preflight
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
        snapshot,
        _assessment(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=status,
            snapshot=snapshot,
            derived=derived,
        ),
        derived,
    )


def _policy_decision(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
    status: QuoteAssessmentStatus,
    evidence: UsQuotePolicyEvidence | None = None,
    derived: TradeSignalPublication | None = None,
) -> UsQuotePolicyDecision:
    return UsQuotePolicyDecision(
        evidence,
        _assessment(
            base,
            scan_started_at=scan_started_at,
            evaluated_at=evaluated_at,
            status=status,
            quote_id=None if evidence is None else evidence.quote_id,
            derived=derived,
        ),
        derived,
    )


def _assessment(
    base: TradeSignalPublication,
    *,
    scan_started_at: dt.datetime,
    evaluated_at: dt.datetime,
    status: QuoteAssessmentStatus,
    snapshot: UsQuoteSnapshot | None = None,
    quote_id: str | None = None,
    derived: TradeSignalPublication | None = None,
) -> QuoteActionabilityAssessment:
    return QuoteActionabilityAssessment(
        assessment_id=assessment_identity(
            base_signal_id=base.signal.signal_id,
            scan_started_at=scan_started_at,
        ),
        base_signal_id=base.signal.signal_id,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
        status=status,
        quote_id=snapshot.quote_id if snapshot is not None else quote_id,
        derived_signal_id=None if derived is None else derived.signal.signal_id,
    )


__all__ = (
    "assess_us_quote",
    "assess_us_quote_evidence",
    "preflight_quote_assessment",
    "provider_failed_assessment",
)
