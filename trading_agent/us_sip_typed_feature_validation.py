from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Final

from trading_agent.canonical_dataset_event_reader import replay_canonical_dataset_events
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplayError
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.intraday_feature_kernel import (
    FeatureSnapshotStatus,
    IntradayFeatureSnapshot,
)
from trading_agent.research_input_identity import (
    ResearchInputIdentity,
    ResearchInputIdentityError,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds
from trading_agent.us_intraday_volume_profile_models import (
    IntradayVolumeProfileError,
    validate_intraday_volume_profile,
)

_SCOPE: Final = "us_equities.day_trading.runtime_features"
_SOURCE_ID: Final = "alpaca/sip"
_ONE_MINUTE: Final = dt.timedelta(minutes=1)


class UsSipTypedFeatureValidationError(ValueError):
    pass


def validate_us_sip_typed_feature_input(
    snapshot: IntradayFeatureSnapshot,
    dataset_directory: Path,
    minimum_rvol_bps: int,
) -> CanonicalEventEnvelope:
    try:
        _validate_snapshot(snapshot, minimum_rvol_bps)
        replay, events = replay_canonical_dataset_events(dataset_directory)
        identity = ResearchInputIdentity.from_verified_replay(_SCOPE, replay)
        if identity != snapshot.identity or replay.event_count != snapshot.bar_count:
            raise UsSipTypedFeatureValidationError
        return _trigger_event(snapshot, events)
    except (
        AttributeError,
        CanonicalDatasetReplayError,
        IntradayVolumeProfileError,
        OSError,
        ResearchInputIdentityError,
        TypeError,
        ValueError,
    ):
        raise UsSipTypedFeatureValidationError from None


def _validate_snapshot(snapshot: IntradayFeatureSnapshot, minimum_rvol_bps: int) -> None:
    if (
        type(snapshot) is not IntradayFeatureSnapshot
        or snapshot.status is not FeatureSnapshotStatus.READY
        or type(snapshot.identity) is not ResearchInputIdentity
        or snapshot.identity.scope != _SCOPE
        or type(minimum_rvol_bps) is not int
        or not 1 <= minimum_rvol_bps <= 100_000
        or type(snapshot.instrument_id) is not str
        or not snapshot.instrument_id
        or not _aware(snapshot.observed_at)
        or not _aware(snapshot.source_start_at)
        or not _aware(snapshot.source_end_at)
        or _time(snapshot.source_end_at) <= _time(snapshot.source_start_at)
        or type(snapshot.bar_count) is not int
        or snapshot.bar_count <= 0
        or type(snapshot.indicator_semantic_version) is not str
        or not snapshot.indicator_semantic_version
        or type(snapshot.breakout_close_above_prior_high) is not bool
    ):
        raise UsSipTypedFeatureValidationError
    indicators = (
        snapshot.vwap,
        snapshot.atr14,
        snapshot.rsi14,
        snapshot.macd_line,
        snapshot.macd_signal,
        snapshot.macd_histogram,
        snapshot.rvol,
    )
    if any(type(value) is not Decimal or not value.is_finite() for value in indicators):
        raise UsSipTypedFeatureValidationError
    validate_intraday_volume_profile(snapshot.volume_profile)
    bounds = regular_session_bounds(snapshot.volume_profile.target_session_date)
    if (
        bounds is None
        or snapshot.volume_profile.instrument_id != snapshot.instrument_id
        or snapshot.volume_profile.target_session_date != snapshot.observed_at.astimezone(NEW_YORK).date()
        or snapshot.volume_profile.through_minute != snapshot.bar_count
        or snapshot.source_start_at != bounds[0]
        or snapshot.source_end_at != bounds[0] + _ONE_MINUTE * snapshot.volume_profile.through_minute
    ):
        raise UsSipTypedFeatureValidationError


def _trigger_event(
    snapshot: IntradayFeatureSnapshot,
    events: tuple[CanonicalEventEnvelope, ...],
) -> CanonicalEventEnvelope:
    entity = CanonicalEntityRef(
        entity_type=CanonicalEntityType.INSTRUMENT,
        entity_id=snapshot.instrument_id,
    )
    if not events or any(not _valid_event(item, snapshot, entity) for item in events):
        raise UsSipTypedFeatureValidationError
    ordered = tuple(sorted(events, key=_event_time))
    event_times = tuple(_event_time(item) for item in ordered)
    if (
        len(event_times) != len(set(event_times))
        or event_times[0] != snapshot.source_start_at
        or event_times[-1] + _ONE_MINUTE != snapshot.source_end_at
        or any(right - left != _ONE_MINUTE for left, right in pairwise(event_times))
    ):
        raise UsSipTypedFeatureValidationError
    return ordered[-1]


def _valid_event(
    event: CanonicalEventEnvelope,
    snapshot: IntradayFeatureSnapshot,
    entity: CanonicalEntityRef,
) -> bool:
    return (
        type(event) is CanonicalEventEnvelope
        and event.source_id.canonical_id == _SOURCE_ID
        and event.event_type == "minute_bar"
        and event.operation is CanonicalEventOperation.ORIGINAL
        and event.correction_of is None
        and event.entity_refs == (entity,)
        and _aware(event.event_time)
        and event.received_at <= snapshot.observed_at
        and event.normalized_at <= snapshot.observed_at
    )


def _event_time(event: CanonicalEventEnvelope) -> dt.datetime:
    return _time(event.event_time)


def _time(value: dt.datetime | None) -> dt.datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise UsSipTypedFeatureValidationError
    return value


def _aware(value: dt.datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


__all__ = ("validate_us_sip_typed_feature_input",)
