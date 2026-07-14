from __future__ import annotations

import dataclasses
import datetime as dt
import math
import statistics

from trading_agent.kis_live import NEW_YORK, regular_session_bounds
from trading_agent.orb_execution import empty_outcome, setup_outcome, simulate_entry
from trading_agent.orb_models import (
    OrbBar,
    OrbOutcome,
    OrbOutcomeStatus,
    OrbSelection,
    OrbTestConfig,
)


def measure_orb_day(
    selections: tuple[OrbSelection, ...],
    bars: tuple[OrbBar, ...],
    config: OrbTestConfig,
) -> OrbOutcome:
    first = min(selections, key=lambda row: row.observed_at)
    ordered = tuple(sorted(bars, key=lambda row: row.timestamp))
    if not _complete_session(first.observed_at, ordered):
        return empty_outcome(first, config, OrbOutcomeStatus.CENSORED, False)
    opening = ordered[: config.range_minutes]
    range_high = max(bar.high for bar in opening)
    range_low = min(bar.low for bar in opening)
    opening_volume = statistics.fmean(bar.volume for bar in opening)
    signal = _find_signal(
        tuple(sorted(selections, key=lambda row: row.observed_at)),
        ordered,
        range_high,
        opening_volume,
        config,
    )
    if signal is None:
        return empty_outcome(first, config, OrbOutcomeStatus.NO_SIGNAL, True)
    selection, _ = signal
    entry = range_high * (1.0 + config.breakout_buffer_bps / 10_000.0)
    risk = (entry - range_low) * config.stop_multiple
    stop = entry - risk
    target = entry + risk * config.target_r
    if (
        risk <= 0.0
        or risk / entry > config.max_risk_pct
        or not math.isfinite(selection.spread_bps)
        or selection.spread_bps > config.max_spread_bps
    ):
        return setup_outcome(
            selection,
            config,
            OrbOutcomeStatus.RISK_REJECTED,
            entry,
            stop,
            target,
        )
    entry_start = _next_minute(selection.observed_at)
    path = tuple(bar for bar in ordered if bar.timestamp >= entry_start)
    return simulate_entry(selection, path, config, entry, stop, target)


def _find_signal(
    selections: tuple[OrbSelection, ...],
    bars: tuple[OrbBar, ...],
    range_high: float,
    opening_volume: float,
    config: OrbTestConfig,
) -> tuple[OrbSelection, OrbBar] | None:
    range_end = bars[0].timestamp + dt.timedelta(minutes=config.range_minutes)
    for index, selection in enumerate(selections):
        next_at = (
            None
            if index + 1 == len(selections)
            else selections[index + 1].observed_at
        )
        batch_times = tuple(
            sorted(
                {
                    bar.first_observed_at
                    for bar in bars
                    if bar.first_observed_at >= selection.observed_at
                    and (next_at is None or bar.first_observed_at < next_at)
                }
            )
        )
        for available_at in batch_times:
            known = tuple(
                bar
                for bar in bars
                if bar.first_observed_at == available_at
                and bar.timestamp >= range_end
                and bar.timestamp + dt.timedelta(minutes=1) <= available_at
            )
            if not known:
                continue
            latest = known[-1]
            if (
                latest.close > range_high
                and latest.volume >= opening_volume * config.volume_multiplier
            ):
                return dataclasses.replace(
                    selection,
                    observed_at=max(selection.observed_at, available_at),
                ), latest
    return None


def _complete_session(observed_at: dt.datetime, bars: tuple[OrbBar, ...]) -> bool:
    bounds = regular_session_bounds(observed_at.astimezone(NEW_YORK).date())
    if bounds is None:
        return False
    expected = int((bounds[1] - bounds[0]) / dt.timedelta(minutes=1))
    return len(bars) == expected and all(
        bar.timestamp == bounds[0] + dt.timedelta(minutes=index)
        for index, bar in enumerate(bars)
    )


def _next_minute(value: dt.datetime) -> dt.datetime:
    return value.astimezone(NEW_YORK).replace(second=0, microsecond=0) + dt.timedelta(
        minutes=1
    )
