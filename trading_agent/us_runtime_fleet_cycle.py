from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, override

from trading_agent.signal_contract_models import OpportunitySnapshot
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
from trading_agent.us_runtime_policy_scope import (
    PreparedRuntimePolicyScope,
    RuntimePolicyScopeError,
    RuntimePolicyScopeRequest,
    ScannerBundleReader,
    prepare_runtime_policy_scope,
)
from trading_agent.us_subscription_models import (
    ActiveMarketDataSubscription,
    SubscriptionCooldown,
    SubscriptionPolicyConfig,
    SubscriptionPolicyDecision,
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
        scope = prepare_runtime_policy_scope(
            scanner,
            RuntimePolicyScopeRequest(
                request.evaluated_at,
                request.active,
                request.cooldowns,
                request.policy_config,
            ),
        )
        return bind_runtime_profiles(scope, request.profiles)
    except (
        AttributeError,
        IntradayVolumeProfileArtifactError,
        KeyError,
        OSError,
        RuntimePolicyScopeError,
        TypeError,
        ValueError,
    ):
        raise RuntimeFleetCycleError from None


def bind_runtime_profiles(
    scope: PreparedRuntimePolicyScope,
    profiles: tuple[ProfileArtifactBinding, ...],
) -> PreparedRuntimeFleetCycle:
    try:
        if type(scope) is not PreparedRuntimePolicyScope:
            raise RuntimeFleetCycleError
        paths = _profile_paths(profiles, scope.decision)
        requests = tuple(
            _load_request(item.instrument_id, paths[item.instrument_id], scope.completed_minute)
            for item in scope.decision.desired
        )
        return PreparedRuntimeFleetCycle(scope.opportunity, scope.decision, requests)
    except (AttributeError, IntradayVolumeProfileArtifactError, KeyError, OSError, TypeError, ValueError):
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


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "PreparedRuntimeFleetCycle",
    "ProfileArtifactBinding",
    "RuntimeFleetCycleError",
    "RuntimeFleetCycleRequest",
    "RuntimeFleetCycleResult",
    "bind_runtime_profiles",
    "execute_runtime_fleet_cycle",
    "prepare_runtime_fleet_cycle",
)
