from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import TypedDict, override

from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEvent,
    KrThemeDaySessionPhaseStatus,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidKrThemeDaySessionEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day session source evidence is invalid"


@dataclass(frozen=True, slots=True)
class KrThemeDaySessionSourceState:
    source_state_sha256: str
    reference_count: int

    def __post_init__(self) -> None:
        if (
            _HEX64.fullmatch(self.source_state_sha256) is None
            or type(self.reference_count) is not int
            or self.reference_count <= 0
        ):
            raise InvalidKrThemeDaySessionEvidenceError


@dataclass(frozen=True, slots=True)
class KrThemeDaySessionSourceAttestation:
    attestation_id: str
    event_id: str
    session_id: str
    phase: KrThemeDaySessionPhase
    cycle_key: str
    source_state_sha256: str
    reference_count: int


class _Payload(TypedDict):
    attestation_id: str
    cycle_key: str
    event_id: str
    phase: str
    reference_count: int
    session_id: str
    source_state_sha256: str


def build_kr_theme_day_session_source_attestation(
    event: KrThemeDaySessionPhaseEvent,
    state: KrThemeDaySessionSourceState,
) -> KrThemeDaySessionSourceAttestation:
    if event.status is not KrThemeDaySessionPhaseStatus.COMPLETED:
        raise InvalidKrThemeDaySessionEvidenceError
    provisional = KrThemeDaySessionSourceAttestation(
        "0" * 64,
        event.event_id,
        event.session_id,
        event.phase,
        event.cycle_key,
        state.source_state_sha256,
        state.reference_count,
    )
    attestation = replace(provisional, attestation_id=_attestation_id(provisional))
    validate_kr_theme_day_session_source_attestation(attestation)
    return attestation


def validate_kr_theme_day_session_source_attestation(
    attestation: KrThemeDaySessionSourceAttestation,
) -> None:
    if (
        type(attestation) is not KrThemeDaySessionSourceAttestation
        or any(
            _HEX64.fullmatch(value) is None
            for value in (
                attestation.attestation_id,
                attestation.event_id,
                attestation.session_id,
                attestation.source_state_sha256,
            )
        )
        or type(attestation.phase) is not KrThemeDaySessionPhase
        or not attestation.cycle_key
        or attestation.cycle_key != attestation.cycle_key.strip()
        or type(attestation.reference_count) is not int
        or attestation.reference_count <= 0
        or attestation.attestation_id != _attestation_id(attestation)
    ):
        raise InvalidKrThemeDaySessionEvidenceError


def kr_theme_day_session_source_attestation_bytes(
    attestation: KrThemeDaySessionSourceAttestation,
) -> bytes:
    validate_kr_theme_day_session_source_attestation(attestation)
    return _canonical(_payload(attestation)) + b"\n"


def kr_theme_day_session_source_attestation_from_bytes(
    value: bytes,
) -> KrThemeDaySessionSourceAttestation:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != set(_Payload.__required_keys__):
            raise InvalidKrThemeDaySessionEvidenceError
        attestation = KrThemeDaySessionSourceAttestation(
            payload["attestation_id"],
            payload["event_id"],
            payload["session_id"],
            KrThemeDaySessionPhase(payload["phase"]),
            payload["cycle_key"],
            payload["source_state_sha256"],
            payload["reference_count"],
        )
        validate_kr_theme_day_session_source_attestation(attestation)
        if kr_theme_day_session_source_attestation_bytes(attestation) != value:
            raise InvalidKrThemeDaySessionEvidenceError
        return attestation
    except (KeyError, TypeError, ValueError):
        raise InvalidKrThemeDaySessionEvidenceError from None


def kr_theme_day_session_source_state(references: tuple[str, ...]) -> KrThemeDaySessionSourceState:
    if not references or references != tuple(sorted(set(references))):
        raise InvalidKrThemeDaySessionEvidenceError
    payload = json.dumps(references, ensure_ascii=True, separators=(",", ":")).encode()
    return KrThemeDaySessionSourceState(hashlib.sha256(payload).hexdigest(), len(references))


def _attestation_id(attestation: KrThemeDaySessionSourceAttestation) -> str:
    payload = _payload(attestation)
    payload["attestation_id"] = ""
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _payload(attestation: KrThemeDaySessionSourceAttestation) -> _Payload:
    return {
        "attestation_id": attestation.attestation_id,
        "cycle_key": attestation.cycle_key,
        "event_id": attestation.event_id,
        "phase": attestation.phase.value,
        "reference_count": attestation.reference_count,
        "session_id": attestation.session_id,
        "source_state_sha256": attestation.source_state_sha256,
    }


def _canonical(payload: _Payload) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
