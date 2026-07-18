from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import CompletedMinuteBar
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_intraday_volume_profile import (
    HistoricalVolumeSession,
    build_intraday_volume_profile,
)
from trading_agent.us_intraday_volume_profile_models import (
    INTRADAY_VOLUME_PROFILE_SESSIONS,
    IntradayVolumeProfileEvidence,
    create_intraday_volume_profile_evidence,
)


def volume_profile(
    instrument_id: str,
    target_session_date: dt.date,
    *,
    through_minute: int = 35,
    expected_cumulative_volume: int = 4_000,
    identity_fill: str = "d",
) -> IntradayVolumeProfileEvidence:
    source_dates = _prior_sessions(target_session_date)
    volumes = (expected_cumulative_volume,) * INTRADAY_VOLUME_PROFILE_SESSIONS
    return create_intraday_volume_profile_evidence(
        tuple(_identity_for_date(day, identity_fill) for day in source_dates),
        instrument_id,
        target_session_date,
        through_minute,
        source_dates,
        volumes,
    )


def historical_volume_profile(
    instrument_id: str,
    target_session_date: dt.date,
    *,
    through_minute: int = 35,
    identity_fill: str = "7",
) -> IntradayVolumeProfileEvidence:
    sessions = tuple(
        _historical_session(day, through_minute, identity_fill) for day in _prior_sessions(target_session_date)
    )
    return build_intraday_volume_profile(
        instrument_id,
        target_session_date,
        through_minute=through_minute,
        sessions=sessions,
    )


def _identity_for_date(session_date: dt.date, fill: str) -> ResearchInputIdentity:
    digest = hashlib.sha256(f"{session_date.isoformat()}:{fill}".encode()).hexdigest()
    replay = CanonicalDatasetReplay(
        dataset_id=f"historical-volume-{session_date.isoformat()}-{fill}",
        event_count=390,
        canonical_event_content_sha256=digest,
        parquet_sha256="e" * 64,
        raw_manifest_id=f"raw-historical-volume-{session_date.isoformat()}-{fill}",
        raw_manifest_content_sha256="f" * 64,
    )
    return ResearchInputIdentity.from_verified_replay(
        "us_equities.intraday_volume_profile",
        replay,
    )


def _historical_session(
    session_date: dt.date,
    through_minute: int,
    identity_fill: str,
) -> HistoricalVolumeSession:
    bounds = regular_session_bounds(session_date)
    assert bounds is not None
    opened, closed = bounds
    count = int((closed - opened) / dt.timedelta(minutes=1))
    volumes = [100] * count
    volumes[through_minute - 1] = 600
    bars = tuple(_historical_bar(opened + dt.timedelta(minutes=index), volume) for index, volume in enumerate(volumes))
    return HistoricalVolumeSession(
        session_date,
        _identity_for_date(session_date, identity_fill),
        bars,
    )


def _historical_bar(start_at: dt.datetime, volume: int) -> CompletedMinuteBar:
    return CompletedMinuteBar(
        start_at,
        start_at + dt.timedelta(minutes=1),
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100"),
        volume,
    )


def _prior_sessions(target: dt.date) -> tuple[dt.date, ...]:
    result: list[dt.date] = []
    candidate = target - dt.timedelta(days=1)
    while len(result) < INTRADAY_VOLUME_PROFILE_SESSIONS:
        if regular_session_bounds(candidate) is not None:
            result.append(candidate)
        candidate -= dt.timedelta(days=1)
    return tuple(reversed(result))


__all__ = ("historical_volume_profile", "volume_profile")
