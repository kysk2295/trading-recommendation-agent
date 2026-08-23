from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from typing import override

from pydantic import ValidationError

from trading_agent.alpaca_news_models import AlpacaNewsArticle
from trading_agent.alpaca_news_opportunity_evidence import AlpacaNewsOpportunityEvidenceBundle
from trading_agent.generated_strategy_protocol import BarFrame
from trading_agent.market_context_models import MarketContextSnapshot
from trading_agent.signal_contract_models import EvidenceRef, QuoteValidation
from trading_agent.us_day_situation_projection import project_us_day_situation, project_us_strategy_day_situation
from trading_agent.us_day_source_models import CanonicalUsDaySource
from trading_agent.us_day_thesis_models import UsDayCurrentMarket
from trading_agent.us_forward_shadow_models import UsForwardShadowTick, completed_bar_id
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_quote_actionability_evidence import UsQuotePolicyEvidence
from trading_agent.us_strategy_day_input import UsStrategyDayCandidateEvidence, UsStrategyDayInput, spread_bps


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


def project_us_strategy_day_source(source: UsStrategyDayInput, evaluated_at: dt.datetime) -> CanonicalUsDaySource:
    try:
        checked = UsStrategyDayInput.model_validate(source.model_dump(mode="python"))
        situation = project_us_strategy_day_situation(checked, evaluated_at)
        markets = tuple(_strategy_current_market(item, evaluated_at) for item in checked.candidates)
        return CanonicalUsDaySource(situation=situation, current_markets=markets)
    except (KeyError, TypeError, ValidationError, ValueError):
        raise UsDaySourceProjectionError from None


def _strategy_current_market(
    evidence: UsStrategyDayCandidateEvidence,
    evaluated_at: dt.datetime,
) -> UsDayCurrentMarket:
    latest = evidence.bars[-1]
    spread = spread_bps(evidence)
    valid_until = evidence.quote.observed_at + dt.timedelta(seconds=5)
    quote = QuoteValidation(
        bid=Decimal(str(evidence.quote.bid)),
        ask=Decimal(str(evidence.quote.ask)),
        observed_at=evidence.quote.observed_at,
        valid_until=valid_until,
        spread_bps=spread,
        max_slippage_bps=max(spread, Decimal("20")),
    )
    prior = evidence.bars[:-1]
    average_minute_volume = max(1, sum(item.volume for item in prior) // len(prior))
    current_bar = BarFrame(
        symbol=evidence.symbol,
        timestamp=latest.timestamp,
        open=latest.open,
        high=latest.high,
        low=latest.low,
        close=latest.close,
        volume=latest.volume,
        prior_close=evidence.bars[0].open,
        average_daily_volume=average_minute_volume * 390,
        spread_bps=float(spread),
        catalyst="",
    )
    source_quote_ref = next(item for item in evidence.evidence_refs if item.namespace == "quote/alpaca-sip")
    quote_ref = EvidenceRef(
        namespace="quote/snapshot",
        record_id=f"us-quote:{hashlib.sha256(source_quote_ref.canonical_id.encode()).hexdigest()}",
        observed_at=source_quote_ref.observed_at,
    )
    bar_ref = EvidenceRef(
        namespace="research/current_bar",
        record_id=completed_bar_id(current_bar),
        observed_at=current_bar.timestamp,
    )
    if valid_until < evaluated_at:
        raise UsDaySourceProjectionError
    return UsDayCurrentMarket(
        symbol=evidence.symbol,
        quote=quote,
        quote_ref=quote_ref,
        current_bar_ref=bar_ref,
        current_bar=current_bar,
    )


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


__all__ = (
    "UsDaySourceProjectionError",
    "project_us_day_source",
    "project_us_strategy_day_source",
)
