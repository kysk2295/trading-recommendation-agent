from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Final, override

from pydantic import ValidationError

from trading_agent.alpaca_security_master_models import AlpacaSecurityMasterSnapshot
from trading_agent.data_capability_models import (
    DataCapability,
    DataCorrectionPolicy,
    DataDeliveryMode,
    DataEntitlement,
    DataHealthState,
    DataRateLimits,
    DataRequirementFailureMode,
    DataRetentionPolicy,
    DataSourceClass,
    DataSourceId,
    DataUse,
    RedistributionPolicy,
    StrategyDataRequirement,
    TimestampSemantic,
)
from trading_agent.data_foundation_manifest import DataFoundationManifest
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.security_master_models import DataMarketDomain
from trading_agent.signal_contract_models import OpportunitySnapshot, SourceCoverage
from trading_agent.strategy_data_gate import StrategyDataStatus

_ERROR_MESSAGE: Final = "US broad scanner foundation is invalid"
_KIS_COVERAGE: Final = frozenset(
    f"kis_{source}_{exchange}" for source in ("updown", "volume") for exchange in ("ams", "nas", "nys")
)
_EXPECTED_COVERAGE: Final = _KIS_COVERAGE | {"nyse_halts"}
_ENTITLEMENT_CONTRACT_EFFECTIVE_FROM: Final = dt.datetime(2026, 7, 17, tzinfo=dt.UTC)
_RETENTION: Final = DataRetentionPolicy(
    raw_retention_days=30,
    derived_retention_days=365,
    deletion_required=True,
    correction_policy=DataCorrectionPolicy.APPEND_CORRECTION,
)


class UsBroadScannerFoundationError(ValueError):
    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "UsBroadScannerFoundationError()"


@dataclass(frozen=True, slots=True)
class _SourceContract:
    source: DataSourceId
    event_type: str
    universe: str
    latest_received_at: dt.datetime
    max_age_seconds: int
    expected_latency_ms: int
    requests_per_minute: int
    entitlement_id: str
    requirement_id: str


def build_us_broad_scanner_foundation(
    opportunity: OpportunitySnapshot,
    security_master: AlpacaSecurityMasterSnapshot,
) -> DataFoundationManifest:
    try:
        coverage = _validate_inputs(opportunity, security_master)
        contracts = _source_contracts(opportunity, security_master, coverage)
        manifest = DataFoundationManifest(
            manifest_id=_manifest_id(opportunity, security_master),
            registered_at=opportunity.observed_at,
            evaluated_at=opportunity.observed_at,
            strategy_lane=opportunity.strategy_lane,
            capabilities=tuple(_capability(item, opportunity.observed_at) for item in contracts),
            entitlements=tuple(_entitlement(item) for item in contracts),
            requirements=tuple(
                sorted(
                    (_requirement(item, opportunity) for item in contracts),
                    key=lambda item: item.requirement_id,
                )
            ),
            instruments=security_master.instruments,
            aliases=security_master.aliases,
            corporate_actions=(),
            events=(),
        )
        if manifest.evaluate_data_readiness().status is not StrategyDataStatus.READY:
            raise UsBroadScannerFoundationError
        return manifest
    except (KeyError, TypeError, ValidationError, ValueError):
        raise UsBroadScannerFoundationError from None


def _validate_inputs(
    opportunity: OpportunitySnapshot,
    security_master: AlpacaSecurityMasterSnapshot,
) -> dict[str, SourceCoverage]:
    if (
        type(opportunity) is not OpportunitySnapshot
        or type(security_master) is not AlpacaSecurityMasterSnapshot
        or opportunity.strategy_lane.market_id is not MarketId.US_EQUITIES
        or opportunity.strategy_lane.agent_family is not AgentFamily.OPPORTUNITY_MANAGER
        or opportunity.strategy_lane.strategy_id != "ranking_momentum"
        or opportunity.producer_strategy_version != "kis-risk-screen-v1"
        or security_master.observed_at > opportunity.observed_at
        or opportunity.observed_at - security_master.observed_at > dt.timedelta(days=1)
    ):
        raise UsBroadScannerFoundationError
    coverage = {item.source_id: item for item in opportunity.source_coverage}
    if frozenset(coverage) != _EXPECTED_COVERAGE or any(
        not item.complete or item.failure_reason is not None or item.observed_at > opportunity.observed_at
        for item in coverage.values()
    ):
        raise UsBroadScannerFoundationError
    return coverage


def _source_contracts(
    opportunity: OpportunitySnapshot,
    security_master: AlpacaSecurityMasterSnapshot,
    coverage: dict[str, SourceCoverage],
) -> tuple[_SourceContract, ...]:
    ranking_observed = min(coverage[source].observed_at for source in _KIS_COVERAGE)
    return (
        _SourceContract(
            DataSourceId(provider="alpaca", feed="assets"),
            "instrument_snapshot",
            "us_equities:all_active",
            security_master.observed_at,
            86_400,
            30_000,
            200,
            "alpaca-assets-paper-recommendation",
            "broad-scanner-assets-current",
        ),
        _SourceContract(
            DataSourceId(provider="kis", feed="us_ranking"),
            "ranking_snapshot",
            "us_equities:listed",
            ranking_observed,
            60,
            5_000,
            60,
            "kis-ranking-paper-recommendation",
            "broad-scanner-ranking-current",
        ),
        _SourceContract(
            DataSourceId(provider="nyse", feed="current_halts"),
            "halt_snapshot",
            "us_equities:listed",
            coverage["nyse_halts"].observed_at,
            60,
            5_000,
            60,
            "nyse-halts-paper-recommendation",
            "broad-scanner-halts-current",
        ),
    )


def _capability(contract: _SourceContract, evaluated_at: dt.datetime) -> DataCapability:
    return DataCapability(
        source_id=contract.source,
        source_class=DataSourceClass.MARKET_MICROSTRUCTURE,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=(contract.event_type,),
        universe=contract.universe,
        delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
        expected_latency_ms=contract.expected_latency_ms,
        timestamp_semantics=(TimestampSemantic.RECEIVED_AT,),
        retention=_RETENTION,
        rate_limits=DataRateLimits(requests_per_minute=contract.requests_per_minute),
        freshness_slo_seconds=contract.max_age_seconds,
        completeness_slo_bps=10_000,
        health_state=DataHealthState.COMPLETE,
        assessed_at=evaluated_at,
        latest_event_received_at=contract.latest_received_at,
        observed_completeness_bps=10_000,
    )


def _entitlement(contract: _SourceContract) -> DataEntitlement:
    return DataEntitlement(
        entitlement_id=contract.entitlement_id,
        source_id=contract.source,
        market_domains=(DataMarketDomain.US_EQUITIES,),
        event_types=(contract.event_type,),
        permitted_uses=(DataUse.PAPER_RECOMMENDATION,),
        real_time=True,
        historical=False,
        redistribution_policy=RedistributionPolicy.DERIVED_ONLY,
        retention=_RETENTION,
        effective_from=_ENTITLEMENT_CONTRACT_EFFECTIVE_FROM,
    )


def _requirement(
    contract: _SourceContract,
    opportunity: OpportunitySnapshot,
) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        requirement_id=contract.requirement_id,
        strategy_lane=opportunity.strategy_lane,
        data_use=DataUse.PAPER_RECOMMENDATION,
        market_domain=DataMarketDomain.US_EQUITIES,
        event_type=contract.event_type,
        primary_source_id=contract.source,
        required_delivery_modes=(DataDeliveryMode.REST_SNAPSHOT,),
        required_timestamp_semantics=(TimestampSemantic.RECEIVED_AT,),
        max_age_seconds=contract.max_age_seconds,
        minimum_completeness_bps=10_000,
        allow_degraded=False,
        failure_mode=DataRequirementFailureMode.BLOCKED_BY_DATA,
    )


def _manifest_id(
    opportunity: OpportunitySnapshot,
    security_master: AlpacaSecurityMasterSnapshot,
) -> str:
    payload = {
        "opportunity_id": opportunity.opportunity_id,
        "security_master_id": security_master.snapshot_id,
        "source_coverage": [item.model_dump(mode="json") for item in opportunity.source_coverage],
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"us-broad-scanner:{hashlib.sha256(encoded.encode()).hexdigest()}"


__all__ = (
    "UsBroadScannerFoundationError",
    "build_us_broad_scanner_foundation",
)
