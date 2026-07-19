from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypedDict, assert_never

from trading_agent.alpaca_sip_trade_stream_supervisor import (
    AlpacaSipSupervisorStatus,
    AlpacaSipSupervisorStopReason,
    AlpacaSipTradeStreamSupervisorError,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AlpacaSipSupervisorAuditKind(StrEnum):
    STARTED = "started"
    CONNECTING = "connecting"
    RETRY_SCHEDULED = "retry_scheduled"
    READY = "ready"
    BLOCKED = "blocked"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class AlpacaSipSupervisorAuditEvent:
    event_id: str
    run_id: str
    sequence: int
    previous_event_id: str | None
    observed_at: dt.datetime
    kind: AlpacaSipSupervisorAuditKind
    operation_count: int
    total_connection_count: int
    retry_delay_seconds: float | None
    status: AlpacaSipSupervisorStatus | None
    stop_reason: AlpacaSipSupervisorStopReason | None


class _EventPayload(TypedDict):
    event_id: str
    kind: str
    observed_at: str
    operation_count: int
    previous_event_id: str | None
    retry_delay_seconds: float | None
    run_id: str
    sequence: int
    status: str | None
    stop_reason: str | None
    total_connection_count: int


def build_audit_event(
    run_id: str,
    sequence: int,
    previous_event_id: str | None,
    observed_at: dt.datetime,
    kind: AlpacaSipSupervisorAuditKind,
    operation_count: int,
    total_connection_count: int,
    *,
    retry_delay_seconds: float | None = None,
    status: AlpacaSipSupervisorStatus | None = None,
    stop_reason: AlpacaSipSupervisorStopReason | None = None,
) -> AlpacaSipSupervisorAuditEvent:
    provisional = AlpacaSipSupervisorAuditEvent(
        "0" * 64,
        run_id,
        sequence,
        previous_event_id,
        observed_at,
        kind,
        operation_count,
        total_connection_count,
        retry_delay_seconds,
        status,
        stop_reason,
    )
    event = replace(provisional, event_id=_event_id(provisional))
    validate_audit_event(event)
    return event


def validate_audit_event(event: AlpacaSipSupervisorAuditEvent) -> None:
    if (
        type(event) is not AlpacaSipSupervisorAuditEvent
        or _HEX64.fullmatch(event.event_id) is None
        or _HEX64.fullmatch(event.run_id) is None
        or type(event.sequence) is not int
        or event.sequence <= 0
        or not _aware(event.observed_at)
        or type(event.kind) is not AlpacaSipSupervisorAuditKind
        or type(event.operation_count) is not int
        or event.operation_count < 0
        or type(event.total_connection_count) is not int
        or event.total_connection_count < 0
        or not _valid_shape(event)
        or event.event_id != _event_id(event)
    ):
        raise AlpacaSipTradeStreamSupervisorError


def audit_event_bytes(event: AlpacaSipSupervisorAuditEvent) -> bytes:
    validate_audit_event(event)
    return _canonical_bytes(_payload(event)) + b"\n"


def audit_event_from_bytes(value: bytes) -> AlpacaSipSupervisorAuditEvent:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != _EVENT_KEYS:
            raise AlpacaSipTradeStreamSupervisorError
        event = AlpacaSipSupervisorAuditEvent(
            payload["event_id"],
            payload["run_id"],
            payload["sequence"],
            payload["previous_event_id"],
            dt.datetime.fromisoformat(payload["observed_at"]),
            AlpacaSipSupervisorAuditKind(payload["kind"]),
            payload["operation_count"],
            payload["total_connection_count"],
            payload["retry_delay_seconds"],
            None if payload["status"] is None else AlpacaSipSupervisorStatus(payload["status"]),
            None if payload["stop_reason"] is None else AlpacaSipSupervisorStopReason(payload["stop_reason"]),
        )
        validate_audit_event(event)
        if audit_event_bytes(event) != value:
            raise AlpacaSipTradeStreamSupervisorError
        return event
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipTradeStreamSupervisorError from None


def _valid_shape(event: AlpacaSipSupervisorAuditEvent) -> bool:
    previous_valid = event.previous_event_id is None if event.sequence == 1 else _is_hex(event.previous_event_id)
    if not previous_valid:
        return False
    match event.kind:
        case AlpacaSipSupervisorAuditKind.STARTED:
            return event.sequence == 1 and event.operation_count == 0 and _open(event) and _no_delay(event)
        case AlpacaSipSupervisorAuditKind.CONNECTING:
            return event.operation_count > 0 and _open(event) and _no_delay(event)
        case AlpacaSipSupervisorAuditKind.RETRY_SCHEDULED:
            return event.operation_count > 0 and _open(event) and _valid_delay(event.retry_delay_seconds)
        case AlpacaSipSupervisorAuditKind.READY:
            return event.status is AlpacaSipSupervisorStatus.READY and event.stop_reason is None and _no_delay(event)
        case AlpacaSipSupervisorAuditKind.BLOCKED:
            return (
                event.status is AlpacaSipSupervisorStatus.BLOCKED and event.stop_reason is not None and _no_delay(event)
            )
        case AlpacaSipSupervisorAuditKind.STOPPED:
            return (
                event.status is AlpacaSipSupervisorStatus.STOPPED
                and event.stop_reason is AlpacaSipSupervisorStopReason.GRACEFUL_SHUTDOWN
                and _no_delay(event)
            )
        case unreachable:
            assert_never(unreachable)


def _open(event: AlpacaSipSupervisorAuditEvent) -> bool:
    return event.status is None and event.stop_reason is None


def _no_delay(event: AlpacaSipSupervisorAuditEvent) -> bool:
    return event.retry_delay_seconds is None


def _valid_delay(value: float | None) -> bool:
    return type(value) is float and 1.0 <= value <= 60.0


def _is_hex(value: str | None) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


def _event_id(event: AlpacaSipSupervisorAuditEvent) -> str:
    payload = _payload(event)
    payload["event_id"] = ""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _payload(event: AlpacaSipSupervisorAuditEvent) -> _EventPayload:
    return {
        "event_id": event.event_id,
        "kind": event.kind.value,
        "observed_at": event.observed_at.isoformat(),
        "operation_count": event.operation_count,
        "previous_event_id": event.previous_event_id,
        "retry_delay_seconds": event.retry_delay_seconds,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "status": None if event.status is None else event.status.value,
        "stop_reason": None if event.stop_reason is None else event.stop_reason.value,
        "total_connection_count": event.total_connection_count,
    }


def _canonical_bytes(value: _EventPayload) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


_EVENT_KEYS = set(_EventPayload.__required_keys__)


__all__ = (
    "AlpacaSipSupervisorAuditEvent",
    "AlpacaSipSupervisorAuditKind",
    "audit_event_bytes",
    "audit_event_from_bytes",
    "build_audit_event",
    "validate_audit_event",
)
