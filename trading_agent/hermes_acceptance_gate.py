from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Final, Literal, Self, override
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from trading_agent.acceptance_evidence import AcceptanceSessionKind
from trading_agent.us_equity_calendar import regular_session_bounds

_DELIVERY_ID: Final = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID: Final = re.compile(r"^X(?:NYS|KRX)-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_SEOUL: Final = ZoneInfo("Asia/Seoul")


class HermesAcceptanceGateStatus(StrEnum):
    PASSED = "passed"
    WAITING = "waiting"
    BLOCKED = "blocked"


class HermesAcceptanceGateReason(StrEnum):
    INSUFFICIENT_US_REAL_SESSIONS = "insufficient_us_real_sessions"
    INSUFFICIENT_KR_REAL_SESSIONS = "insufficient_kr_real_sessions"
    NON_REAL_SESSION = "non_real_session"
    NON_CONSECUTIVE_REAL_SESSIONS = "non_consecutive_real_sessions"
    UNRECONCILED_DELIVERY = "unreconciled_delivery"
    DUPLICATE_DELIVERY = "duplicate_delivery"
    OMITTED_DELIVERY = "omitted_delivery"
    UNACCOUNTED_DELIVERY = "unaccounted_delivery"
    MISSING_SESSION_REPORTS = "missing_session_reports"


class InvalidHermesAcceptanceEvidenceError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: HermesAcceptanceGateReason) -> None:
        super().__init__()
        self.reason = reason

    @override
    def __str__(self) -> str:
        return self.reason.value


class HermesAcceptanceSessionEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    session_id: str
    market_id: Literal["us_equities", "kr_equities"]
    kind: AcceptanceSessionKind
    observed_from: AwareDatetime
    observed_through: AwareDatetime
    expected_delivery_ids: tuple[str, ...]
    projected_delivery_ids: tuple[str, ...]
    acknowledged_delivery_ids: tuple[str, ...]
    terminal_delivery_ids: tuple[str, ...]
    duplicate_delivery_ids: tuple[str, ...]
    omitted_delivery_ids: tuple[str, ...]
    unaccounted_delivery_ids: tuple[str, ...]
    reconciliation_artifact_path: Path
    kr_calendar_snapshot_path: Path | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        expected_prefix = "XNYS-" if self.market_id == "us_equities" else "XKRX-"
        identifier_groups = (
            self.expected_delivery_ids,
            self.projected_delivery_ids,
            self.acknowledged_delivery_ids,
            self.terminal_delivery_ids,
            self.duplicate_delivery_ids,
            self.omitted_delivery_ids,
            self.unaccounted_delivery_ids,
        )
        if (
            _SESSION_ID.fullmatch(self.session_id) is None
            or not self.session_id.startswith(expected_prefix)
            or self.observed_through < self.observed_from
            or not self.expected_delivery_ids
            or any(ids != tuple(sorted(set(ids))) for ids in identifier_groups)
            or any(_DELIVERY_ID.fullmatch(item) is None for ids in identifier_groups for item in ids)
            or self.reconciliation_artifact_path.is_absolute()
            or ".." in self.reconciliation_artifact_path.parts
            or (
                self.kr_calendar_snapshot_path is not None
                and (self.kr_calendar_snapshot_path.is_absolute() or ".." in self.kr_calendar_snapshot_path.parts)
            )
            or not _observation_matches_session(self)
        ):
            raise InvalidHermesAcceptanceEvidenceError(HermesAcceptanceGateReason.UNRECONCILED_DELIVERY)
        return self


def _observation_matches_session(session: HermesAcceptanceSessionEvidence) -> bool:
    session_date = dt.date.fromisoformat(session.session_id[5:])
    if session.market_id == "us_equities":
        bounds = regular_session_bounds(session_date)
        return bounds is not None and bounds[0] <= session.observed_from <= session.observed_through <= bounds[1]
    return (
        session.observed_from.astimezone(_SEOUL).date() == session_date
        and session.observed_through.astimezone(_SEOUL).date() == session_date
    )


class HermesAcceptanceAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: HermesAcceptanceGateStatus
    reasons: tuple[HermesAcceptanceGateReason, ...]
    us_real_session_count: int
    kr_real_session_count: int


def assess_hermes_acceptance(
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> HermesAcceptanceAssessment:
    validated = tuple(
        HermesAcceptanceSessionEvidence.model_validate(item.model_dump(mode="python")) for item in sessions
    )
    us_sessions = tuple(
        item for item in validated if item.market_id == "us_equities" and item.kind is AcceptanceSessionKind.REAL
    )
    kr_sessions = tuple(
        item for item in validated if item.market_id == "kr_equities" and item.kind is AcceptanceSessionKind.REAL
    )
    reasons = _assessment_reasons(validated, us_sessions, kr_sessions)
    status = _status_for(reasons)
    return HermesAcceptanceAssessment(
        status=status,
        reasons=reasons,
        us_real_session_count=len(us_sessions),
        kr_real_session_count=len(kr_sessions),
    )


def _assessment_reasons(
    sessions: tuple[HermesAcceptanceSessionEvidence, ...],
    us_sessions: tuple[HermesAcceptanceSessionEvidence, ...],
    kr_sessions: tuple[HermesAcceptanceSessionEvidence, ...],
) -> tuple[HermesAcceptanceGateReason, ...]:
    reasons: set[HermesAcceptanceGateReason] = set()
    if len(us_sessions) < 5:
        reasons.add(HermesAcceptanceGateReason.INSUFFICIENT_US_REAL_SESSIONS)
    if len(kr_sessions) < 5:
        reasons.add(HermesAcceptanceGateReason.INSUFFICIENT_KR_REAL_SESSIONS)
    if len(us_sessions) >= 5 and not _consecutive_us_sessions(us_sessions):
        reasons.add(HermesAcceptanceGateReason.NON_CONSECUTIVE_REAL_SESSIONS)
    for session in sessions:
        if session.kind is not AcceptanceSessionKind.REAL:
            reasons.add(HermesAcceptanceGateReason.NON_REAL_SESSION)
        expected = set(session.expected_delivery_ids)
        projected = set(session.projected_delivery_ids)
        acknowledged = set(session.acknowledged_delivery_ids)
        terminal = set(session.terminal_delivery_ids)
        if expected != projected or expected != acknowledged | terminal or acknowledged & terminal:
            reasons.add(HermesAcceptanceGateReason.UNRECONCILED_DELIVERY)
        if session.duplicate_delivery_ids:
            reasons.add(HermesAcceptanceGateReason.DUPLICATE_DELIVERY)
        if session.omitted_delivery_ids:
            reasons.add(HermesAcceptanceGateReason.OMITTED_DELIVERY)
        if session.unaccounted_delivery_ids:
            reasons.add(HermesAcceptanceGateReason.UNACCOUNTED_DELIVERY)
    return tuple(sorted(reasons, key=lambda item: item.value))


def _status_for(reasons: tuple[HermesAcceptanceGateReason, ...]) -> HermesAcceptanceGateStatus:
    if not reasons:
        return HermesAcceptanceGateStatus.PASSED
    waiting = {
        HermesAcceptanceGateReason.INSUFFICIENT_US_REAL_SESSIONS,
        HermesAcceptanceGateReason.INSUFFICIENT_KR_REAL_SESSIONS,
    }
    return HermesAcceptanceGateStatus.WAITING if set(reasons) <= waiting else HermesAcceptanceGateStatus.BLOCKED


def current_hermes_acceptance_waiting() -> HermesAcceptanceAssessment:
    return HermesAcceptanceAssessment(
        status=HermesAcceptanceGateStatus.WAITING,
        reasons=(HermesAcceptanceGateReason.MISSING_SESSION_REPORTS,),
        us_real_session_count=0,
        kr_real_session_count=0,
    )


def _consecutive_us_sessions(sessions: tuple[HermesAcceptanceSessionEvidence, ...]) -> bool:
    dates = _session_dates(sessions)
    return all(
        _next_us_session_date(previous) == current
        for previous, current in pairwise(dates)
    )


def _session_dates(sessions: tuple[HermesAcceptanceSessionEvidence, ...]) -> tuple[dt.date, ...]:
    return tuple(sorted(dt.date.fromisoformat(session.session_id[5:]) for session in sessions))


def _next_us_session_date(previous: dt.date) -> dt.date | None:
    candidate = previous + dt.timedelta(days=1)
    while candidate.year <= 2028:
        if regular_session_bounds(candidate) is not None:
            return candidate
        candidate += dt.timedelta(days=1)
    return None
