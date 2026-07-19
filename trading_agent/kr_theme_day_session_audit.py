from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypedDict, override

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidKrThemeDaySessionAuditError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day session phase audit is invalid"


class KrThemeDaySessionPhase(StrEnum):
    REGISTER = "register"
    START = "start"
    INTRADAY_COLLECT = "intraday_collect"
    INTRADAY_ENTRY = "intraday_entry"
    INTRADAY_EXIT = "intraday_exit"
    EOD_COLLECT = "eod_collect"
    EOD_EXIT = "eod_exit"
    POST_SESSION = "post_session"


class KrThemeDaySessionPhaseStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class KrThemeDaySessionPhaseEventRequest:
    session_id: str
    phase: KrThemeDaySessionPhase
    cycle_key: str
    observed_at: dt.datetime
    status: KrThemeDaySessionPhaseStatus
    exit_code: int


@dataclass(frozen=True, slots=True)
class KrThemeDaySessionPhaseEvent:
    event_id: str
    session_id: str
    sequence: int
    previous_event_id: str | None
    phase: KrThemeDaySessionPhase
    cycle_key: str
    observed_at: dt.datetime
    status: KrThemeDaySessionPhaseStatus
    exit_code: int


class _Payload(TypedDict):
    cycle_key: str
    event_id: str
    exit_code: int
    observed_at: str
    phase: str
    previous_event_id: str | None
    sequence: int
    session_id: str
    status: str


def build_kr_theme_day_session_phase_event(
    request: KrThemeDaySessionPhaseEventRequest,
    sequence: int,
    previous_event_id: str | None,
) -> KrThemeDaySessionPhaseEvent:
    provisional = KrThemeDaySessionPhaseEvent(
        "0" * 64,
        request.session_id,
        sequence,
        previous_event_id,
        request.phase,
        request.cycle_key,
        request.observed_at,
        request.status,
        request.exit_code,
    )
    event = replace(provisional, event_id=_event_id(provisional))
    validate_kr_theme_day_session_phase_event(event)
    return event


def validate_kr_theme_day_session_phase_event(event: KrThemeDaySessionPhaseEvent) -> None:
    previous_ok = event.previous_event_id is None if event.sequence == 1 else _is_hex(event.previous_event_id)
    if (
        type(event) is not KrThemeDaySessionPhaseEvent
        or _HEX64.fullmatch(event.event_id) is None
        or _HEX64.fullmatch(event.session_id) is None
        or type(event.sequence) is not int
        or event.sequence <= 0
        or not previous_ok
        or type(event.phase) is not KrThemeDaySessionPhase
        or not event.cycle_key
        or event.cycle_key != event.cycle_key.strip()
        or not _aware(event.observed_at)
        or type(event.status) is not KrThemeDaySessionPhaseStatus
        or type(event.exit_code) is not int
        or event.exit_code < 0
        or (event.status is KrThemeDaySessionPhaseStatus.COMPLETED) != (event.exit_code == 0)
        or event.event_id != _event_id(event)
    ):
        raise InvalidKrThemeDaySessionAuditError


def kr_theme_day_session_phase_event_bytes(event: KrThemeDaySessionPhaseEvent) -> bytes:
    validate_kr_theme_day_session_phase_event(event)
    return json.dumps(_payload(event), ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode() + b"\n"


def kr_theme_day_session_phase_event_from_bytes(value: bytes) -> KrThemeDaySessionPhaseEvent:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != set(_Payload.__required_keys__):
            raise InvalidKrThemeDaySessionAuditError
        event = KrThemeDaySessionPhaseEvent(
            payload["event_id"],
            payload["session_id"],
            payload["sequence"],
            payload["previous_event_id"],
            KrThemeDaySessionPhase(payload["phase"]),
            payload["cycle_key"],
            dt.datetime.fromisoformat(payload["observed_at"]),
            KrThemeDaySessionPhaseStatus(payload["status"]),
            payload["exit_code"],
        )
        validate_kr_theme_day_session_phase_event(event)
        if kr_theme_day_session_phase_event_bytes(event) != value:
            raise InvalidKrThemeDaySessionAuditError
        return event
    except (KeyError, TypeError, ValueError):
        raise InvalidKrThemeDaySessionAuditError from None


def _event_id(event: KrThemeDaySessionPhaseEvent) -> str:
    payload = _payload(event)
    payload["event_id"] = ""
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _payload(event: KrThemeDaySessionPhaseEvent) -> _Payload:
    return {
        "cycle_key": event.cycle_key,
        "event_id": event.event_id,
        "exit_code": event.exit_code,
        "observed_at": event.observed_at.isoformat(),
        "phase": event.phase.value,
        "previous_event_id": event.previous_event_id,
        "sequence": event.sequence,
        "session_id": event.session_id,
        "status": event.status.value,
    }


def _is_hex(value: str | None) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
