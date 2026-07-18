from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from trading_agent.intraday_feature_kernel import CompletedMinuteBar
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_intraday_volume_profile_models import (
    INTRADAY_VOLUME_PROFILE_SESSIONS,
    IntradayVolumeProfileError,
    IntradayVolumeProfileEvidence,
    create_intraday_volume_profile_evidence,
)


@dataclass(frozen=True, slots=True)
class HistoricalVolumeSession:
    session_date: dt.date
    identity: ResearchInputIdentity
    bars: tuple[CompletedMinuteBar, ...]


def build_intraday_volume_profile(
    instrument_id: str,
    target_session_date: dt.date,
    *,
    through_minute: int,
    sessions: tuple[HistoricalVolumeSession, ...],
) -> IntradayVolumeProfileEvidence:
    target_bounds = regular_session_bounds(target_session_date) if type(target_session_date) is dt.date else None
    if (
        type(instrument_id) is not str
        or not instrument_id
        or target_bounds is None
        or type(through_minute) is not int
        or through_minute <= 0
        or through_minute > _session_minutes(target_bounds)
        or type(sessions) is not tuple
    ):
        raise IntradayVolumeProfileError

    by_date: dict[dt.date, HistoricalVolumeSession] = {}
    for session in sessions:
        _validate_completed_session(session, target_session_date)
        if session.session_date in by_date:
            raise IntradayVolumeProfileError
        by_date[session.session_date] = session

    eligible = tuple(
        session
        for session in sorted(by_date.values(), key=lambda item: item.session_date)
        if len(session.bars) >= through_minute
    )
    selected = eligible[-INTRADAY_VOLUME_PROFILE_SESSIONS:]
    if len(selected) != INTRADAY_VOLUME_PROFILE_SESSIONS:
        raise IntradayVolumeProfileError
    cumulative = tuple(sum(bar.volume for bar in session.bars[:through_minute]) for session in selected)
    return create_intraday_volume_profile_evidence(
        tuple(session.identity for session in selected),
        instrument_id,
        target_session_date,
        through_minute,
        tuple(session.session_date for session in selected),
        cumulative,
    )


def _validate_completed_session(
    session: HistoricalVolumeSession,
    target_session_date: dt.date,
) -> None:
    bounds = (
        regular_session_bounds(session.session_date)
        if type(session) is HistoricalVolumeSession and type(session.session_date) is dt.date
        else None
    )
    if (
        bounds is None
        or type(session.identity) is not ResearchInputIdentity
        or session.session_date >= target_session_date
        or type(session.bars) is not tuple
        or len(session.bars) != _session_minutes(bounds)
    ):
        raise IntradayVolumeProfileError
    opened, _ = bounds
    for index, bar in enumerate(session.bars):
        expected_start = opened + dt.timedelta(minutes=index)
        if (
            type(bar) is not CompletedMinuteBar
            or bar.start_at != expected_start
            or bar.end_at != expected_start + dt.timedelta(minutes=1)
            or type(bar.volume) is not int
            or bar.volume < 0
            or not _valid_prices(bar)
        ):
            raise IntradayVolumeProfileError


def _valid_prices(bar: CompletedMinuteBar) -> bool:
    prices = (bar.open, bar.high, bar.low, bar.close)
    return (
        all(type(price) is Decimal and price.is_finite() and price > 0 for price in prices)
        and bar.low <= min(bar.open, bar.close)
        and max(bar.open, bar.close) <= bar.high
    )


def _session_minutes(bounds: tuple[dt.datetime, dt.datetime]) -> int:
    return int((bounds[1] - bounds[0]) / dt.timedelta(minutes=1))


__all__ = (
    "HistoricalVolumeSession",
    "IntradayVolumeProfileError",
    "build_intraday_volume_profile",
)
