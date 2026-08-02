from __future__ import annotations

import datetime as dt
import math
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.metrics import day_block_bootstrap_interval
from trading_agent.strategy_factory import StrategyMode

INTRADAY_BOOTSTRAP_SEED: Final = 20_260_722


class IntradaySessionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_date: dt.date
    gross_trade_returns: tuple[float, ...]
    net_trade_returns: tuple[float, ...]

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        values = (*self.gross_trade_returns, *self.net_trade_returns)
        if (
            len(self.gross_trade_returns) != len(self.net_trade_returns)
            or len(self.gross_trade_returns) > 100_000
            or any(not math.isfinite(value) or value <= -1.0 for value in values)
            or any(net > gross for gross, net in zip(self.gross_trade_returns, self.net_trade_returns, strict=True))
        ):
            raise ValueError("invalid intraday session outcome")
        return self

    @property
    def trade_count(self) -> int:
        return len(self.net_trade_returns)


class IntradayWalkForwardResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1, 2] = 2
    strategy: StrategyMode
    observed_sessions: int = Field(ge=1)
    fold_count: int = Field(ge=1)
    trade_count: int = Field(ge=0)
    side_cost_bps: int = Field(ge=20, le=100)
    gross_average_return: float | None
    average_return: float | None
    profit_factor: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None
    peak_rss_gib: float = Field(ge=0.0, le=9.5)
    bootstrap_samples: int | None = None
    bootstrap_seed: int | None = None
    session_outcomes: tuple[IntradaySessionOutcome, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.schema_version == 1:
            if self.bootstrap_samples is not None or self.bootstrap_seed is not None or self.session_outcomes:
                raise ValueError("legacy intraday result cannot contain outcome trace")
            return self
        outcomes = self.session_outcomes
        dates = tuple(item.session_date for item in outcomes)
        gross = tuple(value for item in outcomes for value in item.gross_trade_returns)
        net = tuple(value for item in outcomes for value in item.net_trade_returns)
        interval = (
            (None, None)
            if self.bootstrap_samples is None or self.bootstrap_seed is None
            else day_block_bootstrap_interval(
                tuple(item.net_trade_returns for item in outcomes if item.net_trade_returns),
                self.bootstrap_samples,
                self.bootstrap_seed,
            )
        )
        if (
            self.bootstrap_samples is None
            or not 100 <= self.bootstrap_samples <= 5_000
            or self.bootstrap_seed is None
            or not 0 <= self.bootstrap_seed <= 2**32 - 1
            or len(outcomes) != self.observed_sessions
            or self.fold_count != self.observed_sessions
            or dates != tuple(sorted(set(dates)))
            or len(net) != self.trade_count
            or not _returns_match_cost(gross, net, self.side_cost_bps)
            or not _optional_close(self.gross_average_return, _mean(gross))
            or not _optional_close(self.average_return, _mean(net))
            or not _optional_close(self.profit_factor, _profit_factor(net))
            or not _optional_close(self.cumulative_return, _cumulative_return(net))
            or not _optional_close(self.max_drawdown, _max_drawdown(net))
            or not _optional_close(self.mean_ci_low, interval[0])
            or not _optional_close(self.mean_ci_high, interval[1])
        ):
            raise ValueError("invalid intraday walk-forward outcome trace")
        return self


class GeneratedIntradayWalkForwardResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[3] = 3
    strategy: Literal["generated_python"] = "generated_python"
    strategy_version: str
    strategy_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    signal_stream_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_sessions: int = Field(ge=1)
    fold_count: int = Field(ge=1)
    trade_count: int = Field(ge=0)
    side_cost_bps: int = Field(ge=20, le=100)
    gross_average_return: float | None
    average_return: float | None
    profit_factor: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None
    peak_rss_gib: float = Field(ge=0.0, le=9.5)
    bootstrap_samples: int
    bootstrap_seed: int
    session_outcomes: tuple[IntradaySessionOutcome, ...]

    @model_validator(mode="after")
    def validate_generated_identity(self) -> Self:
        if (
            self.strategy_version != f"generated-python:{self.strategy_artifact_id}"
            or not self.signal_stream_sha256
        ):
            raise ValueError("invalid generated walk-forward identity")
        _ = IntradayWalkForwardResult(
            schema_version=2,
            strategy=StrategyMode.VWAP_RECLAIM,
            observed_sessions=self.observed_sessions,
            fold_count=self.fold_count,
            trade_count=self.trade_count,
            side_cost_bps=self.side_cost_bps,
            gross_average_return=self.gross_average_return,
            average_return=self.average_return,
            profit_factor=self.profit_factor,
            cumulative_return=self.cumulative_return,
            max_drawdown=self.max_drawdown,
            mean_ci_low=self.mean_ci_low,
            mean_ci_high=self.mean_ci_high,
            peak_rss_gib=self.peak_rss_gib,
            bootstrap_samples=self.bootstrap_samples,
            bootstrap_seed=self.bootstrap_seed,
            session_outcomes=self.session_outcomes,
        )
        return self


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _profit_factor(values: tuple[float, ...]) -> float | None:
    losses = tuple(value for value in values if value < 0.0)
    if not values or not losses:
        return None
    return sum(value for value in values if value > 0.0) / abs(sum(losses))


def _cumulative_return(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    equity = 1.0
    for value in values:
        equity *= 1.0 + value
    return equity - 1.0


def _max_drawdown(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    equity = peak = 1.0
    maximum = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        maximum = min(maximum, equity / peak - 1.0)
    return maximum


def _optional_close(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)


def _returns_match_cost(
    gross: tuple[float, ...],
    net: tuple[float, ...],
    side_cost_bps: int,
) -> bool:
    cost = side_cost_bps / 10_000.0
    expected = tuple((1.0 + value) * (1.0 - cost) / (1.0 + cost) - 1.0 for value in gross)
    return all(
        math.isclose(actual, target, rel_tol=1e-12, abs_tol=2e-12) for actual, target in zip(net, expected, strict=True)
    )


__all__ = (
    "INTRADAY_BOOTSTRAP_SEED",
    "GeneratedIntradayWalkForwardResult",
    "IntradaySessionOutcome",
    "IntradayWalkForwardResult",
)
