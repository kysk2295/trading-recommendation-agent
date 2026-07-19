from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, override

from trading_agent.alpaca_sip_dynamic_feature_bridge import (
    AlpacaSipDynamicFeatureConfirmation,
    confirm_intraday_feature_with_dynamic_trade,
)
from trading_agent.alpaca_sip_dynamic_quote_feature_bridge import (
    AlpacaSipDynamicQuoteFeatureConfirmation,
    confirm_intraday_feature_with_dynamic_quote,
)
from trading_agent.alpaca_sip_dynamic_quote_history import AlpacaSipDynamicQuoteHistory
from trading_agent.alpaca_sip_dynamic_trade_history import AlpacaSipDynamicTradeHistory
from trading_agent.intraday_feature_kernel import IntradayFeatureSnapshot

_SEMANTIC_VERSION: Final = "alpaca_sip_dynamic_microstructure_feature_bundle_v1"
_BASIS_POINTS: Final = Decimal(10_000)
type _BundleIdentityContent = tuple[str, str, str, str, bool]


class AlpacaSipDynamicFeatureBundleError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic feature bundle is blocked"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicFeatureBundle:
    bundle_id: str
    semantic_version: str
    trade_confirmation: AlpacaSipDynamicFeatureConfirmation
    quote_confirmation: AlpacaSipDynamicQuoteFeatureConfirmation
    last_trade_vs_midpoint_bps: Decimal
    last_trade_inside_quote: bool
    complete_trade_history: bool
    complete_quote_history: bool

    def __post_init__(self) -> None:
        trade = self.trade_confirmation
        quote = self.quote_confirmation
        expected_bps = ((trade.last_trade_price / quote.midpoint) - Decimal(1)) * _BASIS_POINTS
        expected_inside = quote.bid_price <= trade.last_trade_price <= quote.ask_price
        if (
            len(self.bundle_id) != 64
            or self.semantic_version != _SEMANTIC_VERSION
            or type(trade) is not AlpacaSipDynamicFeatureConfirmation
            or type(quote) is not AlpacaSipDynamicQuoteFeatureConfirmation
            or trade.research_input_identity_sha256 != quote.research_input_identity_sha256
            or trade.dynamic_plan_id != quote.dynamic_plan_id
            or trade.connection_epoch != quote.connection_epoch
            or trade.market_date != quote.market_date
            or trade.instrument_id != quote.instrument_id
            or trade.symbol != quote.symbol
            or trade.observed_at != quote.observed_at
            or trade.bar_source_end_at != quote.bar_source_end_at
            or trade.vwap != quote.vwap
            or type(self.last_trade_vs_midpoint_bps) is not Decimal
            or not self.last_trade_vs_midpoint_bps.is_finite()
            or self.last_trade_vs_midpoint_bps != expected_bps
            or type(self.last_trade_inside_quote) is not bool
            or self.last_trade_inside_quote is not expected_inside
            or self.complete_trade_history is not True
            or self.complete_quote_history is not True
            or trade.complete_history is not self.complete_trade_history
            or quote.complete_history is not self.complete_quote_history
            or self.bundle_id
            != _bundle_id(
                (
                    _SEMANTIC_VERSION,
                    trade.confirmation_id,
                    quote.confirmation_id,
                    str(expected_bps),
                    expected_inside,
                )
            )
        ):
            raise AlpacaSipDynamicFeatureBundleError


def build_alpaca_sip_dynamic_feature_bundle(
    snapshot: IntradayFeatureSnapshot,
    trade_history: AlpacaSipDynamicTradeHistory,
    quote_history: AlpacaSipDynamicQuoteHistory,
) -> AlpacaSipDynamicFeatureBundle:
    try:
        trade = confirm_intraday_feature_with_dynamic_trade(snapshot, trade_history)
        quote = confirm_intraday_feature_with_dynamic_quote(snapshot, quote_history)
        last_trade_vs_midpoint_bps = ((trade.last_trade_price / quote.midpoint) - Decimal(1)) * _BASIS_POINTS
        last_trade_inside_quote = quote.bid_price <= trade.last_trade_price <= quote.ask_price
        identity_content = (
            _SEMANTIC_VERSION,
            trade.confirmation_id,
            quote.confirmation_id,
            str(last_trade_vs_midpoint_bps),
            last_trade_inside_quote,
        )
        return AlpacaSipDynamicFeatureBundle(
            _bundle_id(identity_content),
            _SEMANTIC_VERSION,
            trade,
            quote,
            last_trade_vs_midpoint_bps,
            last_trade_inside_quote,
            True,
            True,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipDynamicFeatureBundleError from None


def _bundle_id(content: _BundleIdentityContent) -> str:
    payload = json.dumps(content, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = (
    "AlpacaSipDynamicFeatureBundle",
    "AlpacaSipDynamicFeatureBundleError",
    "build_alpaca_sip_dynamic_feature_bundle",
)
