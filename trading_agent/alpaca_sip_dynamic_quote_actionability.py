from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_sip_dynamic_feature_bundle import AlpacaSipDynamicFeatureBundle
from trading_agent.signal_contract_models import EvidenceRef
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability_evidence import (
    UsQuotePolicyEvidence,
)
from trading_agent.us_quote_actionability_models import QuoteActionabilityAssessment
from trading_agent.us_quote_actionability_policy import assess_us_quote_evidence

_EVIDENCE_NAMESPACE = "quote/alpaca-sip-dynamic-bundle"


class AlpacaSipDynamicQuoteActionabilityError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic quote actionability is blocked"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicQuoteActionabilityDecision:
    bundle: AlpacaSipDynamicFeatureBundle
    policy_evidence: UsQuotePolicyEvidence | None
    assessment: QuoteActionabilityAssessment
    derived_publication: TradeSignalPublication | None


def assess_alpaca_sip_dynamic_quote(
    base: TradeSignalPublication,
    bundle: AlpacaSipDynamicFeatureBundle,
    *,
    scan_started_at: dt.datetime,
) -> AlpacaSipDynamicQuoteActionabilityDecision:
    evidence = evidence_from_alpaca_sip_dynamic_bundle(bundle)
    evaluated_at = bundle.quote_confirmation.observed_at
    decision = assess_us_quote_evidence(
        base,
        evidence,
        scan_started_at=scan_started_at,
        evaluated_at=evaluated_at,
    )
    return AlpacaSipDynamicQuoteActionabilityDecision(
        bundle,
        decision.evidence,
        decision.assessment,
        decision.derived_publication,
    )


def evidence_from_alpaca_sip_dynamic_bundle(
    bundle: AlpacaSipDynamicFeatureBundle,
) -> UsQuotePolicyEvidence:
    if type(bundle) is not AlpacaSipDynamicFeatureBundle:
        raise AlpacaSipDynamicQuoteActionabilityError
    quote = bundle.quote_confirmation
    return UsQuotePolicyEvidence(
        quote_id=f"us-quote:{bundle.bundle_id}",
        evidence_ref=EvidenceRef(
            namespace=_EVIDENCE_NAMESPACE,
            record_id=bundle.bundle_id,
            observed_at=quote.quote_event_time,
        ),
        symbol=quote.symbol,
        provider_observed_at=quote.quote_event_time,
        received_at=quote.quote_received_at,
        bid=quote.bid_price,
        ask=quote.ask_price,
        bid_size=quote.bid_size,
        ask_size=quote.ask_size,
        spread_bps=quote.spread_bps,
    )


def alpaca_sip_quote_actionability_artifacts_match(
    base: TradeSignalPublication,
    decision: AlpacaSipDynamicQuoteActionabilityDecision,
) -> bool:
    if type(decision) is not AlpacaSipDynamicQuoteActionabilityDecision:
        return False
    try:
        expected = assess_alpaca_sip_dynamic_quote(
            base,
            decision.bundle,
            scan_started_at=decision.assessment.scan_started_at,
        )
    except ValueError:
        return False
    return decision == expected


__all__ = (
    "AlpacaSipDynamicQuoteActionabilityDecision",
    "AlpacaSipDynamicQuoteActionabilityError",
    "alpaca_sip_quote_actionability_artifacts_match",
    "assess_alpaca_sip_dynamic_quote",
    "evidence_from_alpaca_sip_dynamic_bundle",
)
