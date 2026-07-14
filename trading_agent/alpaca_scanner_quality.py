from __future__ import annotations

import csv
import datetime as dt
import gzip
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from trading_agent.alpaca_scanner_quality_models import (
    PORTFOLIO_LIMIT,
    AlpacaScannerQualityError,
    ScannerQualityConfig,
    ScannerQualityOutcome,
    select_scanner_candidates,
)
from trading_agent.session_date_range import SessionDateRange
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class _StagedMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str
    session_date: dt.date
    scanner_cutoff: dt.time
    selected_symbol_count: int
    candidate_bar_count: int


class _ArchiveMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str
    session_date: dt.date
    bar_count: int
    symbol_count: int
    window_start: dt.time
    window_end: dt.time


class _DecisionRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    price: float | None
    change_pct: float | None
    dollar_volume: float | None
    adv_fraction: float | None

    @field_validator("price", "change_pct", "dollar_volume", "adv_fraction", mode="before")
    @classmethod
    def parse_optional_number(cls, value: str | float | None) -> str | float | None:
        return None if value == "" else value


class _BarRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float


def analyze_alpaca_scanner_quality(
    root: Path,
    configs: tuple[ScannerQualityConfig, ...],
    *,
    session_range: SessionDateRange | None = None,
) -> tuple[ScannerQualityOutcome, ...]:
    outcomes: list[ScannerQualityOutcome] = []
    paths = (
        path
        for path in sorted((root / "staged_sessions").glob("*/*/*/session_*.metadata.json"))
        if session_range is None or session_range.contains(_date_from_scoped_path(path))
    )
    for path in paths:
        metadata = _StagedMetadata.model_validate_json(path.read_text(encoding="utf-8"))
        if metadata.status != "complete":
            raise AlpacaScannerQualityError(path, "staged session is not complete")
        if metadata.scanner_cutoff != dt.time(9, 30):
            raise AlpacaScannerQualityError(path, "scanner cutoff must be 09:30 America/New_York")
        session_id = path.name.removeprefix("session_").removesuffix(".metadata.json")
        decisions = _read_decisions(
            root
            / "scanner_decisions"
            / metadata.session_date.strftime("%Y/%m/%d")
            / f"scanner_decisions_{session_id}.csv.gz"
        )
        bars = _read_bars(root, metadata)
        for config in configs:
            selected = select_scanner_candidates(decisions, config, PORTFOLIO_LIMIT)
            outcomes.extend(
                _measure(metadata, config, row, rank, bars.get(row.symbol, ()))
                for rank, row in enumerate(selected, start=1)
            )
    return tuple(
        sorted(
            outcomes,
            key=lambda row: (
                row.config.min_change_pct,
                row.config.max_price,
                row.config.min_dollar_volume,
                row.config.min_adv_fraction,
                row.session_date,
                row.rank,
            ),
        )
    )


def _read_decisions(path: Path) -> tuple[_DecisionRow, ...]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        rows = tuple(_DecisionRow.model_validate(row) for row in csv.DictReader(handle))
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                -(row.change_pct or -1.0),
                -(row.adv_fraction or -1.0),
                -(row.dollar_volume or -1.0),
                row.symbol,
            ),
        )
    )


def _read_bars(root: Path, metadata: _StagedMetadata) -> dict[str, tuple[_BarRow, ...]]:
    archive_root = root / "candidate_minutes" / metadata.session_date.strftime("%Y/%m/%d")
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
        raise AlpacaScannerQualityError(archive_root, f"expected one matching archive, found {len(matches)}")
    grouped: dict[str, list[_BarRow]] = {}
    for path in sorted(matches[0].glob("batch_*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                row = _BarRow.model_validate(raw)
                grouped.setdefault(row.symbol, []).append(row)
    return {symbol: tuple(sorted(rows, key=lambda row: row.timestamp)) for symbol, rows in grouped.items()}


def _measure(
    metadata: _StagedMetadata,
    config: ScannerQualityConfig,
    decision: _DecisionRow,
    rank: int,
    bars: tuple[_BarRow, ...],
) -> ScannerQualityOutcome:
    entry_at = dt.datetime.combine(
        metadata.session_date,
        metadata.scanner_cutoff,
        tzinfo=NEW_YORK,
    ) + dt.timedelta(minutes=1)
    bounds = regular_session_bounds(metadata.session_date)
    if bounds is None:
        raise AlpacaScannerQualityError(Path(metadata.session_date.isoformat()), "not a published market session")
    session = tuple(row for row in bars if entry_at <= row.timestamp.astimezone(NEW_YORK) < bounds[1])
    exit_at = bounds[1] - dt.timedelta(minutes=1)
    entry_row = session[0] if session else None
    exit_row = session[-1] if session else None
    if (
        entry_row is None
        or exit_row is None
        or entry_row.timestamp.astimezone(NEW_YORK) != entry_at
        or exit_row.timestamp.astimezone(NEW_YORK) != exit_at
    ):
        return ScannerQualityOutcome(
            config,
            metadata.session_date,
            decision.symbol,
            rank,
            len(session),
            False,
            entry_at,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    entry = entry_row.open
    return ScannerQualityOutcome(
        config,
        metadata.session_date,
        decision.symbol,
        rank,
        len(session),
        True,
        entry_at,
        entry,
        _return_at(session, entry_at + dt.timedelta(minutes=5), entry),
        _return_at(session, entry_at + dt.timedelta(minutes=15), entry),
        _return_at(session, entry_at + dt.timedelta(minutes=30), entry),
        _ratio(exit_row.close, entry),
        _ratio(max(row.high for row in session), entry),
        _ratio(min(row.low for row in session), entry),
    )


def _return_at(rows: tuple[_BarRow, ...], boundary: dt.datetime, entry: float) -> float:
    row = next(row for row in reversed(rows) if row.timestamp.astimezone(NEW_YORK) < boundary)
    return _ratio(row.close, entry)


def _ratio(value: float, entry: float) -> float:
    return round(value / entry - 1.0, 12)


def _date_from_scoped_path(path: Path) -> dt.date:
    return dt.date(
        int(path.parents[2].name),
        int(path.parents[1].name),
        int(path.parent.name),
    )
