from __future__ import annotations

import statistics
from dataclasses import dataclass

from trading_agent.metrics import PerformanceMetrics
from trading_agent.orb_models import OrbTestConfig
from trading_agent.orb_report_rows import CsvRow, config_fields, config_row


@dataclass(frozen=True, slots=True)
class OrbMetricPoint:
    config: OrbTestConfig
    metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class OrbFlatnessResult:
    config: OrbTestConfig
    side_cost_bps: int
    center_trade_count: int
    center_average_return: float | None
    center_profit_factor: float | None
    center_mean_ci_low: float | None
    center_mean_ci_high: float | None
    neighbor_count: int
    eligible_neighbor_count: int
    positive_neighbor_count: int
    positive_neighbor_rate: float | None
    neighbor_average_return_min: float | None
    neighbor_average_return_median: float | None
    neighbor_average_return_max: float | None
    flat_positive_region: bool


def analyze_orb_flatness(
    points: tuple[OrbMetricPoint, ...],
) -> tuple[OrbFlatnessResult, ...]:
    results: list[OrbFlatnessResult] = []
    for point in points:
        group = tuple(row for row in points if row.metrics.side_cost_bps == point.metrics.side_cost_bps)
        neighbors = tuple(row for row in group if _adjacent(point.config, row.config, group))
        eligible = tuple(
            row.metrics.average_return
            for row in neighbors
            if row.metrics.trade_count > 0 and row.metrics.average_return is not None
        )
        positive = tuple(value for value in eligible if value > 0.0)
        positive_rate = len(positive) / len(eligible) if eligible else None
        results.append(
            OrbFlatnessResult(
                point.config,
                point.metrics.side_cost_bps,
                point.metrics.trade_count,
                point.metrics.average_return,
                point.metrics.profit_factor,
                point.metrics.mean_ci_low,
                point.metrics.mean_ci_high,
                len(neighbors),
                len(eligible),
                len(positive),
                positive_rate,
                min(eligible) if eligible else None,
                statistics.median(eligible) if eligible else None,
                max(eligible) if eligible else None,
                _is_flat_positive(point.metrics.average_return, eligible, positive_rate),
            )
        )
    return tuple(
        sorted(
            results,
            key=lambda row: (
                row.config.range_minutes,
                row.config.volume_multiplier,
                row.config.stop_multiple,
                row.config.target_r,
                row.side_cost_bps,
            ),
        )
    )


def flatness_fields() -> tuple[str, ...]:
    return (
        *config_fields(),
        "side_cost_bps",
        "center_trade_count",
        "center_average_return",
        "center_profit_factor",
        "center_mean_ci_low",
        "center_mean_ci_high",
        "neighbor_count",
        "eligible_neighbor_count",
        "positive_neighbor_count",
        "positive_neighbor_rate",
        "neighbor_average_return_min",
        "neighbor_average_return_median",
        "neighbor_average_return_max",
        "flat_positive_region",
    )


def flatness_row(result: OrbFlatnessResult) -> CsvRow:
    return {
        **config_row(result.config),
        **{field: getattr(result, field) for field in flatness_fields() if field not in config_fields()},
    }


def _adjacent(
    left: OrbTestConfig,
    right: OrbTestConfig,
    points: tuple[OrbMetricPoint, ...],
) -> bool:
    if left == right or _fixed_dimensions(left) != _fixed_dimensions(right):
        return False
    left_values = _grid_dimensions(left)
    right_values = _grid_dimensions(right)
    changed = tuple(
        index for index, values in enumerate(zip(left_values, right_values, strict=True)) if values[0] != values[1]
    )
    if len(changed) != 1:
        return False
    dimension = changed[0]
    axis = sorted({_grid_dimensions(point.config)[dimension] for point in points})
    return abs(axis.index(left_values[dimension]) - axis.index(right_values[dimension])) == 1


def _grid_dimensions(config: OrbTestConfig) -> tuple[float, ...]:
    return (
        float(config.range_minutes),
        config.volume_multiplier,
        config.stop_multiple,
        config.target_r,
    )


def _fixed_dimensions(config: OrbTestConfig) -> tuple[float, ...]:
    return (config.breakout_buffer_bps, config.max_risk_pct, config.max_spread_bps)


def _is_flat_positive(
    center: float | None,
    neighbors: tuple[float, ...],
    positive_rate: float | None,
) -> bool:
    return (
        center is not None
        and center > 0.0
        and len(neighbors) >= 4
        and positive_rate is not None
        and positive_rate >= 0.75
        and min(neighbors) > 0.0
    )
