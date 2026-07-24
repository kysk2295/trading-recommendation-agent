from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.alfred_revision_panel import build_alfred_revision_panel
from trading_agent.alfred_revision_panel_models import AlfredRevisionPanel
from trading_agent.alfred_revision_release_gate import (
    AlfredRevisionReleaseAssessment,
    build_alfred_revision_release_assessment,
)
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
from trading_agent.fred_alfred_models import FredAlfredRequest, FredSourceMode
from trading_agent.fred_alfred_snapshot_models import (
    FredAlfredSnapshot,
    FredObservation,
)
from trading_agent.fred_vintage_dates_models import FredVintageDatesSnapshot
from trading_agent.macro_revision_data_foundation import (
    MacroRevisionDataFoundationError,
    build_macro_revision_data_foundation,
)
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.strategy_data_gate import StrategyDataStatus

NOW = dt.datetime(2026, 7, 24, 6, 0, tzinfo=dt.UTC)
RETENTION = DataRetentionPolicy(
    raw_retention_days=3_650,
    derived_retention_days=3_650,
    deletion_required=False,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)


def test_foundation_binds_exact_artifacts_and_three_ready_requirements() -> None:
    panel, calendar, assessment = _evidence()

    foundation = build_macro_revision_data_foundation(
        panel=panel,
        calendar=calendar,
        assessment=assessment,
        panel_file_sha256="1" * 64,
        calendar_file_sha256="2" * 64,
        assessment_file_sha256="3" * 64,
        capabilities=_capabilities(),
        entitlements=_entitlements(),
        evaluated_at=NOW,
    )

    assert foundation.data_manifest.evaluate_data_readiness().status is StrategyDataStatus.READY
    assert len(foundation.data_manifest.requirements) == 3
    assert foundation.release_assessment.assessment_id == assessment.assessment_id
    assert len(foundation.foundation_id) == 64

    with pytest.raises(MacroRevisionDataFoundationError):
        _ = build_macro_revision_data_foundation(
            panel=panel,
            calendar=calendar,
            assessment=assessment,
            panel_file_sha256="wrong",
            calendar_file_sha256="2" * 64,
            assessment_file_sha256="3" * 64,
            capabilities=_capabilities(),
            entitlements=_entitlements(),
            evaluated_at=NOW,
        )


def _evidence() -> tuple[
    AlfredRevisionPanel,
    FredVintageDatesSnapshot,
    AlfredRevisionReleaseAssessment,
]:
    panel = build_alfred_revision_panel(
        (
            _snapshot(dt.date(2026, 7, 22), Decimal("4.1")),
            _snapshot(dt.date(2026, 7, 23), Decimal("4.1")),
        )
    )
    calendar = FredVintageDatesSnapshot(
        request_id="a" * 64,
        raw_receipt_id="b" * 64,
        observed_at=NOW,
        series_id="DFF",
        realtime_start=dt.date(2026, 7, 1),
        realtime_end=dt.date(2026, 7, 24),
        vintage_dates=(dt.date(2026, 7, 22), dt.date(2026, 7, 23)),
    )
    return panel, calendar, build_alfred_revision_release_assessment(panel, calendar)


def _snapshot(vintage: dt.date, value: Decimal) -> FredAlfredSnapshot:
    request = FredAlfredRequest(
        collection_id=f"alfred-dff-{vintage:%Y%m%d}",
        source_mode=FredSourceMode.ALFRED,
        series_id="DFF",
        observation_start=dt.date(2026, 7, 1),
        observation_end=dt.date(2026, 7, 22),
        vintage_date=vintage,
        limit=100,
    )
    return FredAlfredSnapshot(
        request_id=request.request_id,
        raw_receipt_id=("c" if vintage.day == 22 else "d") * 64,
        observed_at=NOW,
        source_mode=FredSourceMode.ALFRED,
        series_id="DFF",
        observation_start=request.observation_start,
        observation_end=request.observation_end,
        vintage_date=vintage,
        units="lin",
        observations=(
            FredObservation(
                realtime_start=vintage,
                realtime_end=vintage,
                observation_date=dt.date(2026, 7, 1),
                value=value,
            ),
        ),
    )


def _capabilities() -> tuple[DataCapability, ...]:
    return tuple(
        _capability(source, event_type)
        for source, event_type in (
            (DataSourceId(provider="fred", feed="series_vintage_dates"), "macro_release_date"),
            (DataSourceId(provider="fred", feed="series_observations"), "macro_observation"),
            (DataSourceId(provider="alfred", feed="vintage_observations"), "macro_observation"),
        )
    )


def _capability(source: DataSourceId, event_type: str) -> DataCapability:
    return DataCapability(
        source_id=source,
        source_class=DataSourceClass.MACRO_FLOW,
        market_domains=(DataMarketDomain.GLOBAL_MACRO,),
        event_types=(event_type,),
        universe="global_macro:fred_series",
        delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
        historical_from=dt.date(2026, 7, 1),
        expected_latency_ms=86_400_000,
        timestamp_semantics=(
            TimestampSemantic.EVENT_TIME,
            TimestampSemantic.RECEIVED_AT,
        ),
        retention=RETENTION,
        rate_limits=DataRateLimits(requests_per_minute=30),
        freshness_slo_seconds=86_400,
        completeness_slo_bps=10_000,
        health_state=DataHealthState.COMPLETE,
        assessed_at=NOW,
        latest_source_heartbeat_at=NOW,
        observed_completeness_bps=10_000,
    )


def _entitlements() -> tuple[DataEntitlement, ...]:
    return tuple(
        DataEntitlement(
            entitlement_id=f"{source.provider}-{source.feed}-research",
            source_id=source,
            market_domains=(DataMarketDomain.GLOBAL_MACRO,),
            event_types=(event_type,),
            permitted_uses=(
                DataUse.HISTORICAL_RESEARCH,
                DataUse.SHADOW_FORWARD,
            ),
            real_time=False,
            historical=True,
            redistribution_policy=RedistributionPolicy.ATTRIBUTED_SUMMARY,
            retention=RETENTION,
            effective_from=dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        )
        for source, event_type in (
            (DataSourceId(provider="fred", feed="series_vintage_dates"), "macro_release_date"),
            (DataSourceId(provider="fred", feed="series_observations"), "macro_observation"),
            (DataSourceId(provider="alfred", feed="vintage_observations"), "macro_observation"),
        )
    )
