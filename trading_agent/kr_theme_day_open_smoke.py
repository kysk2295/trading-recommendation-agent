from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Final, Literal, Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhase,
    KrThemeDaySessionPhaseEvent,
    KrThemeDaySessionPhaseStatus,
    validate_kr_theme_day_session_phase_event,
)
from trading_agent.kr_theme_day_session_evidence import (
    KrThemeDaySessionSourceAttestation,
    validate_kr_theme_day_session_source_attestation,
)
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionManifest
from trading_agent.kr_theme_day_session_source_state import (
    resolve_kr_theme_day_session_source_state,
    resolve_kr_theme_day_session_source_state_at,
)
from trading_agent.kr_theme_day_session_verifier import KrThemeDaySessionVerificationResult
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.private_query_file import read_private_text_query_only

KST: Final = ZoneInfo("Asia/Seoul")
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_PHASES: Final = (
    KrThemeDaySessionPhase.INTRADAY_COLLECT,
    KrThemeDaySessionPhase.INTRADAY_ENTRY,
    KrThemeDaySessionPhase.INTRADAY_EXIT,
)
_SESSION_PREFIX_PHASES: Final = (
    KrThemeDaySessionPhase.REGISTER,
    KrThemeDaySessionPhase.START,
)
_OPEN_SESSION_PHASES: Final = frozenset((*_SESSION_PREFIX_PHASES, *_REQUIRED_PHASES))


class InvalidKrThemeDayOpenSmokeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day open-session smoke evidence is invalid"


class KrThemeDayOpenSmokeEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    evidence_id: str
    session_id: str
    session_date: dt.date
    verified_at: dt.datetime
    cycle_key: str
    phase_event_ids: tuple[str, ...]
    source_attestation_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise InvalidKrThemeDayOpenSmokeError
        local = self.verified_at.astimezone(KST)
        try:
            parsed_cycle = dt.datetime.fromisoformat(self.cycle_key)
        except ValueError:
            raise InvalidKrThemeDayOpenSmokeError from None
        if parsed_cycle.tzinfo is None or parsed_cycle.utcoffset() is None:
            raise InvalidKrThemeDayOpenSmokeError
        cycle = parsed_cycle.astimezone(KST)
        identifiers = (self.evidence_id, self.session_id, *self.phase_event_ids, *self.source_attestation_ids)
        if (
            local.date() != self.session_date
            or not dt.time(9, 1) <= local.time() < dt.time(15, 30)
            or cycle != local.replace(second=0, microsecond=0)
            or len(self.phase_event_ids) != len(_REQUIRED_PHASES)
            or len(self.source_attestation_ids) != len(_REQUIRED_PHASES)
            or len(set(self.phase_event_ids)) != len(_REQUIRED_PHASES)
            or len(set(self.source_attestation_ids)) != len(_REQUIRED_PHASES)
            or any(_HEX64.fullmatch(value) is None for value in identifiers)
            or self.evidence_id != _evidence_id(self)
        ):
            raise InvalidKrThemeDayOpenSmokeError
        return self


def attest_kr_theme_day_open_smoke(
    manifest: KrThemeDaySessionManifest,
    verification: KrThemeDaySessionVerificationResult,
    events: tuple[KrThemeDaySessionPhaseEvent, ...],
    attestations: tuple[KrThemeDaySessionSourceAttestation, ...],
    verified_at: dt.datetime,
) -> KrThemeDayOpenSmokeEvidence:
    try:
        manifest = KrThemeDaySessionManifest.model_validate(manifest.model_dump(mode="python"))
        for event in events:
            validate_kr_theme_day_session_phase_event(event)
        for attestation in attestations:
            validate_kr_theme_day_session_source_attestation(attestation)
        local = verified_at.astimezone(KST)
        if (
            manifest.paths.intraday_fixture_manifest is not None
            or manifest.paths.eod_fixture_manifest is not None
            or type(verification) is not KrThemeDaySessionVerificationResult
            or not verification.ready
            or verification.event_count != len(events)
            or verified_at.tzinfo is None
            or verified_at.utcoffset() is None
            or local.date() != manifest.session_date
            or not dt.time(9, 1) <= local.time() < dt.time(15, 30)
            or any(event.phase not in _OPEN_SESSION_PHASES or event.observed_at > verified_at for event in events)
        ):
            raise InvalidKrThemeDayOpenSmokeError
        cycle_key = local.replace(second=0, microsecond=0).isoformat(timespec="minutes")
        selected_events = tuple(
            _latest_completed_event(events, manifest.session_id, phase, cycle_key) for phase in _REQUIRED_PHASES
        )
        prefix_events = tuple(
            _latest_completed_event(events, manifest.session_id, phase, "session") for phase in _SESSION_PREFIX_PHASES
        )
        ordered = (*prefix_events, *selected_events)
        sequences = tuple(event.sequence for event in ordered)
        if sequences != tuple(sorted(set(sequences))):
            raise InvalidKrThemeDayOpenSmokeError
        _ = tuple(_source_attestation(manifest, event, attestations, verified_at) for event in prefix_events)
        selected_attestations = tuple(
            _event_attestation(manifest, event, attestations, verified_at) for event in selected_events
        )
        provisional = KrThemeDayOpenSmokeEvidence.model_construct(
            schema_version=1,
            evidence_id="0" * 64,
            session_id=manifest.session_id,
            session_date=manifest.session_date,
            verified_at=verified_at,
            cycle_key=cycle_key,
            phase_event_ids=tuple(event.event_id for event in selected_events),
            source_attestation_ids=tuple(item.attestation_id for item in selected_attestations),
        )
        return KrThemeDayOpenSmokeEvidence.model_validate(
            provisional.model_dump(mode="python") | {"evidence_id": _evidence_id(provisional)}
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpenSmokeError from None


def publish_kr_theme_day_open_smoke(path: Path, evidence: KrThemeDayOpenSmokeEvidence) -> bool:
    try:
        validated = KrThemeDayOpenSmokeEvidence.model_validate(evidence.model_dump(mode="python"))
        return publish_private_immutable_text(path, _evidence_text(validated))
    except (AttributeError, InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpenSmokeError from None


def load_kr_theme_day_open_smoke(path: Path) -> KrThemeDayOpenSmokeEvidence:
    try:
        return _parse_evidence(read_private_text(path))
    except (InvalidPrivateImmutableFileError, OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpenSmokeError from None


def load_kr_theme_day_open_smoke_query_only(path: Path) -> KrThemeDayOpenSmokeEvidence:
    try:
        return _parse_evidence(read_private_text_query_only(path))
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayOpenSmokeError from None


def _parse_evidence(payload: str) -> KrThemeDayOpenSmokeEvidence:
    evidence = KrThemeDayOpenSmokeEvidence.model_validate_json(payload)
    if payload != _evidence_text(evidence):
        raise InvalidKrThemeDayOpenSmokeError
    return evidence


def _latest_completed_event(
    events: tuple[KrThemeDaySessionPhaseEvent, ...],
    session_id: str,
    phase: KrThemeDaySessionPhase,
    cycle_key: str,
) -> KrThemeDaySessionPhaseEvent:
    matches = tuple(event for event in events if event.session_id == session_id and event.phase is phase)
    if not matches:
        raise InvalidKrThemeDayOpenSmokeError
    latest = max(matches, key=lambda event: event.sequence)
    if (
        latest.cycle_key != cycle_key
        or latest.status is not KrThemeDaySessionPhaseStatus.COMPLETED
        or latest.exit_code != 0
    ):
        raise InvalidKrThemeDayOpenSmokeError
    return latest


def _event_attestation(
    manifest: KrThemeDaySessionManifest,
    event: KrThemeDaySessionPhaseEvent,
    attestations: tuple[KrThemeDaySessionSourceAttestation, ...],
    verified_at: dt.datetime,
) -> KrThemeDaySessionSourceAttestation:
    cycle = dt.datetime.fromisoformat(event.cycle_key).astimezone(KST)
    observed = event.observed_at.astimezone(KST)
    if not cycle <= observed < cycle + dt.timedelta(minutes=1):
        raise InvalidKrThemeDayOpenSmokeError
    return _source_attestation(manifest, event, attestations, verified_at)


def _source_attestation(
    manifest: KrThemeDaySessionManifest,
    event: KrThemeDaySessionPhaseEvent,
    attestations: tuple[KrThemeDaySessionSourceAttestation, ...],
    verified_at: dt.datetime,
) -> KrThemeDaySessionSourceAttestation:
    matches = tuple(item for item in attestations if item.event_id == event.event_id)
    current = resolve_kr_theme_day_session_source_state(manifest, event.phase, event.cycle_key)
    causal = resolve_kr_theme_day_session_source_state_at(manifest, event.phase, event.cycle_key, verified_at)
    if (
        len(matches) != 1
        or event.observed_at > verified_at
        or matches[0].session_id != event.session_id
        or matches[0].phase is not event.phase
        or matches[0].cycle_key != event.cycle_key
        or matches[0].source_state_sha256 != current.source_state_sha256
        or matches[0].reference_count != current.reference_count
        or current != causal
    ):
        raise InvalidKrThemeDayOpenSmokeError
    return matches[0]


def _evidence_text(evidence: KrThemeDayOpenSmokeEvidence) -> str:
    return (
        json.dumps(
            evidence.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _evidence_id(evidence: KrThemeDayOpenSmokeEvidence) -> str:
    payload = evidence.model_dump(mode="json", exclude={"evidence_id"})
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()
