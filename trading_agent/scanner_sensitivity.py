from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Final

from trading_agent.market_risk import PORTFOLIO_LIMIT_REASON
from trading_agent.risk_sensitivity import RiskCandidate

PORTFOLIO_LIMIT: Final = 10


@dataclass(frozen=True, slots=True)
class ScannerSensitivityConfig:
    min_change_pct: float
    max_price: float
    min_dollar_volume: float
    min_volume_to_adv: float


@dataclass(frozen=True, slots=True)
class ScannerSensitivitySummary:
    config: ScannerSensitivityConfig
    snapshot_count: int
    candidate_count: int
    risk_eligible_count: int
    feature_available_count: int
    feature_missing_count: int
    threshold_eligible_count: int
    selected_count: int
    retention_rate: float


@dataclass(frozen=True, slots=True)
class ScannerSensitivitySelection:
    config: ScannerSensitivityConfig
    observed_at: dt.datetime
    rank: int
    exchange: str
    symbol: str
    change_pct: float
    price: float
    dollar_volume: float
    volume_to_adv: float


@dataclass(frozen=True, slots=True)
class ScannerSensitivityResult:
    summaries: tuple[ScannerSensitivitySummary, ...]
    selections: tuple[ScannerSensitivitySelection, ...]


def scanner_sensitivity_grid() -> tuple[ScannerSensitivityConfig, ...]:
    return tuple(
        ScannerSensitivityConfig(change, price, dollars, volume_to_adv)
        for change, price, dollars, volume_to_adv in product(
            (0.04, 0.06, 0.08),
            (20.0, 50.0, 200.0),
            (500_000.0, 1_000_000.0, 2_000_000.0),
            (0.05, 0.10, 0.20),
        )
    )


def analyze_scanner_sensitivity(
    candidates: tuple[RiskCandidate, ...],
    configs: tuple[ScannerSensitivityConfig, ...] | None = None,
) -> ScannerSensitivityResult:
    tested_configs = scanner_sensitivity_grid() if configs is None else configs
    snapshots: dict[dt.datetime, list[RiskCandidate]] = defaultdict(list)
    for candidate in candidates:
        snapshots[candidate.observed_at].append(candidate)
    summaries: list[ScannerSensitivitySummary] = []
    selections: list[ScannerSensitivitySelection] = []
    for config in tested_configs:
        risk_eligible_count = 0
        feature_available_count = 0
        threshold_eligible_count = 0
        selected_count = 0
        for observed_at in sorted(snapshots):
            risk_eligible = tuple(row for row in snapshots[observed_at] if _is_risk_eligible(row))
            feature_available = tuple(
                (row, volume_to_adv)
                for row in risk_eligible
                if (volume_to_adv := _volume_to_adv(row)) is not None
            )
            eligible = sorted(
                (
                    (row, volume_to_adv)
                    for row, volume_to_adv in feature_available
                    if _passes_thresholds(row, volume_to_adv, config)
                ),
                key=lambda item: (-item[0].change_pct, -item[0].dollar_volume, item[0].symbol),
            )
            chosen = eligible[:PORTFOLIO_LIMIT]
            risk_eligible_count += len(risk_eligible)
            feature_available_count += len(feature_available)
            threshold_eligible_count += len(eligible)
            selected_count += len(chosen)
            selections.extend(
                ScannerSensitivitySelection(
                    config,
                    observed_at,
                    rank,
                    row.exchange,
                    row.symbol,
                    row.change_pct,
                    row.price,
                    row.dollar_volume,
                    volume_to_adv,
                )
                for rank, (row, volume_to_adv) in enumerate(chosen, start=1)
            )
        candidate_count = len(candidates)
        summaries.append(
            ScannerSensitivitySummary(
                config,
                len(snapshots),
                candidate_count,
                risk_eligible_count,
                feature_available_count,
                risk_eligible_count - feature_available_count,
                threshold_eligible_count,
                selected_count,
                threshold_eligible_count / risk_eligible_count if risk_eligible_count else 0.0,
            )
        )
    return ScannerSensitivityResult(tuple(summaries), tuple(selections))


def write_scanner_sensitivity(output_dir: Path, result: ScannerSensitivityResult) -> None:
    from trading_agent.scanner_sensitivity_report import write_scanner_sensitivity_report

    write_scanner_sensitivity_report(output_dir, result)


def _is_risk_eligible(row: RiskCandidate) -> bool:
    return row.reason in ("", PORTFOLIO_LIMIT_REASON)


def _volume_to_adv(row: RiskCandidate) -> float | None:
    if row.volume is None or row.average_daily_volume is None or row.average_daily_volume <= 0:
        return None
    return row.volume / row.average_daily_volume


def _passes_thresholds(
    row: RiskCandidate,
    volume_to_adv: float,
    config: ScannerSensitivityConfig,
) -> bool:
    return (
        row.change_pct >= config.min_change_pct
        and row.price <= config.max_price
        and row.dollar_volume >= config.min_dollar_volume
        and volume_to_adv >= config.min_volume_to_adv
    )
