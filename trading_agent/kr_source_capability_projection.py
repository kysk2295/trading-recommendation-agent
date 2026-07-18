from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

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
from trading_agent.kis_kr_ranking_collection import KIS_KR_RANKING_ADAPTER_VERSION
from trading_agent.kr_source_collection_models import KrSourceCollectionRun
from trading_agent.kr_theme_models import KrCatalystSource, KrCoverageStatus
from trading_agent.kr_volume_surge import KR_VOLUME_SURGE_ADAPTER_VERSION
from trading_agent.ls_nws_collection import LS_NWS_ADAPTER_VERSION
from trading_agent.opendart_collection import OPENDART_ADAPTER_VERSION
from trading_agent.security_master_models import DataMarketDomain

_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 15, tzinfo=dt.UTC)
_CORRECTION_RETENTION = DataRetentionPolicy(
    raw_retention_days=30,
    derived_retention_days=365,
    deletion_required=True,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)
_NEWS_RETENTION = DataRetentionPolicy(
    raw_retention_days=30,
    derived_retention_days=365,
    deletion_required=True,
    correction_policy=DataCorrectionPolicy.APPEND_TOMBSTONE,
)


@dataclass(frozen=True, slots=True)
class _SourceContract:
    source: KrCatalystSource
    source_id: DataSourceId
    source_class: DataSourceClass
    event_type: str
    delivery_mode: DataDeliveryMode
    timestamp_semantics: tuple[TimestampSemantic, ...]
    freshness_seconds: int
    entitlement_id: str
    adapter_version: str
    source_run_suffix: str
    retention: DataRetentionPolicy


_CONTRACTS: Final = tuple(
    sorted(
        (
            _SourceContract(
                KrCatalystSource.DART,
                DataSourceId(provider="opendart", feed="list"),
                DataSourceClass.REGULATORY_FUNDAMENTAL,
                "disclosure",
                DataDeliveryMode.REST_SNAPSHOT,
                (TimestampSemantic.PUBLISHED_AT, TimestampSemantic.RECEIVED_AT),
                86_400,
                "opendart-list-shadow-v1",
                OPENDART_ADAPTER_VERSION,
                "dart",
                _CORRECTION_RETENTION,
            ),
            _SourceContract(
                KrCatalystSource.NEWS,
                DataSourceId(provider="ls", feed="nws"),
                DataSourceClass.NEWS_EVENTS,
                "news_headline",
                DataDeliveryMode.WEBSOCKET_STREAM,
                (TimestampSemantic.PUBLISHED_AT, TimestampSemantic.RECEIVED_AT),
                300,
                "ls-nws-shadow-v1",
                LS_NWS_ADAPTER_VERSION,
                "news",
                _NEWS_RETENTION,
            ),
            _SourceContract(
                KrCatalystSource.KIS_RANKING,
                DataSourceId(provider="kis", feed="kr_ranking"),
                DataSourceClass.MARKET_MICROSTRUCTURE,
                "ranking_snapshot",
                DataDeliveryMode.REST_SNAPSHOT,
                (TimestampSemantic.RECEIVED_AT,),
                60,
                "kis-kr-ranking-shadow-v1",
                KIS_KR_RANKING_ADAPTER_VERSION,
                "kis_ranking",
                _CORRECTION_RETENTION,
            ),
            _SourceContract(
                KrCatalystSource.VOLUME_SURGE,
                DataSourceId(provider="local", feed="kr_volume_surge"),
                DataSourceClass.MARKET_MICROSTRUCTURE,
                "volume_surge",
                DataDeliveryMode.LOCAL_DERIVED,
                (TimestampSemantic.RECEIVED_AT,),
                60,
                "local-kr-volume-surge-shadow-v1",
                KR_VOLUME_SURGE_ADAPTER_VERSION,
                "volume_surge",
                _CORRECTION_RETENTION,
            ),
        ),
        key=lambda item: item.source_id.canonical_id,
    )
)


class KrSourceCapabilityProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR source capability projection is invalid"


@dataclass(frozen=True, slots=True)
class KrSourceCapabilityProjection:
    collection_cycle_id: str
    collection_date: dt.date
    assessed_at: dt.datetime
    complete: bool
    capabilities: tuple[DataCapability, ...]
    entitlements: tuple[DataEntitlement, ...]


def project_kr_source_capabilities(
    runs: tuple[KrSourceCollectionRun, ...],
) -> KrSourceCapabilityProjection:
    try:
        checked = tuple(KrSourceCollectionRun.model_validate(item.model_dump(mode="python")) for item in runs)
        by_source = _validated_runs(checked)
        collection_date = checked[0].collection_date
        if collection_date is None:
            raise ValueError
        capabilities = tuple(_capability(contract, by_source[contract.source]) for contract in _CONTRACTS)
        entitlements = tuple(_entitlement(contract) for contract in _CONTRACTS)
        return KrSourceCapabilityProjection(
            collection_cycle_id=checked[0].collection_cycle_id,
            collection_date=collection_date,
            assessed_at=max(item.completed_at for item in checked),
            complete=all(item.status is KrCoverageStatus.SUCCESS for item in checked),
            capabilities=capabilities,
            entitlements=entitlements,
        )
    except (IndexError, KeyError, TypeError, ValidationError, ValueError):
        raise KrSourceCapabilityProjectionError from None


def _validated_runs(
    runs: tuple[KrSourceCollectionRun, ...],
) -> dict[KrCatalystSource, KrSourceCollectionRun]:
    sources = tuple(item.source for item in runs)
    cycle_ids = {item.collection_cycle_id for item in runs}
    dates = {item.collection_date for item in runs}
    contract_by_source = {item.source: item for item in _CONTRACTS}
    if (
        len(sources) != len(KrCatalystSource)
        or set(sources) != set(KrCatalystSource)
        or len(cycle_ids) != 1
        or len(dates) != 1
        or None in dates
        or any(
            item.adapter_version != contract_by_source[item.source].adapter_version
            or item.source_run_id != f"{item.collection_cycle_id}:{contract_by_source[item.source].source_run_suffix}"
            for item in runs
        )
    ):
        raise ValueError
    return {item.source: item for item in runs}


def _capability(
    contract: _SourceContract,
    run: KrSourceCollectionRun,
) -> DataCapability:
    succeeded = run.status is KrCoverageStatus.SUCCESS
    return DataCapability(
        source_id=contract.source_id,
        source_class=contract.source_class,
        market_domains=(DataMarketDomain.KR_EQUITIES,),
        event_types=(contract.event_type,),
        universe="kr_equities:listed",
        delivery_modes=(contract.delivery_mode,),
        expected_latency_ms=5_000,
        timestamp_semantics=contract.timestamp_semantics,
        retention=contract.retention,
        rate_limits=DataRateLimits(requests_per_minute=60),
        freshness_slo_seconds=contract.freshness_seconds,
        completeness_slo_bps=10_000,
        health_state=DataHealthState.COMPLETE if succeeded else DataHealthState.FAILED,
        assessed_at=run.completed_at,
        latest_event_received_at=None,
        latest_source_heartbeat_at=run.completed_at,
        observed_completeness_bps=10_000 if succeeded else 0,
    )


def _entitlement(contract: _SourceContract) -> DataEntitlement:
    return DataEntitlement(
        entitlement_id=contract.entitlement_id,
        source_id=contract.source_id,
        market_domains=(DataMarketDomain.KR_EQUITIES,),
        event_types=(contract.event_type,),
        permitted_uses=(DataUse.SHADOW_FORWARD,),
        real_time=True,
        historical=False,
        redistribution_policy=RedistributionPolicy.DERIVED_ONLY,
        retention=contract.retention,
        effective_from=_EFFECTIVE_FROM,
    )


__all__ = (
    "KrSourceCapabilityProjection",
    "KrSourceCapabilityProjectionError",
    "project_kr_source_capabilities",
)
