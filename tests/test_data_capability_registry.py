from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from trading_agent.data_capability_models import (
    DataCapability,
    DataCorrectionPolicy,
    DataDeliveryMode,
    DataEntitlement,
    DataHealthState,
    DataRateLimits,
    DataRetentionPolicy,
    DataSourceClass,
    DataSourceId,
    DataUse,
    RedistributionPolicy,
    TimestampSemantic,
)
from trading_agent.data_capability_registry import (
    DataCapabilityRegistryError,
    DataCapabilityRegistryStore,
)
from trading_agent.security_master_models import DataMarketDomain

UTC = dt.UTC
FIRST_AT = dt.datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
SECOND_AT = FIRST_AT + dt.timedelta(minutes=1)
SOURCE = DataSourceId(provider="fixture", feed="news")
RETENTION = DataRetentionPolicy(
    raw_retention_days=30,
    derived_retention_days=365,
    deletion_required=True,
    correction_policy=DataCorrectionPolicy.APPEND_TOMBSTONE,
)


def test_appends_contract_once_and_replays_health_as_of(tmp_path: Path) -> None:
    store = DataCapabilityRegistryStore(tmp_path / "registry.sqlite3")
    entitlement = _entitlement()

    first = store.append((_capability(FIRST_AT),), (entitlement,))
    retry = store.append((_capability(FIRST_AT),), (entitlement,))
    second = store.append((_capability(SECOND_AT, health=DataHealthState.DEGRADED),), (entitlement,))

    assert (first.capability_assessments, first.entitlements) == (1, 1)
    assert (retry.capability_assessments, retry.entitlements) == (0, 0)
    assert (second.capability_assessments, second.entitlements) == (1, 0)
    initial = store.snapshot(as_of=FIRST_AT, source_ids=(SOURCE,))
    current = store.snapshot(as_of=SECOND_AT, source_ids=(SOURCE,))
    assert initial.capabilities == (_capability(FIRST_AT),)
    assert current.capabilities == (_capability(SECOND_AT, health=DataHealthState.DEGRADED),)
    assert current.entitlements == (entitlement,)
    assert current.missing_capability_source_ids == ()
    assert current.missing_entitlement_source_ids == ()
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_snapshot_reports_missing_sources_without_inventing_fallback(tmp_path: Path) -> None:
    store = DataCapabilityRegistryStore(tmp_path / "registry.sqlite3")
    store.append((_capability(FIRST_AT),), (_entitlement(),))
    missing = DataSourceId(provider="missing", feed="news")

    snapshot = store.snapshot(as_of=FIRST_AT, source_ids=(SOURCE, missing))

    assert snapshot.capabilities == (_capability(FIRST_AT),)
    assert snapshot.entitlements == (_entitlement(),)
    assert snapshot.missing_capability_source_ids == ("missing/news",)
    assert snapshot.missing_entitlement_source_ids == ("missing/news",)


def test_rejects_same_source_time_conflict_and_overlapping_entitlement(tmp_path: Path) -> None:
    store = DataCapabilityRegistryStore(tmp_path / "registry.sqlite3")
    store.append((_capability(FIRST_AT),), (_entitlement(),))
    conflicting_capability = _capability(FIRST_AT, health=DataHealthState.DEGRADED)
    overlapping_payload = _entitlement().model_dump(mode="python")
    overlapping_payload["entitlement_id"] = "fixture-news-shadow-v2"
    overlapping = DataEntitlement.model_validate(overlapping_payload)

    with pytest.raises(DataCapabilityRegistryError):
        store.append((conflicting_capability,), (_entitlement(),))
    with pytest.raises(DataCapabilityRegistryError):
        store.append((_capability(SECOND_AT),), (overlapping,))


def test_tampered_payload_mode_and_symlink_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    store = DataCapabilityRegistryStore(path)
    store.append((_capability(FIRST_AT),), (_entitlement(),))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER capability_assessments_no_update")
        connection.execute("UPDATE capability_assessments SET payload_json = X'7B7D'")
        connection.commit()
    with pytest.raises(DataCapabilityRegistryError):
        store.snapshot(as_of=FIRST_AT, source_ids=(SOURCE,))

    entitlement_path = tmp_path / "entitlement.sqlite3"
    entitlement_store = DataCapabilityRegistryStore(entitlement_path)
    entitlement_store.append((_capability(FIRST_AT),), (_entitlement(),))
    with sqlite3.connect(entitlement_path) as connection:
        connection.execute("DROP TRIGGER entitlements_no_update")
        connection.execute("UPDATE entitlements SET effective_from_utc='2026-01-01T00:00:00Z'")
        connection.commit()
    with pytest.raises(DataCapabilityRegistryError):
        entitlement_store.snapshot(as_of=FIRST_AT, source_ids=(SOURCE,))

    private_path = tmp_path / "private.sqlite3"
    private_store = DataCapabilityRegistryStore(private_path)
    private_store.append((_capability(FIRST_AT),), (_entitlement(),))
    private_path.chmod(0o640)
    with pytest.raises(DataCapabilityRegistryError):
        private_store.snapshot(as_of=FIRST_AT, source_ids=(SOURCE,))

    target = tmp_path / "target.sqlite3"
    target_store = DataCapabilityRegistryStore(target)
    target_store.append((_capability(FIRST_AT),), (_entitlement(),))
    link = tmp_path / "link.sqlite3"
    link.symlink_to(target)
    with pytest.raises(DataCapabilityRegistryError):
        DataCapabilityRegistryStore(link).snapshot(as_of=FIRST_AT, source_ids=(SOURCE,))


def test_database_triggers_keep_registry_append_only(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    DataCapabilityRegistryStore(path).append((_capability(FIRST_AT),), (_entitlement(),))

    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            connection.execute("DELETE FROM entitlements")
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            connection.execute("UPDATE capability_assessments SET source_id='other/feed'")


def _entitlement() -> DataEntitlement:
    return DataEntitlement(
        entitlement_id="fixture-news-shadow-v1",
        source_id=SOURCE,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=("news_item",),
        permitted_uses=(DataUse.SHADOW_FORWARD,),
        real_time=True,
        historical=False,
        redistribution_policy=RedistributionPolicy.DERIVED_ONLY,
        retention=RETENTION,
        effective_from=dt.datetime(2026, 7, 17, tzinfo=UTC),
    )


def _capability(
    assessed_at: dt.datetime,
    *,
    health: DataHealthState = DataHealthState.COMPLETE,
) -> DataCapability:
    return DataCapability(
        source_id=SOURCE,
        source_class=DataSourceClass.NEWS_EVENTS,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=("news_item",),
        universe="us_equities:listed",
        delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
        expected_latency_ms=1_000,
        timestamp_semantics=(TimestampSemantic.PUBLISHED_AT, TimestampSemantic.RECEIVED_AT),
        retention=RETENTION,
        rate_limits=DataRateLimits(requests_per_minute=60),
        freshness_slo_seconds=60,
        completeness_slo_bps=9_500,
        health_state=health,
        assessed_at=assessed_at,
        latest_event_received_at=assessed_at - dt.timedelta(seconds=1),
        observed_completeness_bps=10_000 if health is DataHealthState.COMPLETE else 9_800,
    )
