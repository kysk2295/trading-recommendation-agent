from __future__ import annotations

import datetime as dt

from trading_agent.orb_models import (
    OrbBar,
    OrbOutcome,
    OrbOutcomeStatus,
    OrbSelection,
    OrbTestConfig,
)


def simulate_entry(
    selection: OrbSelection,
    bars: tuple[OrbBar, ...],
    config: OrbTestConfig,
    entry: float,
    stop: float,
    target: float,
) -> OrbOutcome:
    for bar in bars:
        if bar.high < entry:
            if bar.low <= stop:
                return setup_outcome(
                    selection,
                    config,
                    OrbOutcomeStatus.INVALIDATED,
                    entry,
                    stop,
                    target,
                )
            continue
        return _after_entry(selection, bars, bar, config, entry, stop, target)
    return setup_outcome(
        selection,
        config,
        OrbOutcomeStatus.NO_ENTRY,
        entry,
        stop,
        target,
    )


def _after_entry(
    selection: OrbSelection,
    bars: tuple[OrbBar, ...],
    entry_bar: OrbBar,
    config: OrbTestConfig,
    entry: float,
    stop: float,
    target: float,
) -> OrbOutcome:
    path = tuple(bar for bar in bars if bar.timestamp >= entry_bar.timestamp)
    for bar in path:
        if bar.low <= stop:
            return trade_outcome(
                selection,
                config,
                OrbOutcomeStatus.STOPPED,
                entry_bar.timestamp,
                bar.timestamp,
                entry,
                stop,
                target,
                stop,
            )
        if bar.high >= target:
            return trade_outcome(
                selection,
                config,
                OrbOutcomeStatus.TARGET,
                entry_bar.timestamp,
                bar.timestamp,
                entry,
                stop,
                target,
                target,
            )
    last = path[-1]
    return trade_outcome(
        selection,
        config,
        OrbOutcomeStatus.TIME_EXIT,
        entry_bar.timestamp,
        last.timestamp + dt.timedelta(minutes=1),
        entry,
        stop,
        target,
        last.close,
    )


def empty_outcome(
    selection: OrbSelection,
    config: OrbTestConfig,
    status: OrbOutcomeStatus,
    complete: bool,
) -> OrbOutcome:
    return OrbOutcome(
        config,
        selection.observed_at,
        selection.exchange,
        selection.symbol,
        selection.change_pct,
        selection.dollar_volume,
        selection.spread_bps,
        complete,
        status,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def setup_outcome(
    selection: OrbSelection,
    config: OrbTestConfig,
    status: OrbOutcomeStatus,
    entry: float,
    stop: float,
    target: float,
) -> OrbOutcome:
    base = empty_outcome(selection, config, status, True)
    return OrbOutcome(
        base.config,
        base.observed_at,
        base.exchange,
        base.symbol,
        base.change_pct,
        base.dollar_volume,
        base.spread_bps,
        base.complete,
        base.status,
        selection.observed_at,
        None,
        None,
        entry,
        stop,
        target,
        None,
        None,
    )


def trade_outcome(
    selection: OrbSelection,
    config: OrbTestConfig,
    status: OrbOutcomeStatus,
    entry_at: dt.datetime,
    exit_at: dt.datetime,
    entry: float,
    stop: float,
    target: float,
    exit_price: float,
) -> OrbOutcome:
    setup = setup_outcome(selection, config, status, entry, stop, target)
    return OrbOutcome(
        setup.config,
        setup.observed_at,
        setup.exchange,
        setup.symbol,
        setup.change_pct,
        setup.dollar_volume,
        setup.spread_bps,
        setup.complete,
        setup.status,
        setup.signal_at,
        entry_at,
        exit_at,
        entry,
        stop,
        target,
        exit_price,
        exit_price / entry - 1.0,
    )
