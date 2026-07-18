from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, override

from trading_agent.signal_contract_models import OpportunitySnapshot
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_feature_evidence_models import UsFeatureGateResult
from trading_agent.us_feature_evidence_projection import (
    InvalidUsFeatureEvidenceProjectionError,
    project_us_opportunity_with_feature_evidence,
)
from trading_agent.us_intraday_volume_profile_artifact import (
    IntradayVolumeProfileArtifactError,
    IntradayVolumeProfileArtifactStore,
)
from trading_agent.us_market_data_fleet import (
    RuntimeFleetResult,
    UsMarketDataFleetError,
)
from trading_agent.us_market_data_fleet_audit import (
    RuntimeFleetAuditError,
    RuntimeFleetAuditRecord,
    build_runtime_fleet_audit,
)
from trading_agent.us_market_data_runtime_models import RuntimeFeatureRequest
from trading_agent.us_opportunity_scanner_models import UsOpportunityScannerBundle
from trading_agent.us_subscription_models import (
    ActiveMarketDataSubscription,
    SubscriptionCooldown,
    SubscriptionPolicyConfig,
    SubscriptionPolicyDecision,
    SubscriptionPolicyError,
    SubscriptionPolicyStatus,
)


class RuntimeFleetCycleError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime fleet cycle input is invalid"


@dataclass(frozen=True, slots=True)
class ProfileArtifactBinding:
    instrument_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class RuntimeFleetCycleRequest:
    evaluated_at: dt.datetime
    active: tuple[ActiveMarketDataSubscription, ...]
    cooldowns: tuple[SubscriptionCooldown, ...]
    policy_config: SubscriptionPolicyConfig
    profiles: tuple[ProfileArtifactBinding, ...]


@dataclass(frozen=True, slots=True)
class PreparedRuntimeFleetCycle:
    opportunity: OpportunitySnapshot
    decision: SubscriptionPolicyDecision
    requests: tuple[RuntimeFeatureRequest, ...]


@dataclass(frozen=True, slots=True)
class RuntimeFleetCycleResult:
    fleet: RuntimeFleetResult
    gate: UsFeatureGateResult
    audit: RuntimeFleetAuditRecord
    audit_appended: bool


class ScannerBundleReader(Protocol):
    def latest_bundle(self) -> UsOpportunityScannerBundle | None: ...


class RuntimeFleetRunner(Protocol):
    def run_cycle(
        self,
        decision: SubscriptionPolicyDecision,
        requests: tuple[RuntimeFeatureRequest, ...],
    ) -> RuntimeFleetResult: ...


class RuntimeFleetAuditWriter(Protocol):
    def append(self, record: RuntimeFleetAuditRecord) -> bool: ...


def prepare_runtime_fleet_cycle(
    scanner: ScannerBundleReader,
    request: RuntimeFleetCycleRequest,
) -> PreparedRuntimeFleetCycle:
    try:
        if type(request) is not RuntimeFleetCycleRequest or not _aware(request.evaluated_at):
            raise RuntimeFleetCycleError
        bundle = scanner.latest_bundle()
        if type(bundle) is not UsOpportunityScannerBundle:
            raise RuntimeFleetCycleError
        decision = build_subscription_policy_decision(
            bundle.snapshot,
            evaluated_at=request.evaluated_at,
            active=request.active,
            cooldowns=request.cooldowns,
            config=request.policy_config,
        )
        _validate_ready_scope(bundle, decision, request.evaluated_at)
        paths = _profile_paths(request.profiles, decision)
        completed_minute = _completed_minute(request.evaluated_at)
        requests = tuple(
            _load_request(item.instrument_id, paths[item.instrument_id], completed_minute) for item in decision.desired
        )
        return PreparedRuntimeFleetCycle(bundle.opportunity, decision, requests)
    except (
        AttributeError,
        IntradayVolumeProfileArtifactError,
        KeyError,
        OSError,
        SubscriptionPolicyError,
        TypeError,
        ValueError,
    ):
        raise RuntimeFleetCycleError from None


def execute_runtime_fleet_cycle(
    prepared: PreparedRuntimeFleetCycle,
    fleet: RuntimeFleetRunner,
    audit_writer: RuntimeFleetAuditWriter,
) -> RuntimeFleetCycleResult:
    try:
        if type(prepared) is not PreparedRuntimeFleetCycle:
            raise RuntimeFleetCycleError
        result = fleet.run_cycle(prepared.decision, prepared.requests)
        gate = project_us_opportunity_with_feature_evidence(
            prepared.opportunity,
            result.bindings,
            evaluated_at=prepared.decision.evaluated_at,
        )
        audit = build_runtime_fleet_audit(
            prepared.decision,
            prepared.requests,
            result,
            gate,
        )
        appended = audit_writer.append(audit)
        return RuntimeFleetCycleResult(result, gate, audit, appended)
    except (
        InvalidUsFeatureEvidenceProjectionError,
        RuntimeFleetAuditError,
        TypeError,
        UsMarketDataFleetError,
        ValueError,
    ):
        raise RuntimeFleetCycleError from None


def _validate_ready_scope(
    bundle: UsOpportunityScannerBundle,
    decision: SubscriptionPolicyDecision,
    evaluated_at: dt.datetime,
) -> None:
    desired_symbols = tuple(item.symbol for item in decision.desired)
    opportunity_symbols = tuple(item.symbol for item in bundle.opportunity.candidates)
    snapshot_symbols = tuple(item.symbol for item in bundle.snapshot.candidates)
    if (
        decision.status is not SubscriptionPolicyStatus.READY
        or not decision.desired
        or evaluated_at >= bundle.opportunity.valid_until
        or bundle.opportunity.observed_at != bundle.snapshot.observed_at
        or desired_symbols != opportunity_symbols
        or snapshot_symbols != opportunity_symbols
    ):
        raise RuntimeFleetCycleError


def _profile_paths(
    bindings: tuple[ProfileArtifactBinding, ...],
    decision: SubscriptionPolicyDecision,
) -> dict[str, Path]:
    if type(bindings) is not tuple or any(type(item) is not ProfileArtifactBinding for item in bindings):
        raise RuntimeFleetCycleError
    paths = {item.instrument_id: item.path for item in bindings}
    desired_ids = {item.instrument_id for item in decision.desired}
    if len(paths) != len(bindings) or set(paths) != desired_ids:
        raise RuntimeFleetCycleError
    return paths


def _load_request(instrument_id: str, path: Path, completed_minute: int) -> RuntimeFeatureRequest:
    profile = IntradayVolumeProfileArtifactStore(path.parent).load(path)
    if profile.instrument_id != instrument_id or profile.through_minute != completed_minute:
        raise RuntimeFleetCycleError
    return RuntimeFeatureRequest(instrument_id, profile)


def _completed_minute(evaluated_at: dt.datetime) -> int:
    current = evaluated_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    if bounds is None:
        raise RuntimeFleetCycleError
    boundary = current.replace(second=0, microsecond=0)
    minutes = int((boundary - bounds[0]) / dt.timedelta(minutes=1))
    if minutes <= 0 or boundary >= bounds[1]:
        raise RuntimeFleetCycleError
    return minutes


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "PreparedRuntimeFleetCycle",
    "ProfileArtifactBinding",
    "RuntimeFleetCycleError",
    "RuntimeFleetCycleRequest",
    "RuntimeFleetCycleResult",
    "execute_runtime_fleet_cycle",
    "prepare_runtime_fleet_cycle",
)
