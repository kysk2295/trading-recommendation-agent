from __future__ import annotations

import datetime as dt
from typing import override

from pydantic import ValidationError

from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.alpaca_news_opportunity_evidence import AlpacaNewsOpportunityEvidenceBundle
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.signal_contract_models import QuoteValidation
from trading_agent.us_day_situation_projection import project_us_day_situation
from trading_agent.us_day_source_models import CanonicalUsDaySource
from trading_agent.us_day_thesis_models import UsDayCurrentMarket
from trading_agent.us_forward_shadow_models import UsForwardShadowTick
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_quote_actionability_evidence import UsQuotePolicyEvidence


class UsDaySourceProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US day canonical source is invalid"


def project_us_day_source(
    *,
    scanner: UsOpportunityScannerBundle,
    articles: tuple[AlpacaNewsArticle, ...],
    news_evidence: AlpacaNewsOpportunityEvidenceBundle,
    market_context: MarketContextSnapshot,
    quotes: tuple[UsQuotePolicyEvidence, ...],
    completed_bars: tuple[UsForwardShadowTick, ...],
    evaluated_at: dt.datetime,
) -> CanonicalUsDaySource:
    try:
        situation = project_us_day_situation(
            scanner=scanner,
            articles=articles,
            news_evidence=news_evidence,
            market_context=market_context,
            quotes=quotes,
            completed_bars=completed_bars,
            evaluated_at=evaluated_at,
        )
        quote_by_symbol = {item.symbol: item for item in quotes}
        tick_by_symbol = {item.bars[-1].symbol: item for item in completed_bars}
        markets = tuple(
            _current_market(symbol, quote_by_symbol[symbol], tick_by_symbol[symbol])
            for symbol in sorted(quote_by_symbol)
        )
        return CanonicalUsDaySource(situation=situation, current_markets=markets)
    except (KeyError, TypeError, ValidationError, ValueError):
        raise UsDaySourceProjectionError from None


def _current_market(
    symbol: str,
    policy_quote: UsQuotePolicyEvidence,
    tick: UsForwardShadowTick,
) -> UsDayCurrentMarket:
    if (
        policy_quote.provider_observed_at < tick.quote.observed_at
        or policy_quote.provider_observed_at > tick.quote.valid_until
        or policy_quote.received_at > tick.quote.valid_until
    ):
        raise UsDaySourceProjectionError
    quote = QuoteValidation(
        bid=policy_quote.bid,
        ask=policy_quote.ask,
        observed_at=policy_quote.provider_observed_at,
        valid_until=tick.quote.valid_until,
        spread_bps=policy_quote.spread_bps,
        max_slippage_bps=tick.quote.max_slippage_bps,
    )
    return UsDayCurrentMarket(
        symbol=symbol,
        quote=quote,
        quote_ref=policy_quote.evidence_ref,
        current_bar_ref=tick.evidence_refs[0],
        current_bar=tick.bars[-1],
    )


__all__ = ("UsDaySourceProjectionError", "project_us_day_source")
