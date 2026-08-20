from __future__ import annotations

import datetime as dt
import hashlib
import resource
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.engine import RecommendationEngine
from trading_agent.generated_strategy_artifact import PublishedGeneratedStrategy
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.intraday_walk_forward_models import (
    INTRADAY_BOOTSTRAP_SEED,
    GeneratedIntradayWalkForwardResult,
    IntradaySessionOutcome,
)
from trading_agent.kis_live import NEW_YORK
from trading_agent.metrics import MetricsConfig, PaperTrade, extract_paper_trades, net_return, summarize_performance
from trading_agent.models import BarInput
from trading_agent.risk import RiskConfig
from trading_agent.scanner import MomentumScanner, ScannerConfig
from trading_agent.store import PaperStore

MAX_GENERATED_INTRADAY_RSS_GIB: Final = 10.0
_FORBIDDEN_FULL_UNIVERSE_DIRECTORY: Final = "regend_us_stocks"


class GeneratedIntradayEvaluationError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GeneratedIntradayEvaluationRequest:
    bars: tuple[BarInput, ...]
    strategy: PublishedGeneratedStrategy
    sandbox: GeneratedStrategySandbox
    minimum_training_sessions: int
    per_side_cost_bps: int
    bootstrap_samples: int
    rss_limit_gib: float
    source_path: Path | None = None


def run_generated_intraday_walk_forward(
    request: GeneratedIntradayEvaluationRequest,
    work_dir: Path,
) -> GeneratedIntradayWalkForwardResult:
    validate_generated_intraday_evaluation_scope(request.source_path, request.rss_limit_gib)
    sessions: dict[dt.date, list[BarInput]] = {}
    for bar in request.bars:
        sessions.setdefault(bar.timestamp.astimezone(NEW_YORK).date(), []).append(bar)
    oos_sessions = tuple(sorted(sessions.items()))[request.minimum_training_sessions :]
    if not oos_sessions:
        raise GeneratedIntradayEvaluationError("no_oos_sessions")
    work_dir.mkdir(parents=True, exist_ok=True)
    database = work_dir / "generated-python.sqlite3"
    if database.exists():
        raise GeneratedIntradayEvaluationError("work_database_exists")
    store = PaperStore(database)
    stream_hashes: list[str] = []
    for _, bars in oos_sessions:
        _ = _require_rss(request.rss_limit_gib)
        with request.sandbox.open_session(request.strategy) as strategy:
            engine = RecommendationEngine(
                MomentumScanner(ScannerConfig()),
                strategy,
                RiskConfig(),
                store,
            )
            last_bars: dict[str, BarInput] = {}
            for bar in bars:
                _ = engine.process(bar)
                last_bars[bar.symbol] = bar
            for bar in last_bars.values():
                engine.finalize_day(bar)
            stream_hashes.append(strategy.signal_stream_sha256)
    trades = extract_paper_trades((store,))
    metrics = summarize_performance(
        trades,
        MetricsConfig(request.per_side_cost_bps, request.bootstrap_samples, INTRADAY_BOOTSTRAP_SEED),
    )
    outcomes = tuple(
        _session_outcome(session_date, trades, request.per_side_cost_bps)
        for session_date, _ in oos_sessions
    )
    artifact = request.strategy.artifact
    return GeneratedIntradayWalkForwardResult(
        strategy_version=f"generated-python:{artifact.artifact_id}",
        strategy_artifact_id=artifact.artifact_id,
        runtime_fingerprint=artifact.payload.runtime.runtime_fingerprint,
        signal_stream_sha256=hashlib.sha256("".join(stream_hashes).encode()).hexdigest(),
        observed_sessions=len(oos_sessions),
        fold_count=len(oos_sessions),
        trade_count=metrics.trade_count,
        side_cost_bps=metrics.side_cost_bps,
        gross_average_return=(
            None if not trades else sum(trade.gross_return for trade in trades) / len(trades)
        ),
        average_return=metrics.average_return,
        profit_factor=metrics.profit_factor,
        cumulative_return=metrics.cumulative_return,
        max_drawdown=metrics.max_drawdown,
        mean_ci_low=metrics.mean_ci_low,
        mean_ci_high=metrics.mean_ci_high,
        peak_rss_gib=_require_rss(request.rss_limit_gib),
        bootstrap_samples=request.bootstrap_samples,
        bootstrap_seed=INTRADAY_BOOTSTRAP_SEED,
        session_outcomes=outcomes,
    )


def validate_generated_intraday_evaluation_scope(
    source_path: Path | None,
    rss_limit_gib: float,
) -> None:
    if not 0.0 < rss_limit_gib <= MAX_GENERATED_INTRADAY_RSS_GIB:
        raise GeneratedIntradayEvaluationError("rss_limit_must_not_exceed_10_gib")
    if source_path is not None and _FORBIDDEN_FULL_UNIVERSE_DIRECTORY in source_path.parts:
        raise GeneratedIntradayEvaluationError("full_universe_input_forbidden")


def _session_outcome(
    session_date: dt.date,
    trades: tuple[PaperTrade, ...],
    side_cost_bps: int,
) -> IntradaySessionOutcome:
    selected = tuple(
        trade for trade in trades if trade.exit_at.astimezone(NEW_YORK).date() == session_date
    )
    return IntradaySessionOutcome(
        session_date=session_date,
        gross_trade_returns=tuple(trade.gross_return for trade in selected),
        net_trade_returns=tuple(net_return(trade, side_cost_bps) for trade in selected),
    )


def _require_rss(limit_gib: float) -> float:
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    bytes_used = peak if sys.platform == "darwin" else peak * 1024.0
    rss_gib = bytes_used / (1024.0**3)
    if rss_gib >= limit_gib:
        raise GeneratedIntradayEvaluationError("rss_limit_reached")
    return rss_gib
