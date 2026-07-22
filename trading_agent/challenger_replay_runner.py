from __future__ import annotations

import csv
import datetime as dt
import resource
import sys
from pathlib import Path

from trading_agent.challenger_replay_models import ReplayBar, ReplayContext, ReplaySource
from trading_agent.engine import RecommendationEngine, finalize_due_recommendations
from trading_agent.intraday_research_loop_models import (
    IntradayWalkForwardError,
    IntradayWalkForwardRequest,
    IntradayWalkForwardResult,
)
from trading_agent.kis_live import NEW_YORK, regular_session_bounds
from trading_agent.metrics import MetricsConfig, extract_paper_trades, summarize_performance
from trading_agent.metrics_report import write_metrics_report
from trading_agent.models import BarInput
from trading_agent.replay import write_report
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore
from trading_agent.strategy_factory import StrategyMode, build_strategy


def run_intraday_walk_forward(
    request: IntradayWalkForwardRequest,
    work_dir: Path,
) -> IntradayWalkForwardResult:
    sessions: dict[dt.date, list[BarInput]] = {}
    for bar in request.bars:
        sessions.setdefault(bar.timestamp.astimezone(NEW_YORK).date(), []).append(bar)
    ordered_sessions = tuple(sorted(sessions.items()))
    oos_sessions = ordered_sessions[request.minimum_training_sessions :]
    if not oos_sessions:
        raise IntradayWalkForwardError("no_oos_sessions")
    work_dir.mkdir(parents=True, exist_ok=True)
    database = work_dir / f"{request.strategy.value}.sqlite3"
    if database.exists():
        raise IntradayWalkForwardError("work_database_exists")
    store = PaperStore(database)
    for _, rows in oos_sessions:
        _require_rss_below(request.rss_limit_gib)
        engine = _engine(request.strategy, store)
        last_bars: dict[str, BarInput] = {}
        for bar in rows:
            _ = engine.process(bar)
            last_bars[bar.symbol] = bar
        for bar in last_bars.values():
            engine.finalize_day(bar)
    trades = extract_paper_trades((store,))
    metrics = summarize_performance(
        trades,
        MetricsConfig(request.per_side_cost_bps, request.bootstrap_samples, 20_260_722),
    )
    peak = _require_rss_below(request.rss_limit_gib)
    return IntradayWalkForwardResult(
        strategy=request.strategy,
        observed_sessions=len(oos_sessions),
        fold_count=len(oos_sessions),
        trade_count=metrics.trade_count,
        side_cost_bps=metrics.side_cost_bps,
        gross_average_return=(None if not trades else sum(row.gross_return for row in trades) / len(trades)),
        average_return=metrics.average_return,
        profit_factor=metrics.profit_factor,
        cumulative_return=metrics.cumulative_return,
        max_drawdown=metrics.max_drawdown,
        mean_ci_low=metrics.mean_ci_low,
        mean_ci_high=metrics.mean_ci_high,
        peak_rss_gib=peak,
    )


def _require_rss_below(limit_gib: float) -> float:
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    bytes_used = peak if sys.platform == "darwin" else peak * 1024.0
    rss_gib = bytes_used / (1024.0**3)
    if rss_gib >= limit_gib:
        raise IntradayWalkForwardError("rss_limit_reached")
    return rss_gib


def run_challenger_replay(
    source: ReplaySource,
    strategy: StrategyMode,
    output: Path,
) -> tuple[int, int]:
    database = output / "paper_recommendations.sqlite3"
    if database.exists():
        raise FileExistsError(database)
    output.mkdir(parents=True, exist_ok=True)
    _write_coverage(output / "symbol_coverage.csv", source)
    store = PaperStore(database)
    complete_keys = {(row.exchange, row.symbol) for row in source.coverage if row.complete}
    complete_contexts = tuple(row for row in source.contexts if (row.exchange, row.symbol) in complete_keys)
    for context in complete_contexts:
        known = tuple(
            _bar_input(row, context)
            for row in source.bars
            if (row.exchange, row.symbol) == (context.exchange, context.symbol)
            and row.first_observed_at <= context.observed_at
            and row.timestamp <= context.latest_completed_bar_at
        )
        _engine(strategy, store).process_forward(known, context.observed_at)
    latest_contexts = _latest_context_by_key(complete_contexts)
    for key, context in latest_contexts.items():
        if not store.open_recommendations(context.symbol):
            continue
        full_path = tuple(_bar_input(row, context) for row in source.bars if (row.exchange, row.symbol) == key)
        _engine(strategy, store).advance_forward(full_path)
    bounds = regular_session_bounds(source.session_date)
    if bounds is not None:
        _ = finalize_due_recommendations(store, bounds[1])
    trades = extract_paper_trades((store,))
    _ = write_metrics_report(output / "paper_metrics", trades)
    write_report(output / "recommendations_ko.md", store)
    return len(store.recommendations()), len(trades)


def _engine(strategy: StrategyMode, store: PaperStore) -> RecommendationEngine:
    return RecommendationEngine(
        MomentumScanner(ScannerConfig()),
        build_strategy(strategy, range_minutes=5),
        RiskConfig(),
        store,
    )


def _bar_input(row: ReplayBar, context: ReplayContext) -> BarInput:
    return BarInput(
        symbol=row.symbol,
        timestamp=row.timestamp,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        prior_close=context.prior_close,
        average_daily_volume=context.average_daily_volume,
        spread_bps=context.spread_bps,
    )


def _latest_context_by_key(
    contexts: tuple[ReplayContext, ...],
) -> dict[tuple[str, str], ReplayContext]:
    result: dict[tuple[str, str], ReplayContext] = {}
    for row in contexts:
        result[(row.exchange, row.symbol)] = row
    return result


def _write_coverage(path: Path, source: ReplaySource) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("exchange", "symbol", "expected_minutes", "archived_minutes", "complete", "reason"))
        writer.writerows(
            (
                row.exchange,
                row.symbol,
                row.expected_minutes,
                row.archived_minutes,
                row.complete,
                row.reason,
            )
            for row in source.coverage
        )
