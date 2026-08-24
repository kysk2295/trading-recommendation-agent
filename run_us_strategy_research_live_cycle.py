#!/usr/bin/env -S uv run --offline --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx2[http2,brotli,zstd]", "pydantic>=2.11"]
# ///

from __future__ import annotations

import argparse
import datetime as dt
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from trading_agent.alpaca_http import (
    AlpacaApiError,
    AlpacaSecretFileError,
    MissingAlpacaCredentialsError,
    create_alpaca_client,
    create_alpaca_news_http_client,
    load_alpaca_credentials,
)
from trading_agent.alpaca_news_client import AlpacaNewsClient, AlpacaNewsTransportError
from trading_agent.alpaca_news_collection import collect_alpaca_news
from trading_agent.alpaca_news_coverage import assess_alpaca_news_coverage
from trading_agent.alpaca_news_coverage_models import AlpacaNewsCoverageManifest
from trading_agent.alpaca_news_models import AlpacaNewsContractError, AlpacaNewsRequest, AlpacaNewsRunStatus
from trading_agent.alpaca_news_opportunity_evidence import (
    AlpacaNewsOpportunityEvidenceError,
    project_alpaca_news_opportunity_evidence,
)
from trading_agent.alpaca_news_store import AlpacaNewsStore, AlpacaNewsStoreError
from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.day_discovery_live_projection import (
    DayDiscoveryLiveProjectionError,
    project_us_live_discovery_evidence,
    publish_live_discovery_evidence_once,
)
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.strategy_research_forward_observations import (
    persist_forward_observations,
    project_matured_intraday_observations,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_strategy_day_input import UsStrategyDayInput, candidate_evidence
from trading_agent.us_strategy_research_http import AlpacaUsStrategyResearchClient
from trading_agent.us_strategy_research_source import (
    UsStrategyResearchSourceError,
    build_us_strategy_research_sources,
)

SYMBOLS = ("DIA", "IWM", "QQQ", "SPY")
Clock = Callable[[], dt.datetime]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="US SIP read-only evidence to six-agent research source cycle")
    parser.add_argument("--credentials-path", type=Path, required=True)
    parser.add_argument("--live-session-root", type=Path, required=True)
    parser.add_argument("--market-context-root", type=Path, required=True)
    parser.add_argument("--day-source-root", type=Path, required=True)
    parser.add_argument("--news-database", type=Path, required=True)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Clock = lambda: dt.datetime.now(dt.UTC),
) -> int:
    args = parse_args(argv)
    now = clock()
    local = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(local.date())
    if bounds is None or not bounds[0] < now < bounds[1]:
        print(f"status=session_closed session={local.date().isoformat()} mutation=0")
        return 0
    try:
        credentials = load_alpaca_credentials(args.credentials_path)
        with create_alpaca_client() as http:
            bars, quotes = AlpacaUsStrategyResearchClient(http, credentials).fetch(SYMBOLS, now)
        opportunity, context = build_us_strategy_research_sources(bars, quotes, now)
        news_request = AlpacaNewsRequest(
            collection_id=f"us-day-{local.strftime('%Y%m%d-%H%M%S')}",
            symbols=tuple(item.symbol for item in opportunity.candidates),
            start_at=bounds[0],
            end_at=now,
            limit=50,
            max_pages=2,
        )
        news_store = AlpacaNewsStore(args.news_database.expanduser().absolute())
        with create_alpaca_news_http_client() as news_http:
            news_result = collect_alpaca_news(
                AlpacaNewsClient(news_http, credentials, _clock=lambda: now),
                news_store,
                news_request,
                _clock=lambda: now,
            )
        if news_result.run.status is not AlpacaNewsRunStatus.SUCCESS:
            raise UsStrategyResearchSourceError("news_collection_incomplete")
        news_manifest = AlpacaNewsCoverageManifest(
            universe_id="us-strategy-day-v1",
            cutoff_at=now,
            requests=(news_request,),
        )
        news_assessment = assess_alpaca_news_coverage(news_manifest, news_store)
        news_evidence = project_alpaca_news_opportunity_evidence(
            news_manifest,
            news_assessment,
            news_store,
        )
        symbols = {item.symbol for item in opportunity.candidates}
        articles = tuple(item for item in news_result.articles if set(item.symbols).intersection(symbols))
        day_input = UsStrategyDayInput(
            opportunity=opportunity,
            market_context=context,
            articles=articles,
            news_evidence=news_evidence,
            candidates=candidate_evidence(opportunity, bars, quotes, now),
            materialized_at=now,
        )
        session = args.live_session_root.expanduser().absolute() / local.strftime("%Y%m%d")
        session.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(session, 0o700)
        outbox = session / "opportunities.v1.jsonl"
        _prepare_outbox(outbox)
        _ = append_opportunity_snapshot(outbox, opportunity)
        _, discovery_created = publish_live_discovery_evidence_once(
            args.live_session_root,
            project_us_live_discovery_evidence(day_input, published_at=now),
        )
        forward_inserted = persist_forward_observations(
            session / "strategy-research-forward-observations.json",
            project_matured_intraday_observations(_opportunities(outbox), now),
        )
        context_root = args.market_context_root.expanduser().absolute()
        context_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(context_root, 0o700)
        _ = publish_private_immutable_text(
            context_root
            / f"{context.observed_at.strftime('%Y%m%dT%H%M%S%fZ')}-{context.context_id}.market-context.json",
            context.model_dump_json() + "\n",
        )
        day_root = args.day_source_root.expanduser().absolute() / local.strftime("%Y%m%d")
        day_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(day_root, 0o700)
        _ = publish_private_immutable_text(
            day_root / f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{day_input.input_id}.day-input.json",
            day_input.model_dump_json() + "\n",
        )
    except (
        AlpacaApiError,
        AlpacaSecretFileError,
        AlpacaNewsContractError,
        AlpacaNewsOpportunityEvidenceError,
        AlpacaNewsStoreError,
        AlpacaNewsTransportError,
        DayDiscoveryLiveProjectionError,
        MissingAlpacaCredentialsError,
        OSError,
        TypeError,
        UsStrategyResearchSourceError,
        ValidationError,
        ValueError,
    ) as error:
        print(f"status=blocked_source reason={type(error).__name__} mutation=0")
        return 2
    print(
        f"status=ready opportunity={opportunity.opportunity_id} symbol={opportunity.candidates[0].symbol} "
        f"discovery_created={int(discovery_created)} forward_observations={forward_inserted} mutation=0"
    )
    return 0


def _prepare_outbox(path: Path) -> None:
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    os.chmod(path, 0o600)


def _opportunities(path: Path) -> tuple[OpportunitySnapshot, ...]:
    return tuple(
        OpportunitySnapshot.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line
    )


if __name__ == "__main__":
    raise SystemExit(main())
