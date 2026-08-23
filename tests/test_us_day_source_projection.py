from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from tests.test_us_day_situation_projection import _inputs
from trading_agent.alpaca_models import AlpacaBar
from trading_agent.us_day_source_projection import (
    UsDaySourceProjectionError,
    project_us_day_source,
    project_us_strategy_day_source,
)
from trading_agent.us_strategy_day_input import UsStrategyDayInput, candidate_evidence
from trading_agent.us_strategy_research_source import UsLatestQuote


def test_projects_canonical_source_from_current_verified_inputs() -> None:
    inputs = _inputs()

    source = project_us_day_source(
        scanner=inputs.scanner,
        articles=inputs.articles,
        news_evidence=inputs.news_evidence,
        market_context=inputs.market_context,
        quotes=inputs.quotes,
        completed_bars=inputs.completed_bars,
        evaluated_at=inputs.evaluated_at,
    )

    assert source.situation.session_id == "XNYS-2026-08-20"
    assert tuple(item.symbol for item in source.current_markets) == ("AMD", "NVDA")
    amd = source.current_markets[0]
    assert amd.quote_ref == inputs.quotes[0].evidence_ref
    assert amd.quote.observed_at == inputs.quotes[0].provider_observed_at
    assert amd.current_bar_ref == inputs.completed_bars[0].evidence_refs[0]
    assert amd.current_bar == inputs.completed_bars[0].bars[-1]


def test_source_projection_is_deterministic_under_market_input_reordering() -> None:
    inputs = _inputs()

    first = project_us_day_source(**inputs.__dict__)
    reordered = replace(
        inputs,
        quotes=tuple(reversed(inputs.quotes)),
        completed_bars=tuple(reversed(inputs.completed_bars)),
    )

    assert project_us_day_source(**reordered.__dict__) == first


def test_source_projection_rejects_quote_that_outlives_tick_actionability() -> None:
    inputs = _inputs()
    quote = inputs.quotes[0].model_copy(
        update={"provider_observed_at": inputs.evaluated_at + dt.timedelta(seconds=30)}
    )

    with pytest.raises(UsDaySourceProjectionError, match="US day canonical source is invalid"):
        project_us_day_source(
            scanner=inputs.scanner,
            articles=inputs.articles,
            news_evidence=inputs.news_evidence,
            market_context=inputs.market_context,
            quotes=(quote, inputs.quotes[1]),
            completed_bars=inputs.completed_bars,
            evaluated_at=inputs.evaluated_at,
        )


def test_strategy_projection_keeps_fixed_reviewed_slippage_bound_for_wide_spread() -> None:
    inputs = _inputs()
    bars_by_symbol = {
        tick.bars[-1].symbol: tuple(
            AlpacaBar(
                t=bar.timestamp,
                o=bar.open,
                h=bar.high,
                l=bar.low,
                c=bar.close,
                v=bar.volume,
                n=1,
                vw=bar.close,
            )
            for bar in tick.bars
        )
        for tick in inputs.completed_bars
    }
    quotes_by_symbol = {
        quote.symbol: UsLatestQuote(
            symbol=quote.symbol,
            bid=float(quote.bid),
            ask=float(quote.ask),
            bid_size=quote.bid_size,
            ask_size=quote.ask_size,
            observed_at=quote.provider_observed_at,
        )
        for quote in inputs.quotes
    }
    first_symbol = inputs.scanner.opportunity.candidates[0].symbol
    first = quotes_by_symbol[first_symbol]
    quotes_by_symbol[first_symbol] = first.model_copy(update={"ask": first.bid * 1.01})
    source = UsStrategyDayInput(
        opportunity=inputs.scanner.opportunity,
        market_context=inputs.market_context,
        articles=inputs.articles,
        news_evidence=inputs.news_evidence,
        candidates=candidate_evidence(
            inputs.scanner.opportunity,
            bars_by_symbol,
            quotes_by_symbol,
            inputs.evaluated_at,
        ),
        materialized_at=inputs.evaluated_at,
    )

    projected = project_us_strategy_day_source(source, inputs.evaluated_at)

    market = next(item for item in projected.current_markets if item.symbol == first_symbol)
    assert market.quote.spread_bps > 20
    assert market.quote.max_slippage_bps == 20
