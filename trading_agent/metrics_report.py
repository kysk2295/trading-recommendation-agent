from __future__ import annotations

import csv
from pathlib import Path
from typing import Final

from trading_agent.metrics import (
    MetricsConfig,
    PaperTrade,
    PerformanceMetrics,
    net_return,
    summarize_performance,
)

SIDE_COSTS_BPS: Final = (5, 10, 20)
BOOTSTRAP_SAMPLES: Final = 2_000
BOOTSTRAP_SEED: Final = 20_260_713
CsvValue = str | int | float | bool | None
CsvRow = dict[str, CsvValue]


def write_metrics_report(
    output_dir: Path,
    trades: tuple[PaperTrade, ...],
) -> tuple[PerformanceMetrics, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = tuple(
        summarize_performance(
            trades,
            MetricsConfig(cost, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED),
        )
        for cost in SIDE_COSTS_BPS
    )
    _write_rows(
        output_dir / "paper_metrics.csv",
        _metric_fields(),
        tuple(_metric_row(row) for row in summaries),
    )
    years = tuple(sorted({trade.exit_at.year for trade in trades}))
    yearly_rows: list[CsvRow] = []
    for year in years:
        year_trades = tuple(trade for trade in trades if trade.exit_at.year == year)
        for cost in SIDE_COSTS_BPS:
            summary = summarize_performance(
                year_trades,
                MetricsConfig(cost, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED + year),
            )
            yearly_rows.append({"year": year, **_metric_row(summary)})
    _write_rows(
        output_dir / "paper_yearly_metrics.csv",
        ("year", *_metric_fields()),
        tuple(yearly_rows),
    )
    trade_rows = tuple(
        {
            "recommendation_id": trade.recommendation_id,
            "symbol": trade.symbol,
            "strategy": trade.strategy,
            "entry_at": trade.entry_at.isoformat(),
            "exit_at": trade.exit_at.isoformat(),
            "entry": trade.entry,
            "exit": trade.exit,
            "gross_return": trade.gross_return,
            "exit_state": trade.exit_state.value,
            "uses_close_fallback": trade.uses_close_fallback,
            "net_return_5bp": net_return(trade, 5),
            "net_return_10bp": net_return(trade, 10),
            "net_return_20bp": net_return(trade, 20),
        }
        for trade in trades
    )
    _write_rows(
        output_dir / "paper_trades.csv",
        tuple(trade_rows[0]) if trade_rows else _trade_fields(),
        trade_rows,
    )
    lines = [
        "# Paper 전진검증 성과 대시보드",
        "",
        "> QA·paper 표본이며 수익성 증거나 실제 체결 성과가 아닙니다.",
        "",
        "| 편도 비용(bp) | 거래 | 승률 | PF | 평균 | 누적 | MDD | 평균 95% CI | fallback |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(_metric_markdown(row) for row in summaries)
    lines.extend(
        (
            "",
            "## 해석 제한",
            "",
            "- `active` 뒤 손절·2R·당일 종료가 확인된 추천만 거래로 집계합니다.",
            "- 누적수익과 MDD는 거래 순차 복리 proxy이며 최대 10포지션 일별 포트폴리오가 아닙니다.",
            "- 평균수익 95% CI는 거래일을 블록으로 재표본화한 고정 seed bootstrap이며 "
            + "두 거래일 미만이면 N/A입니다.",
            "- 여러 비용·전략·파라미터를 반복 비교하면 다중검정과 데이터 스누핑 위험이 커집니다.",
            "- 마지막 완료 봉 fallback은 실제 MOC가 아니므로 별도 비율로 표시합니다.",
        )
    )
    _ = (output_dir / "paper_metrics_ko.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return summaries


def _metric_fields() -> tuple[str, ...]:
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


def _trade_fields() -> tuple[str, ...]:
    return (
        "recommendation_id",
        "symbol",
        "strategy",
        "entry_at",
        "exit_at",
        "entry",
        "exit",
        "gross_return",
        "exit_state",
        "uses_close_fallback",
        "net_return_5bp",
        "net_return_10bp",
        "net_return_20bp",
    )


def _metric_row(row: PerformanceMetrics) -> CsvRow:
    return {field: getattr(row, field) for field in _metric_fields()}


def _write_rows(
    path: Path,
    fields: tuple[str, ...],
    rows: tuple[CsvRow, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _metric_markdown(row: PerformanceMetrics) -> str:
    return (
        f"| {row.side_cost_bps} | {row.trade_count} | {_pct(row.win_rate)} | "
        + f"{_number(row.profit_factor)} | {_pct(row.average_return)} | "
        + f"{_pct(row.cumulative_return)} | {_pct(row.max_drawdown)} | "
        + f"{_pct(row.mean_ci_low)} ~ {_pct(row.mean_ci_high)} | "
        + f"{_pct(row.fallback_exit_rate)} |"
    )


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def _number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"
