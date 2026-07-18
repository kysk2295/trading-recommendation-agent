from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alpaca_security_master_models import (
    AlpacaSecurityMasterSnapshot,
    build_alpaca_security_master_snapshot,
)
from trading_agent.data_capability_models import DataSourceId
from trading_agent.data_foundation_manifest import DataFoundationManifest, load_data_foundation_manifest
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)
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
    foundation = load_data_foundation_manifest(FOUNDATION)

    snapshot = projector.project(_opportunity(), foundation)
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
    assert store.latest_foundation() == foundation
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


def test_empty_v1_store_migrates_before_first_audited_projection(tmp_path: Path) -> None:
    database = tmp_path / "scanner.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE us_opportunity_scanner_raw ("
            "generation INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL UNIQUE,"
            "opportunity_id TEXT NOT NULL UNIQUE,observed_at TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,raw_payload BLOB NOT NULL);"
            "CREATE TABLE us_opportunity_scanner_projections ("
            "generation INTEGER PRIMARY KEY AUTOINCREMENT,dataset_id TEXT NOT NULL UNIQUE,"
            "projection_key TEXT NOT NULL UNIQUE,opportunity_id TEXT NOT NULL UNIQUE,"
            "dataset_directory TEXT NOT NULL,snapshot_payload BLOB NOT NULL,"
            "recorded_at TEXT NOT NULL);"
            "PRAGMA user_version = 1;"
        )

    store = UsOpportunityScannerStore(database)
    foundation = load_data_foundation_manifest(FOUNDATION)
    snapshot = UsOpportunityScannerProjector(store, tmp_path / "canonical").project(
        _opportunity(),
        foundation,
    )

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
    assert version == (2,)
    assert store.latest_snapshot() == snapshot
    assert store.latest_foundation() == foundation


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


def test_latest_readers_reject_foundation_identity_column_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "scanner.sqlite3"
    store = UsOpportunityScannerStore(database)
    projector = UsOpportunityScannerProjector(store, tmp_path / "canonical")
    _ = projector.project(_opportunity(), load_data_foundation_manifest(FOUNDATION))
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER us_opportunity_scanner_projections_no_update")
        connection.execute(
            "UPDATE us_opportunity_scanner_projections SET foundation_manifest_id = 'tampered-foundation'"
        )
        connection.commit()

    with pytest.raises(UsOpportunityScannerProjectionError):
        _ = store.latest_snapshot()
    with pytest.raises(UsOpportunityScannerProjectionError):
        _ = store.latest_foundation()


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


def test_current_security_master_resolves_dynamic_candidate_outside_foundation_fixture(
    tmp_path: Path,
) -> None:
    store = UsOpportunityScannerStore(tmp_path / "scanner.sqlite3")
    projector = UsOpportunityScannerProjector(store, tmp_path / "canonical")

    snapshot = projector.project(
        _opportunity(symbol="DYN"),
        _operational_foundation(),
        security_master=_security_master("DYN"),
    )

    assert snapshot.candidates[0].instrument_id == "alpaca:asset-dyn"
    assert snapshot.candidates[0].symbol == "DYN"
    assert store.latest_snapshot() == snapshot


def test_future_security_master_preserves_opportunity_raw_but_blocks_projection(
    tmp_path: Path,
) -> None:
    store = UsOpportunityScannerStore(tmp_path / "scanner.sqlite3")
    projector = UsOpportunityScannerProjector(store, tmp_path / "canonical")

    with pytest.raises(UsOpportunityScannerProjectionError):
        _ = projector.project(
            _opportunity(symbol="DYN"),
            _operational_foundation(),
            security_master=_security_master(
                "DYN",
                observed_at=OBSERVED_AT + dt.timedelta(seconds=1),
            ),
        )

    assert store.raw_count() == 1
    assert store.projection_count() == 0


@pytest.mark.parametrize(
    ("security_observed_at", "foundation"),
    (
        (OBSERVED_AT - dt.timedelta(days=1, seconds=1), None),
        (OBSERVED_AT - dt.timedelta(minutes=1), "fixture"),
    ),
)
def test_stale_security_or_fixture_foundation_blocks_external_master(
    tmp_path: Path,
    security_observed_at: dt.datetime,
    foundation: str | None,
) -> None:
    projector = UsOpportunityScannerProjector(
        UsOpportunityScannerStore(tmp_path / "scanner.sqlite3"),
        tmp_path / "canonical",
    )
    selected_foundation = (
        load_data_foundation_manifest(FOUNDATION) if foundation == "fixture" else _operational_foundation()
    )

    with pytest.raises(UsOpportunityScannerProjectionError):
        _ = projector.project(
            _opportunity(symbol="DYN"),
            selected_foundation,
            security_master=_security_master(
                "DYN",
                observed_at=security_observed_at,
            ),
        )


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


def _security_master(
    symbol: str,
    *,
    observed_at: dt.datetime = OBSERVED_AT - dt.timedelta(minutes=1),
) -> AlpacaSecurityMasterSnapshot:
    instrument = InstrumentId(
        value=f"alpaca:asset-{symbol.lower()}",
        market_domain=DataMarketDomain.US_EQUITIES,
        asset_class=AssetClass.EQUITY,
        venue="XNAS",
        currency="USD",
        timezone="America/New_York",
        valid_from=observed_at,
    )
    alias = InstrumentAlias(
        instrument_id=instrument.value,
        namespace="alpaca",
        alias_type=InstrumentAliasType.PROVIDER_SYMBOL,
        value=symbol,
        effective_from=observed_at,
    )
    return build_alpaca_security_master_snapshot(
        "a" * 64,
        observed_at,
        (instrument,),
        (alias,),
    )


def _operational_foundation() -> DataFoundationManifest:
    fixture = load_data_foundation_manifest(FOUNDATION)
    source = DataSourceId(provider="alpaca", feed="sip")
    return DataFoundationManifest(
        manifest_id="us-orb-operational-test-v1",
        registered_at=fixture.registered_at,
        evaluated_at=fixture.evaluated_at,
        strategy_lane=fixture.strategy_lane,
        capabilities=tuple(item.model_copy(update={"source_id": source}) for item in fixture.capabilities),
        entitlements=tuple(item.model_copy(update={"source_id": source}) for item in fixture.entitlements),
        requirements=tuple(
            item.model_copy(
                update={
                    "primary_source_id": source,
                    "fallback_source_ids": (),
                }
            )
            for item in fixture.requirements
        ),
        instruments=fixture.instruments,
        aliases=fixture.aliases,
        corporate_actions=fixture.corporate_actions,
        events=tuple(item.model_copy(update={"source_id": source}) for item in fixture.events),
    )
