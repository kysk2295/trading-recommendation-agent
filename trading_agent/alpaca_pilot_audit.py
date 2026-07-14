from __future__ import annotations

import csv
import datetime as dt
import gzip
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from trading_agent.alpaca_pilot_audit_grid import scanner_grid_union_issues
from trading_agent.session_date_range import SessionDateRange
from trading_agent.us_equity_calendar import regular_session_bounds

NEW_YORK = ZoneInfo("America/New_York")


class _StagedMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str
    session_date: dt.date
    scanner_cutoff: dt.time
    reference_source: str
    universe_symbol_count: int
    selected_symbol_count: int
    selected_symbols: tuple[str, ...]
    base_selected_symbol_count: int | None = None
    base_selected_symbols: tuple[str, ...] | None = None
    candidate_selection_contract: str | None = None
    scanner_grid_config_count: int | None = None
    scanner_grid_portfolio_limit: int | None = None
    scanner_bar_count: int
    candidate_bar_count: int
    selection_uses_bars_strictly_before_cutoff: bool


class _ArchiveMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    status: str
    session_date: dt.date
    bar_count: int
    symbol_count: int
    window_start: dt.time
    window_end: dt.time


@dataclass(frozen=True, slots=True)
class _Decision:
    symbol: str
    selected: bool
    last_timestamp: dt.datetime | None
    price: float | None
    change_pct: float | None
    dollar_volume: float | None
    adv_fraction: float | None


@dataclass(frozen=True, slots=True)
class _ArchiveExpectation:
    root_name: str
    session_date: dt.date
    window_start: dt.time
    window_end: dt.time
    symbol_count: int
    bar_count: int


@dataclass(frozen=True, slots=True)
class _ArchiveAudit:
    row_count: int
    duplicate_count: int
    temporal_violation_count: int
    symbols: frozenset[str]


@dataclass(frozen=True, slots=True)
class PilotAuditResult:
    passed: bool
    session_count: int
    selected_symbol_count: int
    scanner_bar_count: int
    candidate_bar_count: int
    scanner_duplicate_count: int
    candidate_duplicate_count: int
    temporal_violation_count: int
    incomplete_artifact_count: int
    issues: tuple[str, ...]
    session_start: str | None
    session_end: str | None


def audit_staged_pilot(
    root: Path,
    minimum_sessions: int = 50,
    session_range: SessionDateRange | None = None,
) -> PilotAuditResult:
    issues: list[str] = []
    selected_count = 0
    scanner_bars = 0
    candidate_bars = 0
    scanner_duplicates = 0
    candidate_duplicates = 0
    temporal_violations = 0
    valid_sessions = 0
    artifacts = tuple(
        path for path in root.rglob("*") if _is_incomplete(path) and _artifact_is_in_range(root, path, session_range)
    )
    metadata_paths = tuple(
        path
        for path in sorted((root / "staged_sessions").glob("*/*/*/session_*.metadata.json"))
        if _metadata_is_in_range(path, session_range)
    )
    for metadata_path in metadata_paths:
        try:
            metadata = _StagedMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            issues.append(f"invalid_staged_metadata:{metadata_path}:{error}")
            continue
        if regular_session_bounds(metadata.session_date) is None:
            issues.append(f"non_market_session:{metadata.session_date}")
            continue
        valid_sessions += 1
        session_id = metadata_path.name.removeprefix("session_").removesuffix(".metadata.json")
        if metadata.status != "complete":
            issues.append(f"incomplete_session:{metadata.session_date}")
        if metadata.reference_source != "range_cache":
            issues.append(f"reference_source:{metadata.session_date}:{metadata.reference_source}")
        if not metadata.selection_uses_bars_strictly_before_cutoff:
            issues.append(f"cutoff_flag:{metadata.session_date}")
        selected_count += metadata.selected_symbol_count
        scanner_bars += metadata.scanner_bar_count
        candidate_bars += metadata.candidate_bar_count
        decisions_path = (
            root
            / "scanner_decisions"
            / metadata.session_date.strftime("%Y/%m/%d")
            / f"scanner_decisions_{session_id}.csv.gz"
        )
        decisions = _read_decisions(decisions_path, issues)
        base_selected = frozenset(row.symbol for row in decisions if row.selected)
        archived = frozenset(metadata.selected_symbols)
        if len(archived) != metadata.selected_symbol_count:
            issues.append(f"selected_symbol_count:{metadata.session_date}")
        if metadata.candidate_selection_contract is None:
            if base_selected != archived:
                issues.append(f"selected_symbols:{metadata.session_date}")
        else:
            issues.extend(
                scanner_grid_union_issues(
                    metadata.session_date,
                    metadata.candidate_selection_contract,
                    metadata.base_selected_symbol_count,
                    metadata.base_selected_symbols,
                    metadata.scanner_grid_config_count,
                    metadata.scanner_grid_portfolio_limit,
                    decisions,
                    base_selected,
                    archived,
                )
            )
        cutoff_at = dt.datetime.combine(metadata.session_date, metadata.scanner_cutoff, tzinfo=NEW_YORK)
        temporal_violations += sum(
            row.last_timestamp is not None and row.last_timestamp.astimezone(dt.UTC) >= cutoff_at for row in decisions
        )
        scanner = _audit_expected_archive(
            root,
            _ArchiveExpectation(
                "scanner_minutes",
                metadata.session_date,
                dt.time(4),
                metadata.scanner_cutoff,
                metadata.universe_symbol_count,
                metadata.scanner_bar_count,
            ),
            issues,
        )
        scanner_duplicates += scanner.duplicate_count
        candidate = _audit_expected_archive(
            root,
            _ArchiveExpectation(
                "candidate_minutes",
                metadata.session_date,
                metadata.scanner_cutoff,
                dt.time(20),
                metadata.selected_symbol_count,
                metadata.candidate_bar_count,
            ),
            issues,
        )
        candidate_duplicates += candidate.duplicate_count
        temporal_violations += candidate.temporal_violation_count
        if not candidate.symbols.issubset(archived):
            issues.append(f"candidate_symbols:{metadata.session_date}")
    if valid_sessions < minimum_sessions:
        issues.append(f"minimum_sessions:{valid_sessions}<{minimum_sessions}")
    if artifacts:
        issues.append(f"incomplete_artifacts:{len(artifacts)}")
    passed = not issues and not scanner_duplicates and not candidate_duplicates and not temporal_violations
    return PilotAuditResult(
        passed,
        valid_sessions,
        selected_count,
        scanner_bars,
        candidate_bars,
        scanner_duplicates,
        candidate_duplicates,
        temporal_violations,
        len(artifacts),
        tuple(issues),
        session_range.start.isoformat() if session_range is not None else None,
        session_range.end.isoformat() if session_range is not None else None,
    )


def _audit_expected_archive(
    root: Path,
    expected: _ArchiveExpectation,
    issues: list[str],
) -> _ArchiveAudit:
    archive_root = root / expected.root_name / expected.session_date.strftime("%Y/%m/%d")
    matches: list[Path] = []
    for path in archive_root.glob("archive_*/session.metadata.json"):
        try:
            metadata = _ArchiveMetadata.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            issues.append(f"invalid_archive_metadata:{path}:{error}")
            continue
        if (
            metadata.status == "complete"
            and metadata.session_date == expected.session_date
            and metadata.window_start == expected.window_start
            and metadata.window_end == expected.window_end
            and metadata.symbol_count == expected.symbol_count
            and metadata.bar_count == expected.bar_count
        ):
            matches.append(path.parent)
    if len(matches) != 1:
        issues.append(f"archive_match:{expected.root_name}:{expected.session_date}:{len(matches)}")
        return _ArchiveAudit(0, 0, 0, frozenset())
    return _audit_rows(matches[0], expected)


def _audit_rows(archive: Path, expected: _ArchiveExpectation) -> _ArchiveAudit:
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    temporal = 0
    symbols: set[str] = set()
    cutoff_at = dt.datetime.combine(expected.session_date, expected.window_start, tzinfo=NEW_YORK)
    rows = 0
    for path in sorted(archive.glob("batch_*.csv.gz")):
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (row["symbol"], row["timestamp"])
                duplicates += key in seen
                seen.add(key)
                symbols.add(row["symbol"])
                rows += 1
                if expected.root_name == "candidate_minutes":
                    temporal += dt.datetime.fromisoformat(row["timestamp"]).astimezone(dt.UTC) < cutoff_at
    return _ArchiveAudit(rows, duplicates, temporal, frozenset(symbols))


def _read_decisions(path: Path, issues: list[str]) -> tuple[_Decision, ...]:
    if not path.is_file():
        issues.append(f"missing_decisions:{path}")
        return ()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return tuple(
            _Decision(
                row["symbol"],
                row["selected"] == "True",
                dt.datetime.fromisoformat(row["last_timestamp"]) if row["last_timestamp"] else None,
                _optional_float(row.get("price")),
                _optional_float(row.get("change_pct")),
                _optional_float(row.get("dollar_volume")),
                _optional_float(row.get("adv_fraction")),
            )
            for row in csv.DictReader(handle)
        )


def _optional_float(value: str | None) -> float | None:
    return None if value in (None, "") else float(value)


def _is_incomplete(path: Path) -> bool:
    return path.is_file() and (path.name.endswith((".part", ".tmp", "-journal", "-wal")))


def _metadata_is_in_range(path: Path, session_range: SessionDateRange | None) -> bool:
    return session_range is None or session_range.contains(_date_from_scoped_path(path))


def _artifact_is_in_range(
    root: Path,
    path: Path,
    session_range: SessionDateRange | None,
) -> bool:
    if session_range is None:
        return True
    relative = path.relative_to(root)
    date_scoped_roots = {
        "candidate_minutes",
        "daily_reference",
        "scanner_decisions",
        "scanner_minutes",
        "staged_sessions",
    }
    if len(relative.parts) < 4 or relative.parts[0] not in date_scoped_roots:
        return True
    try:
        session_date = dt.date(*(int(part) for part in relative.parts[1:4]))
    except ValueError:
        return True
    return session_range.contains(session_date)


def _date_from_scoped_path(path: Path) -> dt.date:
    return dt.date(
        int(path.parents[2].name),
        int(path.parents[1].name),
        int(path.parent.name),
    )
