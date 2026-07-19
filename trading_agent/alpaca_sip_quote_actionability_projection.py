from __future__ import annotations

import datetime as dt
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
from trading_agent.alpaca_sip_dynamic_subscription import AlpacaSipDynamicSubscriptionPlan
from trading_agent.alpaca_sip_dynamic_trade_history import materialize_alpaca_sip_dynamic_trade_history_as_of
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.intraday_feature_kernel import IntradayFeatureSnapshot
from trading_agent.trade_signal_publication import TradeSignalPublication


class AlpacaSipQuoteActionabilityProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP quote actionability projection is blocked"


@dataclass(frozen=True, slots=True)
class AlpacaSipQuoteActionabilityProjectionResult:
    decision: AlpacaSipDynamicQuoteActionabilityDecision
    appended: bool


def project_alpaca_sip_quote_actionability(
    base: TradeSignalPublication,
    snapshot: IntradayFeatureSnapshot,
    receipt_store: AlpacaSipDynamicReceiptStore,
    plan: AlpacaSipDynamicSubscriptionPlan,
    output_store: AlpacaSipQuoteActionabilityStore,
    *,
    scan_started_at: dt.datetime,
) -> AlpacaSipQuoteActionabilityProjectionResult:
    try:
        as_of = snapshot.observed_at
        trade_history = materialize_alpaca_sip_dynamic_trade_history_as_of(
            receipt_store,
            plan,
            as_of=as_of,
        )
        quote_history = materialize_alpaca_sip_dynamic_quote_history_as_of(
            receipt_store,
            plan,
            as_of=as_of,
        )
        bundle = build_alpaca_sip_dynamic_feature_bundle(
            snapshot,
            trade_history,
            quote_history,
        )
        decision = assess_alpaca_sip_dynamic_quote(
            base,
            bundle,
            scan_started_at=scan_started_at,
        )
        appended = output_store.append(base, decision)
        return AlpacaSipQuoteActionabilityProjectionResult(decision, appended)
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
    "AlpacaSipQuoteActionabilityProjectionResult",
    "project_alpaca_sip_quote_actionability",
)
