from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import sqlite3
from pathlib import Path
from typing import Final

from trading_agent.kis_live import NEW_YORK
from trading_agent.orb_models import (
    OrbBar,
    OrbOutcome,
    OrbOutcomeStatus,
    OrbSelection,
    OrbTestConfig,
)
from trading_agent.orb_outcomes import measure_orb_day

TRADE_STATUSES: Final = frozenset(
    {
        OrbOutcomeStatus.STOPPED,
        OrbOutcomeStatus.TARGET,
        OrbOutcomeStatus.TIME_EXIT,
    }
)
RANGE_MINUTES: Final = (1, 5, 15)
VOLUME_MULTIPLIERS: Final = (1.0, 1.5, 2.0)
STOP_MULTIPLES: Final = (0.75, 1.0, 1.25)
TARGET_R_MULTIPLES: Final = (1.0, 2.0, 3.0)


def default_orb_grid() -> tuple[OrbTestConfig, ...]:
    return tuple(
        OrbTestConfig(range_minutes, 5.0, volume, stop, target)
        for range_minutes in RANGE_MINUTES
        for volume in VOLUME_MULTIPLIERS
        for stop in STOP_MULTIPLES
        for target in TARGET_R_MULTIPLES
    )


def analyze_orb_grid(
    snapshot_path: Path,
    database_path: Path,
    configs: tuple[OrbTestConfig, ...],
    max_positions: int = 10,
) -> tuple[OrbOutcome, ...]:
    selections = _read_selections(snapshot_path)
    bars = _read_bars(database_path)
    selection_groups: dict[tuple[dt.date, str, str], list[OrbSelection]] = {}
    for selection in selections:
        key = (
            selection.observed_at.astimezone(NEW_YORK).date(),
            selection.exchange,
            selection.symbol,
        )
        selection_groups.setdefault(key, []).append(selection)
    bar_groups: dict[tuple[dt.date, str, str], list[OrbBar]] = {}
    for exchange, symbol, bar in bars:
        key = (bar.timestamp.astimezone(NEW_YORK).date(), exchange, symbol)
        bar_groups.setdefault(key, []).append(bar)
    measured: list[OrbOutcome] = []
    for config in configs:
        config_outcomes = tuple(
            measure_orb_day(
                tuple(group),
                tuple(bar_groups.get(key, ())),
                config,
            )
            for key, group in selection_groups.items()
        )
        measured.extend(apply_portfolio_limit(config_outcomes, max_positions))
    return tuple(
        sorted(
            measured,
            key=lambda row: (
                row.config.range_minutes,
                row.config.volume_multiplier,
                row.config.stop_multiple,
                row.config.target_r,
                row.observed_at,
                row.exchange,
                row.symbol,
            ),
        )
    )


def apply_portfolio_limit(
    outcomes: tuple[OrbOutcome, ...],
    max_positions: int,
) -> tuple[OrbOutcome, ...]:
    eligible = tuple(
        row
        for row in outcomes
        if row.status in TRADE_STATUSES
        and row.entry_at is not None
        and row.exit_at is not None
    )
    ordered = sorted(
        eligible,
        key=lambda row: (
            row.entry_at,
            -row.change_pct,
            -row.dollar_volume,
            row.exchange,
            row.symbol,
        ),
    )
    accepted: set[tuple[str, str, dt.datetime]] = set()
    active_exits: list[dt.datetime] = []
    for row in ordered:
        if row.entry_at is None or row.exit_at is None:
            continue
        active_exits = [value for value in active_exits if value > row.entry_at]
        if len(active_exits) >= max_positions:
            continue
        accepted.add((row.exchange, row.symbol, row.entry_at))
        active_exits.append(row.exit_at)
    return tuple(
        dataclasses.replace(
            row,
            portfolio_selected=(
                row.entry_at is not None
                and (row.exchange, row.symbol, row.entry_at) in accepted
            ),
        )
        for row in outcomes
    )


def _read_selections(path: Path) -> tuple[OrbSelection, ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    return tuple(
        OrbSelection(
            dt.datetime.fromisoformat(row["observed_at"]),
            row["exchange"],
            row["symbol"],
            float(row["change_pct"]),
            float(row["dollar_volume"]),
            float(row["spread_bps"]),
        )
        for row in rows
        if row.get("selection_input") == "True"
    )


def _read_bars(
    path: Path,
) -> tuple[tuple[str, str, OrbBar], ...]:
    if not path.is_file():
        return ()
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'candidate_minute_bars'"
        ).fetchone()
        if table is None:
            return ()
        rows: list[
            tuple[str, str, str, str, float, float, float, float, int]
        ] = connection.execute(
            "SELECT exchange, symbol, exchange_timestamp, first_observed_at, "
            "open, high, low, close, volume FROM candidate_minute_bars "
            "ORDER BY exchange, symbol, exchange_timestamp"
        ).fetchall()
    return tuple(
        (
            row[0],
            row[1],
            OrbBar(
                dt.datetime.fromisoformat(row[2]),
                dt.datetime.fromisoformat(row[3]),
                float(row[4]),
                float(row[5]),
                float(row[6]),
                float(row[7]),
                int(row[8]),
            ),
        )
        for row in rows
    )
