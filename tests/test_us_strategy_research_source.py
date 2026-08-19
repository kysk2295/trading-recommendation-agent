from __future__ import annotations

import datetime as dt

import pytest

from trading_agent.alpaca_models import AlpacaBar
from trading_agent.research_identity_models import MarketId
from trading_agent.us_strategy_research_source import (
    UsLatestQuote,
    UsStrategyResearchSourceError,
    build_us_strategy_research_sources,
)

NOW = dt.datetime(2026, 8, 19, 13, 42, tzinfo=dt.UTC)


def _bar(minute: int, close: float) -> AlpacaBar:
    return AlpacaBar(
        t=dt.datetime(2026, 8, 19, 13, minute, tzinfo=dt.UTC),
        o=close - 0.1,
        h=close + 0.2,
        l=close - 0.2,
        c=close,
        v=100_000,
        n=1_000,
        vw=close,
    )


def test_builds_current_us_momentum_opportunity_from_completed_sip_bars_and_quote() -> None:
    opportunity, context = build_us_strategy_research_sources(
        {
            "SPY": (_bar(37, 500.0), _bar(38, 501.0), _bar(39, 502.0), _bar(40, 503.0)),
            "QQQ": (_bar(37, 450.0), _bar(38, 451.0), _bar(39, 451.5), _bar(40, 452.0)),
        },
        {
            "SPY": UsLatestQuote(symbol="SPY", bid=502.99, ask=503.01, observed_at=NOW - dt.timedelta(seconds=5)),
            "QQQ": UsLatestQuote(symbol="QQQ", bid=451.99, ask=452.01, observed_at=NOW - dt.timedelta(seconds=4)),
        },
        NOW,
    )

    assert opportunity.strategy_lane.market_id is MarketId.US_EQUITIES
    assert opportunity.strategy_lane.strategy_id == "us_intraday_momentum"
    assert opportunity.candidates[0].symbol == "SPY"
    assert (
        dict((item.name, item.value) for item in opportunity.candidates[0].features)["completed_bar_end_at"]
        == "2026-08-19T13:41:00+00:00"
    )
    assert opportunity.observed_at == NOW - dt.timedelta(seconds=5)
    assert context.market_id is MarketId.US_EQUITIES
    assert context.observed_at == opportunity.observed_at
    assert all(item.complete for item in opportunity.source_coverage)


def test_rejects_incomplete_or_stale_us_source() -> None:
    with pytest.raises(UsStrategyResearchSourceError, match="latest_quote_stale"):
        build_us_strategy_research_sources(
            {"SPY": (_bar(39, 502.0), _bar(40, 503.0))},
            {
                "SPY": UsLatestQuote(
                    symbol="SPY",
                    bid=502.99,
                    ask=503.01,
                    observed_at=NOW - dt.timedelta(minutes=3),
                )
            },
            NOW,
        )


def test_rejects_session_closed_before_using_market_payload() -> None:
    before_open = dt.datetime(2026, 8, 19, 12, 0, tzinfo=dt.UTC)
    with pytest.raises(UsStrategyResearchSourceError, match="session_closed"):
        build_us_strategy_research_sources({}, {}, before_open)
