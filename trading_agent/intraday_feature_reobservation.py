from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from typing import override

from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus, IntradayFeatureSnapshot
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_intraday_volume_profile_models import (
    IntradayVolumeProfileError,
    validate_intraday_volume_profile,
)
from trading_agent.us_runtime_policy_scope import completed_regular_minute


class IntradayFeatureReobservationError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday feature reobservation is blocked"


def reobserve_ready_intraday_feature(
    snapshot: IntradayFeatureSnapshot,
    observed_at: dt.datetime,
) -> IntradayFeatureSnapshot:
    try:
        _validate_snapshot(snapshot)
        if (
            not _aware(observed_at)
            or observed_at < snapshot.observed_at
            or observed_at.astimezone(NEW_YORK).date() != snapshot.observed_at.astimezone(NEW_YORK).date()
            or completed_regular_minute(observed_at) != completed_regular_minute(snapshot.observed_at)
        ):
            raise IntradayFeatureReobservationError
        return dataclasses.replace(snapshot, observed_at=observed_at)
    except (AttributeError, IntradayVolumeProfileError, TypeError, ValueError):
        raise IntradayFeatureReobservationError from None


def _validate_snapshot(snapshot: IntradayFeatureSnapshot) -> None:
    values = (
        snapshot.close,
        snapshot.vwap,
        snapshot.atr14,
        snapshot.rsi14,
        snapshot.macd_line,
        snapshot.macd_signal,
        snapshot.macd_histogram,
        snapshot.rvol,
    )
    if (
        type(snapshot) is not IntradayFeatureSnapshot
        or snapshot.status is not FeatureSnapshotStatus.READY
        or type(snapshot.identity) is not ResearchInputIdentity
        or not snapshot.instrument_id
        or not _aware(snapshot.observed_at)
        or not _aware(snapshot.source_start_at)
        or not _aware(snapshot.source_end_at)
        or _time(snapshot.source_start_at) >= _time(snapshot.source_end_at)
        or _time(snapshot.source_end_at) >= snapshot.observed_at
        or snapshot.bar_count < 35
        or not snapshot.indicator_semantic_version
        or any(type(value) is not Decimal or not value.is_finite() for value in values)
        or type(snapshot.breakout_close_above_prior_high) is not bool
    ):
        raise IntradayFeatureReobservationError
    validate_intraday_volume_profile(snapshot.volume_profile)
    if (
        snapshot.volume_profile.instrument_id != snapshot.instrument_id
        or snapshot.volume_profile.target_session_date != snapshot.observed_at.astimezone(NEW_YORK).date()
    ):
        raise IntradayFeatureReobservationError


def _time(value: dt.datetime | None) -> dt.datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise IntradayFeatureReobservationError
    return value


def _aware(value: dt.datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "IntradayFeatureReobservationError",
    "reobserve_ready_intraday_feature",
)
