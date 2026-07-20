from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypedDict, assert_never, override

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidUsNewsCatalystDaySessionAuditError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst day session audit is invalid"


class UsNewsCatalystDaySessionPhase(StrEnum):
    REGISTER = "register"
    START = "start"
    COLLECT = "collect"
    OBSERVE = "observe"
    FINALIZE = "finalize"
    REVIEW = "review"


class UsNewsCatalystDaySessionEventStatus(StrEnum):
    COMPLETED = "completed"
    RECOVERED = "recovered"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class UsNewsCatalystDaySessionEventRequest:
    session_id: str
    phase: UsNewsCatalystDaySessionPhase
    observed_at: dt.datetime
    status: UsNewsCatalystDaySessionEventStatus
    command_exit_code: int | None
    evidence_sha256: str | None
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class UsNewsCatalystDaySessionEvent:
    event_id: str
    session_id: str
    sequence: int
    previous_event_id: str | None
    phase: UsNewsCatalystDaySessionPhase
    observed_at: dt.datetime
    status: UsNewsCatalystDaySessionEventStatus
    command_exit_code: int | None
    evidence_sha256: str | None
    reason_code: str | None


class _Payload(TypedDict):
    command_exit_code: int | None
    event_id: str
    evidence_sha256: str | None
    observed_at: str
    phase: str
    previous_event_id: str | None
    reason_code: str | None
    sequence: int
    session_id: str
    status: str


def build_us_news_catalyst_day_session_event(
    request: UsNewsCatalystDaySessionEventRequest,
    sequence: int,
    previous_event_id: str | None,
) -> UsNewsCatalystDaySessionEvent:
    provisional = UsNewsCatalystDaySessionEvent(
        "0" * 64,
        request.session_id,
        sequence,
        previous_event_id,
        request.phase,
        request.observed_at,
        request.status,
        request.command_exit_code,
        request.evidence_sha256,
        request.reason_code,
    )
    event = replace(provisional, event_id=_event_id(provisional))
    validate_us_news_catalyst_day_session_event(event)
    return event


def validate_us_news_catalyst_day_session_event(event: UsNewsCatalystDaySessionEvent) -> None:
    previous_ok = event.previous_event_id is None if event.sequence == 1 else _hex(event.previous_event_id)
    if (
        type(event) is not UsNewsCatalystDaySessionEvent
        or not _hex(event.event_id)
        or not _hex(event.session_id)
        or type(event.sequence) is not int
        or event.sequence <= 0
        or not previous_ok
        or type(event.phase) is not UsNewsCatalystDaySessionPhase
        or not _aware(event.observed_at)
        or type(event.status) is not UsNewsCatalystDaySessionEventStatus
        or not _valid_outcome(event)
        or event.event_id != _event_id(event)
    ):
        raise InvalidUsNewsCatalystDaySessionAuditError


def us_news_catalyst_day_session_event_bytes(event: UsNewsCatalystDaySessionEvent) -> bytes:
    validate_us_news_catalyst_day_session_event(event)
    return json.dumps(_payload(event), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode() + b"\n"


def us_news_catalyst_day_session_event_from_bytes(value: bytes) -> UsNewsCatalystDaySessionEvent:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != set(_Payload.__required_keys__):
            raise InvalidUsNewsCatalystDaySessionAuditError
        event = UsNewsCatalystDaySessionEvent(
            payload["event_id"], payload["session_id"], payload["sequence"],
            payload["previous_event_id"], UsNewsCatalystDaySessionPhase(payload["phase"]),
            dt.datetime.fromisoformat(payload["observed_at"]),
            UsNewsCatalystDaySessionEventStatus(payload["status"]),
            payload["command_exit_code"], payload["evidence_sha256"], payload["reason_code"],
        )
        validate_us_news_catalyst_day_session_event(event)
        if us_news_catalyst_day_session_event_bytes(event) != value:
            raise InvalidUsNewsCatalystDaySessionAuditError
        return event
    except (KeyError, TypeError, ValueError):
        raise InvalidUsNewsCatalystDaySessionAuditError from None


def _valid_outcome(event: UsNewsCatalystDaySessionEvent) -> bool:
    exit_ok = event.command_exit_code is None or (
        type(event.command_exit_code) is int and event.command_exit_code >= 0
    )
    reason_ok = event.reason_code is None or _canonical_text(event.reason_code)
    evidence_ok = event.evidence_sha256 is None or _hex(event.evidence_sha256)
    if not exit_ok or not reason_ok or not evidence_ok:
        return False
    match event.status:
        case UsNewsCatalystDaySessionEventStatus.COMPLETED:
            return (
                event.command_exit_code is not None
                and event.evidence_sha256 is not None
                and event.reason_code is None
            )
        case UsNewsCatalystDaySessionEventStatus.RECOVERED:
            return event.command_exit_code is None and event.evidence_sha256 is not None and event.reason_code is None
        case UsNewsCatalystDaySessionEventStatus.SKIPPED:
            return (
                event.command_exit_code is None
                and event.evidence_sha256 is not None
                and event.reason_code is not None
            )
        case UsNewsCatalystDaySessionEventStatus.BLOCKED:
            return event.evidence_sha256 is None and event.reason_code is not None
        case unreachable:
            assert_never(unreachable)


def _event_id(event: UsNewsCatalystDaySessionEvent) -> str:
    payload = _payload(event)
    payload["event_id"] = ""
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _payload(event: UsNewsCatalystDaySessionEvent) -> _Payload:
    return {
        "command_exit_code": event.command_exit_code, "event_id": event.event_id,
        "evidence_sha256": event.evidence_sha256, "observed_at": event.observed_at.isoformat(),
        "phase": event.phase.value, "previous_event_id": event.previous_event_id,
        "reason_code": event.reason_code, "sequence": event.sequence,
        "session_id": event.session_id, "status": event.status.value,
    }


def _hex(value: str | None) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(char in value for char in "\r\n\t")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
