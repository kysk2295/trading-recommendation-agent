from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import CompletedMinuteBar
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_intraday_volume_profile import (
    HistoricalVolumeSession,
    IntradayVolumeProfileError,
    build_intraday_volume_profile,
)
from trading_agent.us_intraday_volume_profile_models import (
    INTRADAY_VOLUME_PROFILE_SESSIONS,
    validate_intraday_volume_profile,
)

_TARGET = dt.date(2026, 7, 17)
_INSTRUMENT_ID = "us-eq-fixture-aapl"


def test_profile_uses_latest_twenty_completed_prior_sessions_and_median() -> None:
    sessions = tuple(
        _session(session_date, volume_per_minute=index + 1) for index, session_date in enumerate(_prior_sessions(22))
    )

    profile = build_intraday_volume_profile(
        _identity(),
        _INSTRUMENT_ID,
        _TARGET,
        through_minute=35,
        sessions=sessions,
    )

    expected_dates = tuple(item.session_date for item in sessions[-20:])
    expected_cumulative = tuple(35 * (index + 3) for index in range(20))
    assert profile.source_session_dates == expected_dates
    assert profile.session_cumulative_volumes == expected_cumulative
    assert profile.expected_cumulative_volume == Decimal("437.5")
    assert profile.through_minute == 35
    assert profile.semantic_version == "us_intraday_cumulative_volume_median_20_v1"
    assert len(profile.evidence_sha256) == 64
    validate_intraday_volume_profile(profile)


def test_profile_identity_changes_with_historical_replay_identity() -> None:
    sessions = tuple(_session(day, volume_per_minute=10) for day in _prior_sessions(20))
    first = build_intraday_volume_profile(_identity("a"), _INSTRUMENT_ID, _TARGET, through_minute=35, sessions=sessions)
    second = build_intraday_volume_profile(
        _identity("d"), _INSTRUMENT_ID, _TARGET, through_minute=35, sessions=sessions
    )

    assert first.expected_cumulative_volume == second.expected_cumulative_volume
    assert first.evidence_sha256 != second.evidence_sha256


@pytest.mark.parametrize("offset", (0, 3))
def test_current_or_future_session_is_noncausal(offset: int) -> None:
    sessions = tuple(_session(day, volume_per_minute=10) for day in _prior_sessions(19))
    sessions += (_session(_TARGET + dt.timedelta(days=offset), volume_per_minute=10),)

    with pytest.raises(IntradayVolumeProfileError, match="invalid"):
        build_intraday_volume_profile(_identity(), _INSTRUMENT_ID, _TARGET, through_minute=35, sessions=sessions)


def test_incomplete_or_gapped_historical_session_is_rejected() -> None:
    sessions = list(_session(day, volume_per_minute=10) for day in _prior_sessions(20))
    broken = sessions[-1]
    sessions[-1] = replace(broken, bars=broken.bars[:10] + broken.bars[11:])

    with pytest.raises(IntradayVolumeProfileError, match="invalid"):
        build_intraday_volume_profile(_identity(), _INSTRUMENT_ID, _TARGET, through_minute=35, sessions=tuple(sessions))


def test_fewer_than_twenty_eligible_sessions_is_rejected() -> None:
    sessions = tuple(_session(day, volume_per_minute=10) for day in _prior_sessions(19))

    with pytest.raises(IntradayVolumeProfileError, match="invalid"):
        build_intraday_volume_profile(_identity(), _INSTRUMENT_ID, _TARGET, through_minute=35, sessions=sessions)


def test_stale_profile_with_missing_latest_session_is_rejected() -> None:
    sessions = tuple(_session(day, volume_per_minute=10) for day in _prior_sessions(21)[:-1])

    with pytest.raises(IntradayVolumeProfileError, match="invalid"):
        build_intraday_volume_profile(_identity(), _INSTRUMENT_ID, _TARGET, through_minute=35, sessions=sessions)


def test_tampered_profile_fails_self_validation() -> None:
    sessions = tuple(_session(day, volume_per_minute=10) for day in _prior_sessions(20))
    profile = build_intraday_volume_profile(_identity(), _INSTRUMENT_ID, _TARGET, through_minute=35, sessions=sessions)

    with pytest.raises(IntradayVolumeProfileError, match="invalid"):
        validate_intraday_volume_profile(replace(profile, expected_cumulative_volume=Decimal("999")))


def _identity(fill: str = "a") -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id=f"historical-minute-bars-{fill}",
        event_count=INTRADAY_VOLUME_PROFILE_SESSIONS * 390,
        canonical_event_content_sha256=fill * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id=f"raw-historical-minute-bars-{fill}",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay("us_equities.intraday_volume_profile", replay)


def _session(session_date: dt.date, *, volume_per_minute: int) -> HistoricalVolumeSession:
    bounds = regular_session_bounds(session_date)
    assert bounds is not None
    opened, closed = bounds
    count = int((closed - opened) / dt.timedelta(minutes=1))
    bars = tuple(_bar(opened + dt.timedelta(minutes=index), volume_per_minute) for index in range(count))
    return HistoricalVolumeSession(session_date, bars)


def _bar(start_at: dt.datetime, volume: int) -> CompletedMinuteBar:
    assert start_at.tzinfo is NEW_YORK
    return CompletedMinuteBar(
        start_at,
        start_at + dt.timedelta(minutes=1),
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100"),
        volume,
    )


def _prior_sessions(count: int) -> tuple[dt.date, ...]:
    result: list[dt.date] = []
    candidate = _TARGET - dt.timedelta(days=1)
    while len(result) < count:
        if regular_session_bounds(candidate) is not None:
            result.append(candidate)
        candidate -= dt.timedelta(days=1)
    return tuple(reversed(result))
