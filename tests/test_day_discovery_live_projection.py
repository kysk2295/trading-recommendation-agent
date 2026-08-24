from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kr_day_capsule_adapter import _request as kr_request
from tests.test_us_day_situation_projection import EVALUATED_AT as US_EVALUATED
from tests.test_us_day_situation_projection import _inputs as us_inputs
from trading_agent.alpaca_models import AlpacaBar
from trading_agent.day_discovery_live_projection import (
    project_kr_live_discovery_evidence,
    project_us_live_discovery_evidence,
    publish_live_discovery_evidence_once,
)
from trading_agent.day_discovery_loop import DayDiscoveryTriggerKind
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_identity_models import MarketId
from trading_agent.us_strategy_day_input import UsStrategyDayInput, candidate_evidence
from trading_agent.us_strategy_research_source import UsLatestQuote


def test_us_live_input_projects_latest_completed_bar_to_future_only_discovery() -> None:
    source = _us_day_input()
    published_at = US_EVALUATED + dt.timedelta(seconds=4)

    view = project_us_live_discovery_evidence(source, published_at=published_at)

    assert view.market_id is MarketId.US_EQUITIES
    assert view.trigger_kind is DayDiscoveryTriggerKind.COMPLETED_BAR
    assert view.completed_bar_at == source.candidates[0].bars[-1].timestamp + dt.timedelta(minutes=1)
    assert view.first_eligible_completed_bar_at >= published_at + dt.timedelta(minutes=10)
    assert view.replay_bars[0].timestamp == source.candidates[0].bars[-1].timestamp
    assert view.replay_bars[0].spread_bps > 0
    assert view.search_budget == 16
    assert view.universe_snapshot_at <= view.completed_bar_at < view.first_eligible_completed_bar_at


def test_kr_live_input_uses_read_only_market_lineage_and_next_future_bar() -> None:
    request = kr_request()

    view = project_kr_live_discovery_evidence(
        request.opportunity,
        request.market,
        request.bars,
        request.calendar,
        published_at=request.evaluated_at,
    )

    assert view.market_id is MarketId.KR_EQUITIES
    assert view.trigger_kind is DayDiscoveryTriggerKind.COMPLETED_BAR
    assert view.completed_bar_at == request.bars[-1].end_at
    assert view.first_eligible_completed_bar_at >= request.evaluated_at + dt.timedelta(minutes=10)
    assert view.replay_bars[-1].prior_close == float(request.market.previous_close)
    assert view.replay_bars[-1].average_daily_volume > 0
    assert view.search_budget == 16
    assert "kr_read_only_market_v1" in view.evidence_schema


def test_live_discovery_publication_is_one_immutable_snapshot_per_market_session(
    tmp_path: Path,
) -> None:
    first = project_us_live_discovery_evidence(
        _us_day_input(),
        published_at=US_EVALUATED + dt.timedelta(seconds=4),
    )
    path, created = publish_live_discovery_evidence_once(tmp_path, first)
    changed = first.model_copy(update={"observed_at": first.observed_at + dt.timedelta(seconds=1)})
    replay_path, replay_created = publish_live_discovery_evidence_once(tmp_path, changed)

    assert created is True
    assert replay_created is False
    assert replay_path == path
    assert path.name == "day-discovery-evidence.us_equities.v1.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text(encoding="utf-8") == canonical_model_json(first)


def _us_day_input() -> UsStrategyDayInput:
    inputs = us_inputs()
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
    return UsStrategyDayInput(
        opportunity=inputs.scanner.opportunity,
        market_context=inputs.market_context,
        articles=inputs.articles,
        news_evidence=inputs.news_evidence,
        candidates=candidate_evidence(
            inputs.scanner.opportunity,
            bars_by_symbol,
            quotes_by_symbol,
            US_EVALUATED,
        ),
        materialized_at=US_EVALUATED,
    )
