from __future__ import annotations

import csv
import datetime as dt
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Final, override

from trading_agent.market_risk import MARKET_RISK_HEADER, RiskRejectReason

PORTFOLIO_LIMIT: Final = 10
LEGACY_MARKET_RISK_HEADER: Final = MARKET_RISK_HEADER[:-3]


class RiskScreenFormatError(ValueError):
    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(path, detail)
        self.path = path
        self.detail = detail

    @override
    def __str__(self) -> str:
        return f"위험 판정 CSV 형식 오류 ({self.path}): {self.detail}"


@dataclass(frozen=True, slots=True)
class RiskCandidate:
    observed_at: dt.datetime
    exchange: str
    symbol: str
    reason: str
    change_pct: float
    price: float
    bid: float
    ask: float
    spread_bps: float
    dollar_volume: float
    volume: int | None = None
    average_daily_volume: int | None = None


@dataclass(frozen=True, slots=True)
class RiskSensitivityConfig:
    max_spread_bps: float
    slippage_per_side_bps: float
    max_round_trip_cost_bps: float


@dataclass(frozen=True, slots=True)
class RiskSensitivitySummary:
    config: RiskSensitivityConfig
    snapshot_count: int
    candidate_count: int
    hard_excluded_count: int
    cost_eligible_count: int
    selected_count: int
    retention_rate: float


@dataclass(frozen=True, slots=True)
class RiskSensitivitySelection:
    config: RiskSensitivityConfig
    observed_at: dt.datetime
    rank: int
    exchange: str
    symbol: str
    change_pct: float
    spread_bps: float
    estimated_round_trip_cost_bps: float
    dollar_volume: float


@dataclass(frozen=True, slots=True)
class RiskSensitivityResult:
    summaries: tuple[RiskSensitivitySummary, ...]
    selections: tuple[RiskSensitivitySelection, ...]


def adjacent_risk_configs() -> tuple[RiskSensitivityConfig, ...]:
    return tuple(
        RiskSensitivityConfig(spread, slippage, round_trip)
        for spread, slippage, round_trip in product(
            (80.0, 100.0, 120.0),
            (10.0, 20.0, 30.0),
            (100.0, 140.0, 180.0),
        )
    )


def load_risk_candidates(paths: Iterable[Path]) -> tuple[RiskCandidate, ...]:
    candidates: list[RiskCandidate] = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            header = tuple(reader.fieldnames or ())
            if header not in (LEGACY_MARKET_RISK_HEADER, MARKET_RISK_HEADER):
                raise RiskScreenFormatError(path, f"헤더가 예상과 다릅니다: {reader.fieldnames!r}")
            for line_number, row in enumerate(reader, start=2):
                try:
                    candidates.append(_parse_candidate(row))
                except (KeyError, TypeError, ValueError) as error:
                    raise RiskScreenFormatError(path, f"{line_number}행: {error}") from error
    return tuple(candidates)


def analyze_risk_sensitivity(
    candidates: tuple[RiskCandidate, ...],
    configs: tuple[RiskSensitivityConfig, ...] | None = None,
) -> RiskSensitivityResult:
    tested_configs = adjacent_risk_configs() if configs is None else configs
    snapshots: dict[dt.datetime, list[RiskCandidate]] = defaultdict(list)
    for candidate in candidates:
        snapshots[candidate.observed_at].append(candidate)
    summaries: list[RiskSensitivitySummary] = []
    selections: list[RiskSensitivitySelection] = []
    for config in tested_configs:
        hard_excluded_count = 0
        eligible_count = 0
        selected_count = 0
        for observed_at in sorted(snapshots):
            rows = snapshots[observed_at]
            hard_excluded_count += sum(_is_hard_excluded(row) for row in rows)
            eligible = sorted(
                (row for row in rows if _is_cost_eligible(row, config)),
                key=lambda row: (-row.change_pct, -row.dollar_volume, row.symbol),
            )
            eligible_count += len(eligible)
            chosen = eligible[:PORTFOLIO_LIMIT]
            selected_count += len(chosen)
            selections.extend(
                _selection(config, observed_at, rank, row)
                for rank, row in enumerate(chosen, start=1)
            )
        candidate_count = len(candidates)
        summaries.append(
            RiskSensitivitySummary(
                config,
                len(snapshots),
                candidate_count,
                hard_excluded_count,
                eligible_count,
                selected_count,
                eligible_count / candidate_count if candidate_count else 0.0,
            )
        )
    return RiskSensitivityResult(tuple(summaries), tuple(selections))


def write_risk_sensitivity(
    output_dir: Path,
    result: RiskSensitivityResult,
    source_paths: tuple[Path, ...],
) -> None:
    from trading_agent.risk_sensitivity_report import write_risk_sensitivity_report

    write_risk_sensitivity_report(output_dir, result, source_paths)


def _parse_candidate(row: dict[str, str]) -> RiskCandidate:
    return RiskCandidate(
        dt.datetime.fromisoformat(row["observed_at"]),
        row["exchange"],
        row["symbol"],
        row["reason"],
        float(row["change_pct"]),
        float(row["price"]),
        float(row["bid"]),
        float(row["ask"]),
        float(row["spread_bps"]),
        float(row["dollar_volume"]),
        _optional_int(row.get("volume")),
        _optional_int(row.get("average_daily_volume")),
    )


def _optional_int(value: str | None) -> int | None:
    return None if value in (None, "") else int(value)


def _is_hard_excluded(row: RiskCandidate) -> bool:
    return (
        row.reason == RiskRejectReason.ACTIVE_HALT.value
        or row.bid <= 0.0
        or row.ask <= 0.0
        or row.ask < row.bid
        or not math.isfinite(row.spread_bps)
    )


def _is_cost_eligible(row: RiskCandidate, config: RiskSensitivityConfig) -> bool:
    if _is_hard_excluded(row):
        return False
    return (
        row.spread_bps <= config.max_spread_bps
        and row.spread_bps + config.slippage_per_side_bps * 2.0
        <= config.max_round_trip_cost_bps
    )


def _selection(
    config: RiskSensitivityConfig,
    observed_at: dt.datetime,
    rank: int,
    row: RiskCandidate,
) -> RiskSensitivitySelection:
    return RiskSensitivitySelection(
        config,
        observed_at,
        rank,
        row.exchange,
        row.symbol,
        row.change_pct,
        row.spread_bps,
        row.spread_bps + config.slippage_per_side_bps * 2.0,
        row.dollar_volume,
    )
