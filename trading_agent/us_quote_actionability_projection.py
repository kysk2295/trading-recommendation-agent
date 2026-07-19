from __future__ import annotations

import datetime as dt

from trading_agent.kis_us_quote import KisUsLevelOneQuote
from trading_agent.signal_contract_models import (
    EvidenceRef,
    QuoteValidation,
    SignalActionability,
    TradeSignalEnvelope,
)
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_quote_actionability_evidence import (
    UsQuotePolicyEvidence,
    evidence_from_kis_snapshot,
)
from trading_agent.us_quote_actionability_identity import derived_signal_identity, quote_identity
from trading_agent.us_quote_actionability_models import (
    MAX_QUOTE_SPREAD_BPS,
    QUOTE_FRESHNESS,
    UsQuoteSnapshot,
    spread_bps,
)


def snapshot_from_kis(quote: KisUsLevelOneQuote) -> UsQuoteSnapshot:
    return UsQuoteSnapshot(
        quote_id=quote_identity(
            exchange=quote.exchange,
            symbol=quote.symbol,
            provider_observed_at=quote.provider_observed_at,
            received_at=quote.received_at,
            bid=quote.bid,
            ask=quote.ask,
            bid_size=quote.bid_size,
            ask_size=quote.ask_size,
        ),
        exchange=quote.exchange,
        symbol=quote.symbol,
        provider_observed_at=quote.provider_observed_at,
        received_at=quote.received_at,
        bid=quote.bid,
        ask=quote.ask,
        bid_size=quote.bid_size,
        ask_size=quote.ask_size,
        spread_bps=spread_bps(quote.bid, quote.ask),
    )


def quote_evidence_refs(
    base: TradeSignalPublication,
    snapshot: UsQuoteSnapshot,
) -> tuple[EvidenceRef, ...]:
    return policy_evidence_refs(base, evidence_from_kis_snapshot(snapshot))


def policy_evidence_refs(
    base: TradeSignalPublication,
    evidence: UsQuotePolicyEvidence,
) -> tuple[EvidenceRef, ...]:
    signal = base.signal
    evidence_by_id = {
        item.canonical_id: item
        for item in (
            *signal.evidence_refs,
            EvidenceRef(
                namespace="signal/conditional",
                record_id=signal.signal_id,
                observed_at=signal.observed_at,
            ),
            evidence.evidence_ref,
        )
    }
    return tuple(evidence_by_id[key] for key in sorted(evidence_by_id))


def derived_publication(
    base: TradeSignalPublication,
    snapshot: UsQuoteSnapshot,
    *,
    evaluated_at: dt.datetime,
) -> TradeSignalPublication:
    return derived_publication_from_evidence(
        base,
        evidence_from_kis_snapshot(snapshot),
        evaluated_at=evaluated_at,
    )


def derived_publication_from_evidence(
    base: TradeSignalPublication,
    evidence: UsQuotePolicyEvidence,
    *,
    evaluated_at: dt.datetime,
) -> TradeSignalPublication:
    signal = base.signal
    quote_valid_until = evidence.provider_observed_at + QUOTE_FRESHNESS
    derived_signal = TradeSignalEnvelope(
        signal_id=derived_signal_identity(signal.signal_id, evidence.quote_id),
        strategy_lane=signal.strategy_lane,
        producer_strategy_version=signal.producer_strategy_version,
        symbol=signal.symbol,
        observed_at=evaluated_at,
        valid_until=min(signal.valid_until, quote_valid_until),
        side=signal.side,
        entry_type=signal.entry_type,
        entry_price=signal.entry_price,
        stop_price=signal.stop_price,
        targets=signal.targets,
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule=signal.invalidation_rule,
        rationale=signal.rationale,
        evidence_refs=policy_evidence_refs(base, evidence),
        quote_validation=QuoteValidation(
            bid=evidence.bid,
            ask=evidence.ask,
            observed_at=evidence.provider_observed_at,
            valid_until=quote_valid_until,
            spread_bps=evidence.spread_bps,
            max_slippage_bps=MAX_QUOTE_SPREAD_BPS,
        ),
        opportunity_id=signal.opportunity_id,
    )
    return TradeSignalPublication(published_at=evaluated_at, signal=derived_signal)


__all__ = (
    "derived_publication",
    "derived_publication_from_evidence",
    "policy_evidence_refs",
    "quote_evidence_refs",
    "snapshot_from_kis",
)
