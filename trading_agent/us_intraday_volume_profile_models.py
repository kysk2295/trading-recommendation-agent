from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, override

from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import regular_session_bounds

INTRADAY_VOLUME_PROFILE_SESSIONS: Final = 20
INTRADAY_VOLUME_PROFILE_VERSION: Final = "us_intraday_cumulative_volume_median_20_v1"
_ERROR_MESSAGE: Final = "intraday volume profile input is invalid"


class IntradayVolumeProfileError(ValueError):
    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE


@dataclass(frozen=True, slots=True)
class IntradayVolumeProfileEvidence:
    identity: ResearchInputIdentity
    instrument_id: str
    target_session_date: dt.date
    through_minute: int
    source_session_dates: tuple[dt.date, ...]
    session_cumulative_volumes: tuple[int, ...]
    expected_cumulative_volume: Decimal
    semantic_version: str
    evidence_sha256: str


def create_intraday_volume_profile_evidence(
    identity: ResearchInputIdentity,
    instrument_id: str,
    target_session_date: dt.date,
    through_minute: int,
    source_session_dates: tuple[dt.date, ...],
    session_cumulative_volumes: tuple[int, ...],
) -> IntradayVolumeProfileEvidence:
    expected = _median(session_cumulative_volumes)
    evidence = IntradayVolumeProfileEvidence(
        identity,
        instrument_id,
        target_session_date,
        through_minute,
        source_session_dates,
        session_cumulative_volumes,
        expected,
        INTRADAY_VOLUME_PROFILE_VERSION,
        _evidence_sha256(
            identity,
            instrument_id,
            target_session_date,
            through_minute,
            source_session_dates,
            session_cumulative_volumes,
            expected,
        ),
    )
    validate_intraday_volume_profile(evidence)
    return evidence


def validate_intraday_volume_profile(evidence: IntradayVolumeProfileEvidence) -> None:
    if type(evidence) is not IntradayVolumeProfileEvidence:
        raise IntradayVolumeProfileError
    dates = evidence.source_session_dates
    volumes = evidence.session_cumulative_volumes
    bounds = (
        regular_session_bounds(evidence.target_session_date) if type(evidence.target_session_date) is dt.date else None
    )
    if (
        type(evidence.identity) is not ResearchInputIdentity
        or not _valid_text(evidence.instrument_id)
        or bounds is None
        or type(evidence.through_minute) is not int
        or evidence.through_minute <= 0
        or evidence.through_minute > _session_minutes(bounds)
        or type(dates) is not tuple
        or type(volumes) is not tuple
        or len(dates) != INTRADAY_VOLUME_PROFILE_SESSIONS
        or len(volumes) != INTRADAY_VOLUME_PROFILE_SESSIONS
        or dates
        != _prior_profile_sessions(
            evidence.target_session_date,
            evidence.through_minute,
        )
        or any(type(day) is not dt.date or day >= evidence.target_session_date for day in dates)
        or any(regular_session_bounds(day) is None for day in dates)
        or any(type(volume) is not int or volume <= 0 for volume in volumes)
        or type(evidence.expected_cumulative_volume) is not Decimal
        or not evidence.expected_cumulative_volume.is_finite()
        or evidence.expected_cumulative_volume != _median(volumes)
        or evidence.semantic_version != INTRADAY_VOLUME_PROFILE_VERSION
    ):
        raise IntradayVolumeProfileError
    expected_hash = _evidence_sha256(
        evidence.identity,
        evidence.instrument_id,
        evidence.target_session_date,
        evidence.through_minute,
        dates,
        volumes,
        evidence.expected_cumulative_volume,
    )
    if evidence.evidence_sha256 != expected_hash:
        raise IntradayVolumeProfileError


def _median(values: tuple[int, ...]) -> Decimal:
    if type(values) is not tuple or len(values) != INTRADAY_VOLUME_PROFILE_SESSIONS:
        raise IntradayVolumeProfileError
    try:
        ordered = sorted(values)
        middle = len(ordered) // 2
        return (Decimal(ordered[middle - 1]) + Decimal(ordered[middle])) / Decimal(2)
    except (ArithmeticError, TypeError, ValueError):
        raise IntradayVolumeProfileError from None


def _evidence_sha256(
    identity: ResearchInputIdentity,
    instrument_id: str,
    target_session_date: dt.date,
    through_minute: int,
    source_session_dates: tuple[dt.date, ...],
    session_cumulative_volumes: tuple[int, ...],
    expected: Decimal,
) -> str:
    try:
        payload = {
            "expected_cumulative_volume": str(expected),
            "identity_sha256": identity.identity_sha256,
            "instrument_id": instrument_id,
            "semantic_version": INTRADAY_VOLUME_PROFILE_VERSION,
            "session_cumulative_volumes": session_cumulative_volumes,
            "source_session_dates": tuple(day.isoformat() for day in source_session_dates),
            "target_session_date": target_session_date.isoformat(),
            "through_minute": through_minute,
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()
    except (AttributeError, TypeError, ValueError):
        raise IntradayVolumeProfileError from None


def _valid_text(value: str) -> bool:
    return type(value) is str and 0 < len(value) <= 128


def _session_minutes(bounds: tuple[dt.datetime, dt.datetime]) -> int:
    return int((bounds[1] - bounds[0]) / dt.timedelta(minutes=1))


def _prior_profile_sessions(target: dt.date, through_minute: int) -> tuple[dt.date, ...]:
    result: list[dt.date] = []
    for offset in range(1, 367):
        candidate = target - dt.timedelta(days=offset)
        bounds = regular_session_bounds(candidate)
        if bounds is not None and _session_minutes(bounds) >= through_minute:
            result.append(candidate)
            if len(result) == INTRADAY_VOLUME_PROFILE_SESSIONS:
                break
    if len(result) != INTRADAY_VOLUME_PROFILE_SESSIONS:
        raise IntradayVolumeProfileError
    return tuple(reversed(result))


__all__ = (
    "INTRADAY_VOLUME_PROFILE_SESSIONS",
    "IntradayVolumeProfileError",
    "IntradayVolumeProfileEvidence",
    "create_intraday_volume_profile_evidence",
    "validate_intraday_volume_profile",
)
