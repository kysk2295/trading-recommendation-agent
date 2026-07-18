from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_owner import (
    AlpacaSipRuntimeOwnerFactory,
    AlpacaSipRuntimeOwnerFactoryConfig,
)
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.us_dynamic_subscription_policy import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
    build_subscription_policy_decision,
)
from trading_agent.us_market_data_fleet import UsMarketDataFleet
from trading_agent.us_market_data_runtime_models import RuntimeFeatureRequest
from trading_agent.us_subscription_models import SubscriptionPolicyDecision

NEW_YORK = ZoneInfo("America/New_York")
SESSION_DATE = dt.date(2026, 7, 17)
NOW = dt.datetime(2026, 7, 17, 10, 5, 30, tzinfo=NEW_YORK)
SYMBOLS = ("AAA", "BBB")


def fleet(
    root: Path,
    responder: Callable[[httpx2.Request], httpx2.Response],
    *,
    now: dt.datetime = NOW,
) -> UsMarketDataFleet:
    client = httpx2.Client(
        base_url=ALPACA_DATA_URL,
        transport=httpx2.MockTransport(responder),
        follow_redirects=False,
    )
    page_client = AlpacaSipMinutePageClient(
        client,
        AlpacaCredentials("fixture-key", "fixture-secret"),
        clock=lambda: now,
    )
    factory = AlpacaSipRuntimeOwnerFactory(
        page_client,
        AlpacaSipRuntimeOwnerFactoryConfig(
            runtime_root=root / "owners",
            canonical_root=root / "canonical",
            session_date=SESSION_DATE,
            clock=lambda: now,
        ),
    )
    return UsMarketDataFleet(factory)


def decision(now: dt.datetime = NOW) -> SubscriptionPolicyDecision:
    replay = CanonicalDatasetReplay(
        dataset_id="ds_fleet_scanner",
        event_count=2,
        canonical_event_content_sha256="a" * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id="raw_fleet_scanner",
        raw_manifest_content_sha256="b" * 64,
    )
    identity = ResearchInputIdentity.from_verified_replay(
        "us_equities.broad_scanner",
        replay,
    )
    return build_subscription_policy_decision(
        BroadScannerSnapshot(
            identity,
            now - dt.timedelta(seconds=1),
            tuple(
                BroadScannerCandidate(
                    instrument_id=f"alpaca:asset-{symbol.lower()}",
                    symbol=symbol,
                    priority_score=Decimal(str(3 - rank)),
                    source_rank=rank,
                )
                for rank, symbol in enumerate(SYMBOLS, start=1)
            ),
        ),
        evaluated_at=now,
        active=(),
        cooldowns=(),
        config=SubscriptionPolicyConfig(
            capacity=2,
            max_candidate_age=dt.timedelta(seconds=30),
            minimum_residency=dt.timedelta(minutes=2),
            eviction_cooldown=dt.timedelta(minutes=5),
        ),
    )


def feature_requests() -> tuple[RuntimeFeatureRequest, ...]:
    return tuple(
        RuntimeFeatureRequest(
            f"alpaca:asset-{symbol.lower()}",
            volume_profile(f"alpaca:asset-{symbol.lower()}", SESSION_DATE),
        )
        for symbol in SYMBOLS
    )


def opportunity() -> OpportunitySnapshot:
    observed_at = NOW - dt.timedelta(seconds=1)
    return OpportunitySnapshot(
        opportunity_id="us-opportunity-runtime-fleet",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="kis-risk-screen-v1",
        observed_at=observed_at,
        valid_until=NOW + dt.timedelta(minutes=1),
        candidates=tuple(
            OpportunityCandidate(
                symbol=symbol,
                rank=rank,
                score=Decimal(str(3 - rank)),
                features=(FeatureValue(name="change_pct", value=str(3 - rank)),),
            )
            for rank, symbol in enumerate(SYMBOLS, start=1)
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="fixture/ranking",
                record_id="fleet:1",
                observed_at=observed_at,
            ),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="fixture_ranking",
                observed_at=observed_at,
                record_count=2,
                complete=True,
            ),
        ),
    )


def wire_bars(
    symbol: str,
    count: int,
) -> tuple[dict[str, float | int | str | None], ...]:
    offset = 0.0 if symbol == "AAA" else 20.0
    return tuple(_wire_bar(index, offset) for index in range(count))


def _wire_bar(index: int, offset: float) -> dict[str, float | int | str | None]:
    timestamp = dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC) + dt.timedelta(minutes=index)
    close = 100.0 + offset + index / 10
    return {
        "t": timestamp.isoformat().replace("+00:00", "Z"),
        "o": close,
        "h": close + 0.5,
        "l": close - 0.5,
        "c": close,
        "v": 100 + index,
        "n": 10 + index,
        "vw": close,
    }
