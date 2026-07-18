from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.data_foundation_manifest import load_data_foundation_manifest
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.us_dynamic_subscription_policy import (
    SubscriptionPolicyConfig,
    build_subscription_policy_decision,
)
from trading_agent.us_opportunity_scanner_projection import (
    UsOpportunityScannerProjectionError,
    UsOpportunityScannerProjector,
)
from trading_agent.us_opportunity_scanner_store import UsOpportunityScannerStore
from trading_agent.us_subscription_models import SubscriptionPolicyStatus

PROJECT = Path(__file__).resolve().parents[1]
FOUNDATION = PROJECT / "examples/data/us-orb-data-foundation-v1.json"
OBSERVED_AT = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)


def test_verified_opportunity_projection_drives_bounded_subscription_policy(
    tmp_path: Path,
) -> None:
    store = UsOpportunityScannerStore(tmp_path / "scanner.sqlite3")
    projector = UsOpportunityScannerProjector(store, tmp_path / "canonical")

    snapshot = projector.project(_opportunity(), load_data_foundation_manifest(FOUNDATION))
    decision = build_subscription_policy_decision(
        snapshot,
        evaluated_at=OBSERVED_AT,
        active=(),
        cooldowns=(),
        config=_config(),
    )

    assert snapshot.identity.scope == "us_equities.broad_scanner"
    assert snapshot.observed_at == OBSERVED_AT
    assert snapshot.candidates[0].instrument_id == "us-eq-fixture-0001"
    assert snapshot.candidates[0].symbol == "FIXT"
    assert snapshot.candidates[0].priority_score == Decimal("12.5")
    assert decision.status is SubscriptionPolicyStatus.READY
    assert decision.desired[0].instrument_id == "us-eq-fixture-0001"
    assert decision.desired[0].symbol == "FIXT"
    assert store.raw_count() == 1
    assert store.projection_count() == 1
    assert store.latest_snapshot() == snapshot
    assert len(tuple((tmp_path / "canonical").rglob("events.parquet"))) == 1


def test_exact_projection_retry_reuses_raw_receipt_and_verified_dataset(
    tmp_path: Path,
) -> None:
    store = UsOpportunityScannerStore(tmp_path / "scanner.sqlite3")
    projector = UsOpportunityScannerProjector(store, tmp_path / "canonical")
    opportunity = _opportunity()
    foundation = load_data_foundation_manifest(FOUNDATION)

    first = projector.project(opportunity, foundation)
    second = projector.project(opportunity, foundation)

    assert second == first
    assert store.raw_count() == 1
    assert store.projection_count() == 1
    assert len(tuple((tmp_path / "canonical").rglob("events.parquet"))) == 1


def test_latest_snapshot_reverifies_immutable_canonical_dataset(tmp_path: Path) -> None:
    store = UsOpportunityScannerStore(tmp_path / "scanner.sqlite3")
    projector = UsOpportunityScannerProjector(store, tmp_path / "canonical")
    _ = projector.project(_opportunity(), load_data_foundation_manifest(FOUNDATION))
    parquet = next((tmp_path / "canonical").rglob("events.parquet"))
    parquet.chmod(0o640)

    with pytest.raises(
        UsOpportunityScannerProjectionError,
        match="US opportunity scanner projection is invalid",
    ):
        _ = store.latest_snapshot()


def test_missing_symbol_alias_preserves_raw_evidence_but_blocks_projection(
    tmp_path: Path,
) -> None:
    store = UsOpportunityScannerStore(tmp_path / "scanner.sqlite3")
    projector = UsOpportunityScannerProjector(store, tmp_path / "canonical")
    missing = _opportunity(symbol="MISSING")

    with pytest.raises(
        UsOpportunityScannerProjectionError,
        match="US opportunity scanner projection is invalid",
    ):
        _ = projector.project(missing, load_data_foundation_manifest(FOUNDATION))

    assert store.raw_count() == 1
    assert store.projection_count() == 0
    assert not (tmp_path / "canonical").exists()


def _opportunity(*, symbol: str = "FIXT") -> OpportunitySnapshot:
    return OpportunitySnapshot(
        opportunity_id=f"us-opportunity-{symbol.lower()}-20260717t140000z",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="fixture-v1",
        observed_at=OBSERVED_AT,
        valid_until=OBSERVED_AT + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol=symbol,
                rank=1,
                score=Decimal("12.5"),
                features=(FeatureValue(name="change_pct", value="12.5"),),
            ),
        ),
        evidence_refs=(
            EvidenceRef(
                namespace="fixture/ranking",
                record_id=f"updown:XNAS:1:{symbol}",
                observed_at=OBSERVED_AT,
            ),
        ),
        source_coverage=(
            SourceCoverage(
                source_id="fixture_ranking",
                observed_at=OBSERVED_AT,
                record_count=1,
                complete=True,
            ),
        ),
    )


def _config() -> SubscriptionPolicyConfig:
    return SubscriptionPolicyConfig(
        capacity=1,
        max_candidate_age=dt.timedelta(seconds=30),
        minimum_residency=dt.timedelta(minutes=2),
        eviction_cooldown=dt.timedelta(minutes=5),
    )
