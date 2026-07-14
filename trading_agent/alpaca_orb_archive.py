from __future__ import annotations

import csv
import datetime as dt
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from trading_agent.orb_analysis import apply_portfolio_limit
from trading_agent.orb_models import OrbBar, OrbOutcome, OrbSelection, OrbTestConfig
from trading_agent.orb_outcomes import measure_orb_day
from trading_agent.session_date_range import SessionDateRange

NEW_YORK = ZoneInfo("America/New_York")


class _StagedMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str
    session_date: dt.date
    scanner_cutoff: dt.time
    selected_symbol_count: int
    selected_symbols: tuple[str, ...]
    candidate_bar_count: int


class _ArchiveMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str
    session_date: dt.date
    bar_count: int
    symbol_count: int
    window_start: dt.time
    window_end: dt.time


@dataclass(frozen=True, slots=True)
class AlpacaOrbArchiveConfig:
    assumed_spread_bps: float = 20.0
    max_positions: int = 10


DEFAULT_ARCHIVE_CONFIG: Final = AlpacaOrbArchiveConfig()


@dataclass(frozen=True, slots=True)
class AlpacaOrbArchiveError(RuntimeError):
    path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def analyze_alpaca_orb_grid(
    root: Path,
    configs: tuple[OrbTestConfig, ...],
    archive_config: AlpacaOrbArchiveConfig = DEFAULT_ARCHIVE_CONFIG,
    *,
    session_range: SessionDateRange | None = None,
) -> tuple[OrbOutcome, ...]:
    groups = _load_groups(root, archive_config.assumed_spread_bps, session_range)
    outcomes: list[OrbOutcome] = []
    for config in configs:
        measured = tuple(measure_orb_day(selections, bars, config) for selections, bars in groups)
        outcomes.extend(apply_portfolio_limit(measured, archive_config.max_positions))
    return tuple(
        sorted(
            outcomes,
            key=lambda row: (
                row.config.range_minutes,
                row.config.volume_multiplier,
                row.config.stop_multiple,
                row.config.target_r,
                row.observed_at,
                row.symbol,
            ),
        )
    )


def _load_groups(
    root: Path,
    assumed_spread_bps: float,
    session_range: SessionDateRange | None,
) -> tuple[tuple[tuple[OrbSelection, ...], tuple[OrbBar, ...]], ...]:
    groups: list[tuple[tuple[OrbSelection, ...], tuple[OrbBar, ...]]] = []
    metadata_paths = (
        path
        for path in sorted((root / "staged_sessions").glob("*/*/*/session_*.metadata.json"))
        if session_range is None or session_range.contains(_date_from_scoped_path(path))
    )
    for path in metadata_paths:
        metadata = _StagedMetadata.model_validate_json(path.read_text(encoding="utf-8"))
        if metadata.status != "complete":
            raise AlpacaOrbArchiveError(path, "staged session is not complete")
        if metadata.scanner_cutoff != dt.time(9, 30):
            raise AlpacaOrbArchiveError(path, "scanner cutoff must be 09:30 America/New_York")
        session_id = path.name.removeprefix("session_").removesuffix(".metadata.json")
        decisions_path = (
            root
            / "scanner_decisions"
            / metadata.session_date.strftime("%Y/%m/%d")
            / f"scanner_decisions_{session_id}.csv.gz"
        )
        selections = _read_selections(decisions_path, metadata, assumed_spread_bps)
        bars_by_symbol = _read_candidate_bars(root, metadata)
        groups.extend(
            (selection_group, bars_by_symbol.get(symbol, ()))
            for symbol, selection_group in _group_selections(selections)
        )
    return tuple(groups)


def _read_selections(
    path: Path,
    metadata: _StagedMetadata,
    assumed_spread_bps: float,
) -> tuple[OrbSelection, ...]:
    observed_at = dt.datetime.combine(metadata.session_date, metadata.scanner_cutoff, tzinfo=NEW_YORK)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    return tuple(
        OrbSelection(
            observed_at,
            "SIP",
            row["symbol"],
            float(row["change_pct"]),
            float(row["dollar_volume"]),
            assumed_spread_bps,
        )
        for row in rows
        if row["selected"] == "True"
    )


def _read_candidate_bars(
    root: Path,
    metadata: _StagedMetadata,
) -> dict[str, tuple[OrbBar, ...]]:
    date_path = metadata.session_date.strftime("%Y/%m/%d")
    archive_root = root / "candidate_minutes" / date_path
    matches: list[Path] = []
    for path in archive_root.glob("archive_*/session.metadata.json"):
        archive = _ArchiveMetadata.model_validate_json(path.read_text(encoding="utf-8"))
        if (
            archive.status == "complete"
            and archive.session_date == metadata.session_date
            and archive.window_start == metadata.scanner_cutoff
            and archive.window_end == dt.time(20)
            and archive.symbol_count == metadata.selected_symbol_count
            and archive.bar_count == metadata.candidate_bar_count
        ):
            matches.append(path.parent)
    if len(matches) != 1:
        raise AlpacaOrbArchiveError(archive_root, f"expected one matching archive, found {len(matches)}")
    grouped: dict[str, list[OrbBar]] = {}
    for path in sorted(matches[0].glob("batch_*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                timestamp = dt.datetime.fromisoformat(row["timestamp"]).astimezone(NEW_YORK)
                if timestamp.time() < dt.time(9, 30) or timestamp.time() >= dt.time(16):
                    continue
                grouped.setdefault(row["symbol"], []).append(
                    OrbBar(
                        timestamp,
                        timestamp + dt.timedelta(minutes=1),
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        int(row["volume"]),
                    )
                )
    return {symbol: tuple(sorted(bars, key=lambda bar: bar.timestamp)) for symbol, bars in grouped.items()}


def _group_selections(
    selections: tuple[OrbSelection, ...],
) -> tuple[tuple[str, tuple[OrbSelection, ...]], ...]:
    grouped: dict[str, list[OrbSelection]] = {}
    for selection in selections:
        grouped.setdefault(selection.symbol, []).append(selection)
    return tuple((symbol, tuple(rows)) for symbol, rows in sorted(grouped.items()))


def _date_from_scoped_path(path: Path) -> dt.date:
    return dt.date(
        int(path.parents[2].name),
        int(path.parents[1].name),
        int(path.parent.name),
    )
