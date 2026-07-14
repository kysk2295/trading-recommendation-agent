from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.us_equity_calendar import NEW_YORK

CONFIG_FIELDS: Final = (
    "min_change_pct",
    "min_price",
    "max_price",
    "min_dollar_volume",
    "min_adv_fraction",
)
COUNT_FIELDS: Final = (
    "selected_session_count",
    "selection_count",
    "complete_count",
    "censored_count",
)
SUMMARY_METRICS: Final = (
    "path_coverage_rate",
    "positive_5m_rate",
    "positive_15m_rate",
    "positive_30m_rate",
    "positive_eod_rate",
    "average_5m_return",
    "average_15m_return",
    "average_30m_return",
    "average_eod_return",
    "average_mfe",
    "average_mae",
    "mean_ci_low",
    "mean_ci_high",
)
OUTCOME_PATH_FIELDS: Final = (
    "entry",
    "return_5m",
    "return_15m",
    "return_30m",
    "eod_return",
    "mfe",
    "mae",
)


@dataclass(frozen=True, slots=True)
class ScannerArtifactGateResult:
    passed: bool
    summary_row_count: int
    outcome_row_count: int
    yearly_row_count: int
    expected_summary_row_count: int
    issues: tuple[str, ...]


def audit_scanner_report_artifacts(
    output_dir: Path,
    *,
    expected_config_count: int,
) -> ScannerArtifactGateResult:
    issues: list[str] = []
    summary_rows = _read_rows(
        output_dir / "scanner_quality_summary.csv",
        "summary",
        (*CONFIG_FIELDS, *COUNT_FIELDS, *SUMMARY_METRICS),
        issues,
    )
    outcome_rows = _read_rows(
        output_dir / "scanner_quality_outcomes.csv",
        "outcome",
        (
            *CONFIG_FIELDS,
            "session_date",
            "symbol",
            "rank",
            "bar_count",
            "complete",
            "entry_at",
            *OUTCOME_PATH_FIELDS,
        ),
        issues,
    )
    yearly_rows = _read_rows(
        output_dir / "scanner_quality_yearly.csv",
        "yearly",
        ("year", *CONFIG_FIELDS, *COUNT_FIELDS, *SUMMARY_METRICS),
        issues,
    )
    report_path = output_dir / "scanner_quality_report_ko.md"
    if not report_path.is_file() or report_path.stat().st_size == 0:
        issues.append("report:missing_or_empty:scanner_quality_report_ko.md")

    if len(summary_rows) != expected_config_count:
        issues.append(f"summary:row_count:{len(summary_rows)}!={expected_config_count}")
    summary_keys = _row_keys(summary_rows, CONFIG_FIELDS, "summary", issues)
    if len(summary_keys) != expected_config_count:
        issues.append(f"summary:config_count:{len(summary_keys)}!={expected_config_count}")
    _ = _row_keys(
        outcome_rows,
        (*CONFIG_FIELDS, "session_date", "symbol"),
        "outcome",
        issues,
    )
    yearly_keys = _row_keys(
        yearly_rows,
        ("year", *CONFIG_FIELDS),
        "yearly",
        issues,
    )
    extra_yearly_configs = {key[1:] for key in yearly_keys} - summary_keys
    if extra_yearly_configs:
        issues.append(f"yearly:unknown_configs:{len(extra_yearly_configs)}")

    _check_summary_rows(summary_rows, "summary", issues)
    _check_summary_rows(yearly_rows, "yearly", issues)
    _check_outcome_rows(outcome_rows, issues)
    return ScannerArtifactGateResult(
        passed=not issues,
        summary_row_count=len(summary_rows),
        outcome_row_count=len(outcome_rows),
        yearly_row_count=len(yearly_rows),
        expected_summary_row_count=expected_config_count,
        issues=tuple(issues),
    )


def _read_rows(
    path: Path,
    label: str,
    required_fields: tuple[str, ...],
    issues: list[str],
) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        issues.append(f"{label}:missing:{path.name}")
        return ()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        missing = sorted(set(required_fields) - fields)
        if missing:
            issues.append(f"{label}:missing_fields:{','.join(missing)}")
        return tuple(reader)


def _row_keys(
    rows: tuple[dict[str, str], ...],
    fields: tuple[str, ...],
    label: str,
    issues: list[str],
) -> set[tuple[str, ...]]:
    keys: list[tuple[str, ...]] = []
    for index, row in enumerate(rows, start=2):
        key = tuple(row.get(field, "") for field in fields)
        if any(not value for value in key):
            issues.append(f"{label}:blank_key:line={index}")
        keys.append(key)
    unique = set(keys)
    if len(unique) != len(keys):
        issues.append(f"{label}:duplicate_keys:{len(keys) - len(unique)}")
    return unique


def _check_summary_rows(
    rows: tuple[dict[str, str], ...],
    label: str,
    issues: list[str],
) -> None:
    for index, row in enumerate(rows, start=2):
        sessions = _nonnegative_int(row.get("selected_session_count", ""), label, "sessions", index, issues)
        selected = _nonnegative_int(row.get("selection_count", ""), label, "selected", index, issues)
        complete = _nonnegative_int(row.get("complete_count", ""), label, "complete", index, issues)
        censored = _nonnegative_int(row.get("censored_count", ""), label, "censored", index, issues)
        if selected is not None and complete is not None and censored is not None and selected != complete + censored:
            issues.append(f"{label}:count_mismatch:line={index}")
        if sessions is not None and selected is not None and sessions > selected:
            issues.append(f"{label}:sessions_gt_selection:line={index}")
        if selected == 0:
            for field in SUMMARY_METRICS:
                if row.get(field, ""):
                    issues.append(f"{label}:empty_selection_nonblank:{field}:line={index}")


def _check_outcome_rows(
    rows: tuple[dict[str, str], ...],
    issues: list[str],
) -> None:
    groups: dict[tuple[str, ...], list[int]] = {}
    for index, row in enumerate(rows, start=2):
        rank = _nonnegative_int(row.get("rank", ""), "outcome", "rank", index, issues)
        _ = _nonnegative_int(row.get("bar_count", ""), "outcome", "bar_count", index, issues)
        group = tuple(row.get(field, "") for field in (*CONFIG_FIELDS, "session_date"))
        if rank is not None:
            groups.setdefault(group, []).append(rank)
        _check_entry_at(row, index, issues)
        complete = row.get("complete", "")
        if complete not in {"True", "False"}:
            issues.append(f"outcome:invalid_complete:line={index}")
        if complete == "False":
            for field in OUTCOME_PATH_FIELDS:
                if row.get(field, ""):
                    issues.append(f"outcome:censored_nonblank:{field}:line={index}")
    for group, ranks in groups.items():
        label = "|".join(group)
        if len(ranks) > 10:
            issues.append(f"outcome:portfolio_limit:{label}:{len(ranks)}>10")
        expected = list(range(1, len(ranks) + 1))
        actual = sorted(ranks)
        if actual != expected:
            issues.append(
                f"outcome:rank_sequence:{label}:expected=1-{len(ranks)}:"
                + f"actual={','.join(str(rank) for rank in actual)}"
            )


def _check_entry_at(
    row: dict[str, str],
    index: int,
    issues: list[str],
) -> None:
    try:
        session_date = dt.date.fromisoformat(row.get("session_date", ""))
        entry_at = dt.datetime.fromisoformat(row.get("entry_at", ""))
    except ValueError:
        issues.append(f"outcome:invalid_entry_at:line={index}")
        return
    if entry_at.tzinfo is None:
        issues.append(f"outcome:naive_entry_at:line={index}")
        return
    expected = dt.datetime.combine(
        session_date,
        dt.time(9, 31),
        tzinfo=NEW_YORK,
    )
    if entry_at.astimezone(NEW_YORK) != expected:
        issues.append(f"outcome:entry_at_not_09_31:line={index}")


def _nonnegative_int(
    value: str,
    label: str,
    field: str,
    index: int,
    issues: list[str],
) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        issues.append(f"{label}:invalid_integer:{field}:line={index}")
        return None
    if parsed < 0:
        issues.append(f"{label}:negative_integer:{field}:line={index}")
        return None
    return parsed
