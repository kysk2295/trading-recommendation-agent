from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypedDict, assert_never, override

_HEX = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)


class RuntimeSupervisorLiveAuditError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime supervisor live actionability audit is invalid"


class RuntimeSupervisorLiveStatus(StrEnum):
    DISABLED = "disabled"
    NOT_ATTEMPTED = "not_attempted"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RuntimeSupervisorLiveOutcome:
    status: RuntimeSupervisorLiveStatus
    selected_count: int
    created_count: int
    replay_count: int

    def __post_init__(self) -> None:
        if not _valid_outcome(self):
            raise RuntimeSupervisorLiveAuditError


@dataclass(frozen=True, slots=True)
class RuntimeSupervisorLiveAudit:
    live_audit_id: str
    attempt_id: str
    status: RuntimeSupervisorLiveStatus
    selected_count: int
    created_count: int
    replay_count: int


class _LivePayload(TypedDict):
    attempt_id: str
    created_count: int
    live_audit_id: str
    replay_count: int
    selected_count: int
    status: str


def build_runtime_supervisor_live_audit(
    attempt_id: str,
    outcome: RuntimeSupervisorLiveOutcome,
) -> RuntimeSupervisorLiveAudit:
    if _HEX.fullmatch(attempt_id) is None or type(outcome) is not RuntimeSupervisorLiveOutcome:
        raise RuntimeSupervisorLiveAuditError
    provisional = RuntimeSupervisorLiveAudit(
        "0" * 64,
        attempt_id,
        outcome.status,
        outcome.selected_count,
        outcome.created_count,
        outcome.replay_count,
    )
    audit = replace(provisional, live_audit_id=_audit_sha256(provisional))
    validate_runtime_supervisor_live_audit(audit)
    return audit


def validate_runtime_supervisor_live_audit(audit: RuntimeSupervisorLiveAudit) -> None:
    if (
        type(audit) is not RuntimeSupervisorLiveAudit
        or _HEX.fullmatch(audit.live_audit_id) is None
        or _HEX.fullmatch(audit.attempt_id) is None
        or not _valid_outcome(_outcome(audit))
        or audit.live_audit_id != _audit_sha256(audit)
    ):
        raise RuntimeSupervisorLiveAuditError


def live_audit_bytes(audit: RuntimeSupervisorLiveAudit) -> bytes:
    validate_runtime_supervisor_live_audit(audit)
    return _canonical_bytes(_payload(audit)) + b"\n"


def live_audit_from_bytes(value: bytes) -> RuntimeSupervisorLiveAudit:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != _PAYLOAD_KEYS:
            raise RuntimeSupervisorLiveAuditError
        audit = RuntimeSupervisorLiveAudit(
            payload["live_audit_id"],
            payload["attempt_id"],
            RuntimeSupervisorLiveStatus(payload["status"]),
            payload["selected_count"],
            payload["created_count"],
            payload["replay_count"],
        )
        validate_runtime_supervisor_live_audit(audit)
        if live_audit_bytes(audit) != value:
            raise RuntimeSupervisorLiveAuditError
        return audit
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RuntimeSupervisorLiveAuditError from None


def _valid_outcome(outcome: RuntimeSupervisorLiveOutcome) -> bool:
    counts = (outcome.selected_count, outcome.created_count, outcome.replay_count)
    if type(outcome.status) is not RuntimeSupervisorLiveStatus or any(
        type(item) is not int or not 0 <= item <= 100 for item in counts
    ):
        return False
    match outcome.status:
        case RuntimeSupervisorLiveStatus.COMPLETED:
            return outcome.created_count + outcome.replay_count == outcome.selected_count
        case (
            RuntimeSupervisorLiveStatus.DISABLED
            | RuntimeSupervisorLiveStatus.NOT_ATTEMPTED
            | RuntimeSupervisorLiveStatus.BLOCKED
        ):
            return counts == (0, 0, 0)
        case unreachable:
            assert_never(unreachable)


def _outcome(audit: RuntimeSupervisorLiveAudit) -> RuntimeSupervisorLiveOutcome:
    return RuntimeSupervisorLiveOutcome(
        audit.status,
        audit.selected_count,
        audit.created_count,
        audit.replay_count,
    )


def _payload(audit: RuntimeSupervisorLiveAudit) -> _LivePayload:
    return {
        "attempt_id": audit.attempt_id,
        "created_count": audit.created_count,
        "live_audit_id": audit.live_audit_id,
        "replay_count": audit.replay_count,
        "selected_count": audit.selected_count,
        "status": audit.status.value,
    }


def _audit_sha256(audit: RuntimeSupervisorLiveAudit) -> str:
    payload = _payload(audit)
    payload["live_audit_id"] = ""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_bytes(payload: _LivePayload) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


_PAYLOAD_KEYS = {
    "attempt_id",
    "created_count",
    "live_audit_id",
    "replay_count",
    "selected_count",
    "status",
}

__all__ = (
    "RuntimeSupervisorLiveAudit",
    "RuntimeSupervisorLiveAuditError",
    "RuntimeSupervisorLiveOutcome",
    "RuntimeSupervisorLiveStatus",
    "build_runtime_supervisor_live_audit",
    "live_audit_bytes",
    "live_audit_from_bytes",
)
