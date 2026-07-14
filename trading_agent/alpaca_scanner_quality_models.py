from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Final, Protocol

PORTFOLIO_LIMIT: Final = 10


@dataclass(frozen=True, slots=True)
class ScannerQualityConfig:
    min_change_pct: float
    min_price: float
    max_price: float
    min_dollar_volume: float
    min_adv_fraction: float


class ScannerCandidate(Protocol):
    @property
    def symbol(self) -> str: ...

    @property
    def price(self) -> float | None: ...

    @property
    def change_pct(self) -> float | None: ...

    @property
    def dollar_volume(self) -> float | None: ...

    @property
    def adv_fraction(self) -> float | None: ...


@dataclass(frozen=True, slots=True)
class ScannerQualityOutcome:
    config: ScannerQualityConfig
    session_date: dt.date
    symbol: str
    rank: int
    bar_count: int
    complete: bool
    entry_at: dt.datetime
    entry: float | None
    return_5m: float | None
    return_15m: float | None
    return_30m: float | None
    eod_return: float | None
    mfe: float | None
    mae: float | None


@dataclass(frozen=True, slots=True)
class AlpacaScannerQualityError(RuntimeError):
    path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def scanner_quality_grid() -> tuple[ScannerQualityConfig, ...]:
    return tuple(
        ScannerQualityConfig(change, 0.5, price, dollars, adv)
        for change, price, dollars, adv in product(
            (0.02, 0.04, 0.06, 0.08),
            (20.0, 50.0, 100.0),
            (250_000.0, 500_000.0, 1_000_000.0),
            (0.01, 0.05, 0.10),
        )
    )


def select_scanner_candidates[ScannerCandidateT: ScannerCandidate](
    rows: tuple[ScannerCandidateT, ...],
    config: ScannerQualityConfig,
    limit: int = PORTFOLIO_LIMIT,
) -> tuple[ScannerCandidateT, ...]:
    eligible = tuple(row for row in rows if scanner_candidate_passes(row, config))
    return tuple(sorted(eligible, key=scanner_candidate_rank))[:limit]


def select_scanner_grid_union(
    rows: tuple[ScannerCandidate, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted({row.symbol for config in scanner_quality_grid() for row in select_scanner_candidates(rows, config)})
    )


def scanner_candidate_passes(
    row: ScannerCandidate,
    config: ScannerQualityConfig,
) -> bool:
    return (
        row.price is not None
        and row.change_pct is not None
        and row.dollar_volume is not None
        and row.adv_fraction is not None
        and config.min_price <= row.price <= config.max_price
        and row.change_pct >= config.min_change_pct
        and row.dollar_volume >= config.min_dollar_volume
        and row.adv_fraction >= config.min_adv_fraction
    )


def scanner_candidate_rank(row: ScannerCandidate) -> tuple[float, float, float, str]:
    return (
        -(row.change_pct or -1.0),
        -(row.adv_fraction or -1.0),
        -(row.dollar_volume or -1.0),
        row.symbol,
    )
