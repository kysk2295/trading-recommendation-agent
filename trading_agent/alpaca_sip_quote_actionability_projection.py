from __future__ import annotations

from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_sip_dynamic_feature_bundle import build_alpaca_sip_dynamic_feature_bundle
from trading_agent.alpaca_sip_dynamic_quote_actionability import (
    AlpacaSipDynamicQuoteActionabilityDecision,
    assess_alpaca_sip_dynamic_quote,
)
from trading_agent.alpaca_sip_dynamic_quote_history import materialize_alpaca_sip_dynamic_quote_history_as_of
from trading_agent.alpaca_sip_dynamic_receipt_models import AlpacaSipDynamicReceiptError
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_trade_history import materialize_alpaca_sip_dynamic_trade_history_as_of
from trading_agent.alpaca_sip_quote_actionability_manifest import AlpacaSipQuoteActionabilityManifest
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.intraday_feature_kernel import IntradayFeatureSnapshot
from trading_agent.intraday_feature_reobservation import reobserve_ready_intraday_feature


class AlpacaSipQuoteActionabilityProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP quote actionability projection is blocked"


@dataclass(frozen=True, slots=True)
class AlpacaSipQuoteActionabilityProjectionResult:
    decision: AlpacaSipDynamicQuoteActionabilityDecision
    appended: bool


@dataclass(frozen=True, slots=True)
class AlpacaSipQuoteActionabilityProjectionRequest:
    manifest: AlpacaSipQuoteActionabilityManifest
    snapshot: IntradayFeatureSnapshot
    receipt_store: AlpacaSipDynamicReceiptStore
    output_store: AlpacaSipQuoteActionabilityStore


def project_alpaca_sip_quote_actionability(
    request: AlpacaSipQuoteActionabilityProjectionRequest,
) -> AlpacaSipQuoteActionabilityProjectionResult:
    try:
        if (
            type(request) is not AlpacaSipQuoteActionabilityProjectionRequest
            or type(request.manifest) is not AlpacaSipQuoteActionabilityManifest
            or type(request.snapshot) is not IntradayFeatureSnapshot
            or type(request.receipt_store) is not AlpacaSipDynamicReceiptStore
            or type(request.output_store) is not AlpacaSipQuoteActionabilityStore
            or reobserve_ready_intraday_feature(
                request.manifest.snapshot,
                request.snapshot.observed_at,
            )
            != request.snapshot
        ):
            raise AlpacaSipQuoteActionabilityProjectionError
        as_of = request.snapshot.observed_at
        trade_history = materialize_alpaca_sip_dynamic_trade_history_as_of(
            request.receipt_store,
            request.manifest.plan,
            as_of=as_of,
        )
        quote_history = materialize_alpaca_sip_dynamic_quote_history_as_of(
            request.receipt_store,
            request.manifest.plan,
            as_of=as_of,
        )
        bundle = build_alpaca_sip_dynamic_feature_bundle(
            request.snapshot,
            trade_history,
            quote_history,
        )
        decision = assess_alpaca_sip_dynamic_quote(
            request.manifest.base_publication,
            bundle,
            scan_started_at=request.manifest.scan_started_at,
        )
        result = request.output_store.append_for_manifest(request.manifest, decision)
        return AlpacaSipQuoteActionabilityProjectionResult(decision, result.appended)
    except (
        AlpacaSipDynamicReceiptError,
        AttributeError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise AlpacaSipQuoteActionabilityProjectionError from None


__all__ = (
    "AlpacaSipQuoteActionabilityProjectionError",
    "AlpacaSipQuoteActionabilityProjectionRequest",
    "AlpacaSipQuoteActionabilityProjectionResult",
    "project_alpaca_sip_quote_actionability",
)
