from __future__ import annotations

from trading_agent.metrics import PerformanceMetrics
from trading_agent.orb_flatness import OrbMetricPoint, analyze_orb_flatness
from trading_agent.orb_models import OrbTestConfig


def test_orb_flatness_uses_only_one_step_axis_neighbors() -> None:
    center = OrbTestConfig(5, 5.0, 1.5, 1.0, 2.0)
    neighbors = (
        OrbTestConfig(1, 5.0, 1.5, 1.0, 2.0),
        OrbTestConfig(15, 5.0, 1.5, 1.0, 2.0),
        OrbTestConfig(5, 5.0, 1.0, 1.0, 2.0),
        OrbTestConfig(5, 5.0, 2.0, 1.0, 2.0),
        OrbTestConfig(5, 5.0, 1.5, 0.75, 2.0),
        OrbTestConfig(5, 5.0, 1.5, 1.25, 2.0),
        OrbTestConfig(5, 5.0, 1.5, 1.0, 1.0),
        OrbTestConfig(5, 5.0, 1.5, 1.0, 3.0),
    )
    diagonal = OrbTestConfig(15, 5.0, 2.0, 1.0, 2.0)
    points = (
        OrbMetricPoint(center, _metrics(0.02)),
        *(OrbMetricPoint(config, _metrics(0.01)) for config in neighbors),
        OrbMetricPoint(diagonal, _metrics(-0.50)),
    )

    results = analyze_orb_flatness(points)
    result = next(row for row in results if row.config == center)

    assert result.neighbor_count == 8
    assert result.eligible_neighbor_count == 8
    assert result.positive_neighbor_count == 8
    assert result.positive_neighbor_rate == 1.0
    assert result.neighbor_average_return_min == 0.01
    assert result.flat_positive_region


def _metrics(average_return: float) -> PerformanceMetrics:
    return PerformanceMetrics(
        10,
        100,
        60 if average_return > 0 else 40,
        0.6 if average_return > 0 else 0.4,
        average_return,
        1.5 if average_return > 0 else 0.8,
        average_return,
        -0.1,
        0,
        0.0,
        average_return - 0.01,
        average_return + 0.01,
    )
