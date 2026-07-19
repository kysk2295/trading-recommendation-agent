from __future__ import annotations

from dataclasses import dataclass
from typing import override

from trading_agent.kis_kr_session_calendar_store import KisKrSessionCalendarStore
from trading_agent.kr_theme_day_session_audit import (
    KrThemeDaySessionPhaseEvent,
    KrThemeDaySessionPhaseStatus,
)
from trading_agent.kr_theme_day_session_audit_store import KrThemeDaySessionAuditStore
from trading_agent.kr_theme_day_session_evidence import KrThemeDaySessionSourceAttestation
from trading_agent.kr_theme_day_session_evidence_store import KrThemeDaySessionEvidenceStore
from trading_agent.kr_theme_day_session_manifest import KrThemeDaySessionManifest
from trading_agent.kr_theme_day_session_source_state import resolve_kr_theme_day_session_source_state


class InvalidKrThemeDaySessionVerificationError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day session verification is invalid"


@dataclass(frozen=True, slots=True)
class KrThemeDaySessionVerificationResult:
    event_count: int
    completed_count: int
    blocked_count: int
    attested_count: int

    @property
    def ready(self) -> bool:
        return self.completed_count > 0 and self.blocked_count == 0 and self.completed_count == self.attested_count


def verify_kr_theme_day_session(
    manifest: KrThemeDaySessionManifest,
) -> KrThemeDaySessionVerificationResult:
    try:
        _require_calendar(manifest)
        events = KrThemeDaySessionAuditStore(manifest.paths.audit_store).events(manifest.session_id)
        attestations = KrThemeDaySessionEvidenceStore(manifest.paths.audit_store).attestations(manifest.session_id)
        if not events:
            raise InvalidKrThemeDaySessionVerificationError
        _require_attestation_links(events, attestations)
        latest = _latest_attempts(events)
        completed = tuple(event for event in latest if event.status is KrThemeDaySessionPhaseStatus.COMPLETED)
        blocked = tuple(event for event in latest if event.status is KrThemeDaySessionPhaseStatus.BLOCKED)
        for event in completed:
            _require_current_source(manifest, event, attestations)
        return KrThemeDaySessionVerificationResult(
            event_count=len(events),
            completed_count=len(completed),
            blocked_count=len(blocked),
            attested_count=len(completed),
        )
    except (AttributeError, TypeError, ValueError):
        raise InvalidKrThemeDaySessionVerificationError from None


def _require_calendar(manifest: KrThemeDaySessionManifest) -> None:
    matches = tuple(
        snapshot
        for snapshot in KisKrSessionCalendarStore(manifest.paths.calendar_store).snapshots()
        if snapshot.snapshot_id == manifest.calendar_snapshot_id
    )
    if len(matches) != 1:
        raise InvalidKrThemeDaySessionVerificationError
    days = tuple(day for day in matches[0].payload.days if day.session_date == manifest.session_date)
    if len(days) != 1 or not days[0].business_day or not days[0].trading_day or not days[0].open_day:
        raise InvalidKrThemeDaySessionVerificationError


def _require_attestation_links(
    events: tuple[KrThemeDaySessionPhaseEvent, ...],
    attestations: tuple[KrThemeDaySessionSourceAttestation, ...],
) -> None:
    completed = {event.event_id: event for event in events if event.status is KrThemeDaySessionPhaseStatus.COMPLETED}
    for attestation in attestations:
        event = completed.get(attestation.event_id)
        if (
            event is None
            or attestation.session_id != event.session_id
            or attestation.phase is not event.phase
            or attestation.cycle_key != event.cycle_key
        ):
            raise InvalidKrThemeDaySessionVerificationError


def _latest_attempts(
    events: tuple[KrThemeDaySessionPhaseEvent, ...],
) -> tuple[KrThemeDaySessionPhaseEvent, ...]:
    latest: dict[tuple[str, str], KrThemeDaySessionPhaseEvent] = {}
    for event in events:
        latest[(event.phase.value, event.cycle_key)] = event
    return tuple(sorted(latest.values(), key=lambda event: event.sequence))


def _require_current_source(
    manifest: KrThemeDaySessionManifest,
    event: KrThemeDaySessionPhaseEvent,
    attestations: tuple[KrThemeDaySessionSourceAttestation, ...],
) -> None:
    matches = tuple(attestation for attestation in attestations if attestation.event_id == event.event_id)
    if len(matches) != 1:
        raise InvalidKrThemeDaySessionVerificationError
    current = resolve_kr_theme_day_session_source_state(manifest, event.phase, event.cycle_key)
    if (
        matches[0].source_state_sha256 != current.source_state_sha256
        or matches[0].reference_count != current.reference_count
    ):
        raise InvalidKrThemeDaySessionVerificationError
