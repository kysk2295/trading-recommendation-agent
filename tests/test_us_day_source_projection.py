from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from tests.test_us_day_situation_projection import _inputs
from trading_agent.us_day_source_projection import (
    UsDaySourceProjectionError,
    project_us_day_source,
)


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
