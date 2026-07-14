from __future__ import annotations

import csv
from pathlib import Path

from trading_agent.metrics import PaperTrade, PerformanceMetrics, net_return
from trading_agent.orb_analysis import TRADE_STATUSES
from trading_agent.orb_models import OrbOutcome, OrbTestConfig

CsvValue = str | int | float | bool | None
CsvRow = dict[str, CsvValue]


def config_fields() -> tuple[str, ...]:
    return (
        "range_minutes",
        "breakout_buffer_bps",
        "volume_multiplier",
        "stop_multiple",
        "target_r",
    )


def metric_fields() -> tuple[str, ...]:
    return (
        "side_cost_bps",
        "trade_count",
        "win_count",
        "win_rate",
        "average_return",
        "profit_factor",
        "cumulative_return",
        "max_drawdown",
        "fallback_exit_count",
        "fallback_exit_rate",
        "mean_ci_low",
        "mean_ci_high",
    )


def parameter_fields() -> tuple[str, ...]:
    return (
        *config_fields(),
        "outcome_count",
        "complete_count",
        "signal_count",
        "capacity_skipped_count",
        *metric_fields(),
    )


def outcome_fields() -> tuple[str, ...]:
    return (
        *config_fields(),
        "observed_at",
        "exchange",
        "symbol",
        "change_pct",
        "dollar_volume",
        "spread_bps",
        "complete",
        "status",
        "signal_at",
        "entry_at",
        "exit_at",
        "entry",
        "stop",
        "target",
        "exit_price",
        "gross_return",
        "portfolio_selected",
    )


def trade_fields() -> tuple[str, ...]:
    return (
        "recommendation_id",
        "symbol",
        "entry_at",
        "exit_at",
        "entry",
        "exit",
        "gross_return",
        "status",
        "net_return_5bp",
        "net_return_10bp",
        "net_return_20bp",
    )


def config_row(config: OrbTestConfig) -> CsvRow:
    return {field: getattr(config, field) for field in config_fields()}


def metric_row(metrics: PerformanceMetrics) -> CsvRow:
    return {field: getattr(metrics, field) for field in metric_fields()}


def sample_counts(rows: tuple[OrbOutcome, ...]) -> CsvRow:
    return {
        "outcome_count": len(rows),
        "complete_count": sum(row.complete for row in rows),
        "signal_count": sum(row.signal_at is not None for row in rows),
        "capacity_skipped_count": sum(
            row.status in TRADE_STATUSES and not row.portfolio_selected
            for row in rows
        ),
    }


def outcome_row(row: OrbOutcome) -> CsvRow:
    return {
        **config_row(row.config),
        "observed_at": row.observed_at.isoformat(),
        "exchange": row.exchange,
        "symbol": row.symbol,
        "change_pct": row.change_pct,
        "dollar_volume": row.dollar_volume,
        "spread_bps": row.spread_bps,
        "complete": row.complete,
        "status": row.status.value,
        "signal_at": None if row.signal_at is None else row.signal_at.isoformat(),
        "entry_at": None if row.entry_at is None else row.entry_at.isoformat(),
        "exit_at": None if row.exit_at is None else row.exit_at.isoformat(),
        "entry": row.entry,
        "stop": row.stop,
        "target": row.target,
        "exit_price": row.exit_price,
        "gross_return": row.gross_return,
        "portfolio_selected": row.portfolio_selected,
    }


def trade_row(trade: PaperTrade) -> CsvRow:
    return {
        "recommendation_id": trade.recommendation_id,
        "symbol": trade.symbol,
        "entry_at": trade.entry_at.isoformat(),
        "exit_at": trade.exit_at.isoformat(),
        "entry": trade.entry,
        "exit": trade.exit,
        "gross_return": trade.gross_return,
        "status": trade.exit_state.value,
        "net_return_5bp": net_return(trade, 5),
        "net_return_10bp": net_return(trade, 10),
        "net_return_20bp": net_return(trade, 20),
    }


def write_rows(
    path: Path,
    fields: tuple[str, ...],
    rows: tuple[CsvRow, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
