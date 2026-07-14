from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CONFIG_FIELDS: Final = (
    "range_minutes",
    "breakout_buffer_bps",
    "volume_multiplier",
    "stop_multiple",
    "target_r",
)
COSTS: Final = ("5", "10", "20")
PERIODS: Final = ("pre_2025", "2025_plus")
EMPTY_TRADE_METRICS: Final = (
    "win_rate",
    "average_return",
    "profit_factor",
    "cumulative_return",
    "max_drawdown",
    "fallback_exit_rate",
    "mean_ci_low",
    "mean_ci_high",
)
CENTER_EMPTY_METRICS: Final = (
    "center_average_return",
    "center_profit_factor",
    "center_mean_ci_low",
    "center_mean_ci_high",
)
NEIGHBOR_EMPTY_METRICS: Final = (
    "positive_neighbor_rate",
    "neighbor_average_return_min",
    "neighbor_average_return_median",
    "neighbor_average_return_max",
)


@dataclass(frozen=True, slots=True)
class OrbArtifactGateResult:
    passed: bool
    parameter_row_count: int
    period_row_count: int
    flatness_row_count: int
    expected_parameter_row_count: int
    expected_period_row_count: int
    expected_flatness_row_count: int
    issues: tuple[str, ...]


def audit_orb_report_artifacts(
    output_dir: Path,
    *,
    expected_config_count: int,
) -> OrbArtifactGateResult:
    issues: list[str] = []
    parameter_rows = _read_rows(
        output_dir / "orb_parameter_results.csv",
        "parameter",
        (*CONFIG_FIELDS, "side_cost_bps", "trade_count", *EMPTY_TRADE_METRICS),
        issues,
    )
    period_rows = _read_rows(
        output_dir / "orb_period_results.csv",
        "period",
        (*CONFIG_FIELDS, "period", "side_cost_bps", "trade_count", *EMPTY_TRADE_METRICS),
        issues,
    )
    flatness_rows = _read_rows(
        output_dir / "orb_flatness_results.csv",
        "flatness",
        (
            *CONFIG_FIELDS,
            "side_cost_bps",
            "center_trade_count",
            *CENTER_EMPTY_METRICS,
            "neighbor_count",
            "eligible_neighbor_count",
            "positive_neighbor_count",
            *NEIGHBOR_EMPTY_METRICS,
            "flat_positive_region",
        ),
        issues,
    )
    expected_parameter_rows = expected_config_count * len(COSTS)
    expected_period_rows = expected_parameter_rows * len(PERIODS)
    expected_flatness_rows = expected_parameter_rows
    _check_row_count("parameter", len(parameter_rows), expected_parameter_rows, issues)
    _check_row_count("period", len(period_rows), expected_period_rows, issues)
    _check_row_count("flatness", len(flatness_rows), expected_flatness_rows, issues)

    parameter_keys = _row_keys(
        parameter_rows,
        (*CONFIG_FIELDS, "side_cost_bps"),
        "parameter",
        issues,
    )
    config_keys = {key[:-1] for key in parameter_keys}
    if len(config_keys) != expected_config_count:
        issues.append(f"parameter:config_count:{len(config_keys)}!={expected_config_count}")
    for config in config_keys:
        costs = {key[-1] for key in parameter_keys if key[:-1] == config}
        if costs != set(COSTS):
            issues.append(f"parameter:cost_set:{'|'.join(config)}:{','.join(sorted(costs))}")

    period_keys = _row_keys(
        period_rows,
        (*CONFIG_FIELDS, "side_cost_bps", "period"),
        "period",
        issues,
    )
    expected_period_keys = {(*parameter_key, period) for parameter_key in parameter_keys for period in PERIODS}
    _check_key_set("period", period_keys, expected_period_keys, issues)
    flatness_keys = _row_keys(
        flatness_rows,
        (*CONFIG_FIELDS, "side_cost_bps"),
        "flatness",
        issues,
    )
    _check_key_set("flatness", flatness_keys, parameter_keys, issues)

    _check_empty_trade_metrics(parameter_rows, "parameter", issues)
    _check_empty_trade_metrics(period_rows, "period", issues)
    _check_flatness_rows(flatness_rows, issues)
    return OrbArtifactGateResult(
        passed=not issues,
        parameter_row_count=len(parameter_rows),
        period_row_count=len(period_rows),
        flatness_row_count=len(flatness_rows),
        expected_parameter_row_count=expected_parameter_rows,
        expected_period_row_count=expected_period_rows,
        expected_flatness_row_count=expected_flatness_rows,
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


def _check_row_count(
    label: str,
    actual: int,
    expected: int,
    issues: list[str],
) -> None:
    if actual != expected:
        issues.append(f"{label}:row_count:{actual}!={expected}")


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


def _check_key_set(
    label: str,
    actual: set[tuple[str, ...]],
    expected: set[tuple[str, ...]],
    issues: list[str],
) -> None:
    missing = len(expected - actual)
    extra = len(actual - expected)
    if missing or extra:
        issues.append(f"{label}:key_set:missing={missing}:extra={extra}")


def _check_empty_trade_metrics(
    rows: tuple[dict[str, str], ...],
    label: str,
    issues: list[str],
) -> None:
    for index, row in enumerate(rows, start=2):
        trade_count = _nonnegative_int(row.get("trade_count", ""), label, "trade_count", index, issues)
        if trade_count != 0:
            continue
        for field in EMPTY_TRADE_METRICS:
            if row.get(field, ""):
                issues.append(f"{label}:zero_trade_nonblank:{field}:line={index}")


def _check_flatness_rows(
    rows: tuple[dict[str, str], ...],
    issues: list[str],
) -> None:
    for index, row in enumerate(rows, start=2):
        center = _nonnegative_int(
            row.get("center_trade_count", ""),
            "flatness",
            "center_trade_count",
            index,
            issues,
        )
        neighbor = _nonnegative_int(
            row.get("neighbor_count", ""),
            "flatness",
            "neighbor_count",
            index,
            issues,
        )
        eligible = _nonnegative_int(
            row.get("eligible_neighbor_count", ""),
            "flatness",
            "eligible_neighbor_count",
            index,
            issues,
        )
        positive = _nonnegative_int(
            row.get("positive_neighbor_count", ""),
            "flatness",
            "positive_neighbor_count",
            index,
            issues,
        )
        flat = row.get("flat_positive_region", "")
        if flat not in {"True", "False"}:
            issues.append(f"flatness:invalid_boolean:line={index}")
        if neighbor is not None and eligible is not None and eligible > neighbor:
            issues.append(f"flatness:eligible_gt_neighbor:line={index}")
        if eligible is not None and positive is not None and positive > eligible:
            issues.append(f"flatness:positive_gt_eligible:line={index}")
        if center == 0:
            _require_blank(row, CENTER_EMPTY_METRICS, "flatness", index, issues)
            if flat == "True":
                issues.append(f"flatness:zero_center_marked_flat:line={index}")
        if eligible == 0:
            _require_blank(row, NEIGHBOR_EMPTY_METRICS, "flatness", index, issues)
            if flat == "True":
                issues.append(f"flatness:no_neighbors_marked_flat:line={index}")


def _require_blank(
    row: dict[str, str],
    fields: tuple[str, ...],
    label: str,
    index: int,
    issues: list[str],
) -> None:
    for field in fields:
        if row.get(field, ""):
            issues.append(f"{label}:zero_sample_nonblank:{field}:line={index}")


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
