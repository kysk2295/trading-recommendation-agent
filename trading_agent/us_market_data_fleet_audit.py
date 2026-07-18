from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, replace
from typing import TypedDict, assert_never, override

from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_feature_evidence_models import (
    UsFeatureGateBlocked,
    UsFeatureGateReady,
    UsFeatureGateResult,
)
from trading_agent.us_market_data_fleet import RuntimeFleetResult, RuntimeOwnerOutcome
from trading_agent.us_market_data_runtime_models import RuntimeFeatureRequest
from trading_agent.us_subscription_models import SubscriptionPolicyDecision


class RuntimeFleetAuditError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime fleet audit input is invalid"


class _OwnerPayload(TypedDict):
    connection_epoch: str | None
    feature_identity_sha256: str | None
    instrument_id: str
    last_sequence: int | None
    owner_status: str
    profile_evidence_sha256: str
    runtime_status: str | None
    symbol: str


class _AuditPayload(TypedDict):
    cycle_id: str
    evaluated_at: str
    fleet_status: str
    gate_reason: str | None
    gate_status: str
    opportunity_id: str | None
    owners: list[_OwnerPayload]
    policy_identity_sha256: str
    policy_status: str


@dataclass(frozen=True, slots=True)
class RuntimeOwnerAudit:
    instrument_id: str
    symbol: str
    owner_status: str
    runtime_status: str | None
    connection_epoch: str | None
    last_sequence: int | None
    profile_evidence_sha256: str
    feature_identity_sha256: str | None


@dataclass(frozen=True, slots=True)
class RuntimeFleetAuditRecord:
    cycle_id: str
    evaluated_at: dt.datetime
    policy_identity_sha256: str
    policy_status: str
    fleet_status: str
    owners: tuple[RuntimeOwnerAudit, ...]
    gate_status: str
    gate_reason: str | None
    opportunity_id: str | None


def build_runtime_fleet_audit(
    decision: SubscriptionPolicyDecision,
    requests: tuple[RuntimeFeatureRequest, ...],
    result: RuntimeFleetResult,
    gate: UsFeatureGateResult,
) -> RuntimeFleetAuditRecord:
    try:
        if (
            type(decision) is not SubscriptionPolicyDecision
            or type(decision.identity) is not ResearchInputIdentity
            or type(requests) is not tuple
            or type(result) is not RuntimeFleetResult
            or result.identity != decision.identity
            or result.evaluated_at != decision.evaluated_at
        ):
            raise RuntimeFleetAuditError
        request_by_id = {item.instrument_id: item for item in requests}
        if len(request_by_id) != len(requests):
            raise RuntimeFleetAuditError
        owners = tuple(_owner(item, request_by_id[item.subscription.instrument_id]) for item in result.outcomes)
        if tuple(item.instrument_id for item in owners) != tuple(item.instrument_id for item in decision.desired):
            raise RuntimeFleetAuditError
        gate_status, gate_reason, opportunity_id = _gate(gate)
        provisional = RuntimeFleetAuditRecord(
            "0" * 64,
            decision.evaluated_at,
            decision.identity.identity_sha256,
            decision.status.value,
            result.status.value,
            owners,
            gate_status,
            gate_reason,
            opportunity_id,
        )
        return replace(provisional, cycle_id=_record_sha256(provisional))
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RuntimeFleetAuditError from None


def validate_runtime_fleet_audit(record: RuntimeFleetAuditRecord) -> None:
    if (
        type(record) is not RuntimeFleetAuditRecord
        or not _aware(record.evaluated_at)
        or not record.owners
        or len({item.instrument_id for item in record.owners}) != len(record.owners)
        or any(type(item) is not RuntimeOwnerAudit for item in record.owners)
        or record.cycle_id != _record_sha256(record)
    ):
        raise RuntimeFleetAuditError


def _owner(outcome: RuntimeOwnerOutcome, request: RuntimeFeatureRequest) -> RuntimeOwnerAudit:
    runtime = outcome.runtime_result
    snapshot = None if runtime is None or not runtime.feature_snapshots else runtime.feature_snapshots[0]
    return RuntimeOwnerAudit(
        outcome.subscription.instrument_id,
        outcome.subscription.symbol,
        outcome.status.value,
        None if runtime is None else runtime.status.value,
        None if runtime is None else runtime.connection_epoch,
        None if runtime is None else runtime.last_sequence,
        request.volume_profile.evidence_sha256,
        None if snapshot is None else snapshot.identity.identity_sha256,
    )


def _gate(gate: UsFeatureGateResult) -> tuple[str, str | None, str | None]:
    match gate:
        case UsFeatureGateReady(opportunity=opportunity):
            return "ready", None, opportunity.opportunity_id
        case UsFeatureGateBlocked(reason=reason):
            return "blocked", reason.value, None
        case unreachable:
            assert_never(unreachable)


def record_bytes(record: RuntimeFleetAuditRecord) -> bytes:
    validate_runtime_fleet_audit(record)
    return _canonical_bytes(_payload(record)) + b"\n"


def record_from_bytes(value: bytes) -> RuntimeFleetAuditRecord:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != _RECORD_KEYS:
            raise RuntimeFleetAuditError
        owners = tuple(RuntimeOwnerAudit(**item) for item in payload["owners"])
        record = RuntimeFleetAuditRecord(
            payload["cycle_id"],
            dt.datetime.fromisoformat(payload["evaluated_at"]),
            payload["policy_identity_sha256"],
            payload["policy_status"],
            payload["fleet_status"],
            owners,
            payload["gate_status"],
            payload["gate_reason"],
            payload["opportunity_id"],
        )
        validate_runtime_fleet_audit(record)
        if record_bytes(record) != value:
            raise RuntimeFleetAuditError
        return record
    except (AttributeError, TypeError, ValueError):
        raise RuntimeFleetAuditError from None


def _record_sha256(record: RuntimeFleetAuditRecord) -> str:
    payload = _payload(record)
    payload["cycle_id"] = ""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _payload(record: RuntimeFleetAuditRecord) -> _AuditPayload:
    return {
        "cycle_id": record.cycle_id,
        "evaluated_at": record.evaluated_at.isoformat(),
        "fleet_status": record.fleet_status,
        "gate_reason": record.gate_reason,
        "gate_status": record.gate_status,
        "opportunity_id": record.opportunity_id,
        "owners": [
            {
                "connection_epoch": item.connection_epoch,
                "feature_identity_sha256": item.feature_identity_sha256,
                "instrument_id": item.instrument_id,
                "last_sequence": item.last_sequence,
                "owner_status": item.owner_status,
                "profile_evidence_sha256": item.profile_evidence_sha256,
                "runtime_status": item.runtime_status,
                "symbol": item.symbol,
            }
            for item in record.owners
        ],
        "policy_identity_sha256": record.policy_identity_sha256,
        "policy_status": record.policy_status,
    }


def _canonical_bytes(value: _AuditPayload) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


_RECORD_KEYS = {
    "cycle_id",
    "evaluated_at",
    "fleet_status",
    "gate_reason",
    "gate_status",
    "opportunity_id",
    "owners",
    "policy_identity_sha256",
    "policy_status",
}


__all__ = (
    "RuntimeFleetAuditError",
    "RuntimeFleetAuditRecord",
    "RuntimeOwnerAudit",
    "build_runtime_fleet_audit",
    "record_bytes",
    "record_from_bytes",
    "validate_runtime_fleet_audit",
)
