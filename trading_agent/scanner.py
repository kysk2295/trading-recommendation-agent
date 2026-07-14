from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, final
from zoneinfo import ZoneInfo

from trading_agent.models import BarInput, MomentumCandidate

NEW_YORK: Final = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class ScannerConfig:
    min_gap_pct: float = 0.04
    min_change_pct: float = 0.04
    min_relative_volume: float = 2.0
    min_price: float = 1.0
    max_price: float = 200.0
    min_dollar_volume: float = 500_000.0
    max_spread_bps: float = 100.0


@final
class MomentumScanner:
    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self._cumulative_volume: dict[tuple[str, object], int] = {}
        self._bar_count: dict[tuple[str, object], int] = {}
        self._regular_open: dict[tuple[str, object], float] = {}

    def observe(self, bar: BarInput) -> MomentumCandidate | None:
        exchange_time = bar.timestamp.astimezone(NEW_YORK)
        key = (bar.symbol, exchange_time.date())
        if exchange_time.time().replace(second=0, microsecond=0) == dt.time(9, 30):
            self._regular_open[key] = bar.open
        cumulative = self._cumulative_volume.get(key, 0) + bar.volume
        count = self._bar_count.get(key, 0) + 1
        self._cumulative_volume[key] = cumulative
        self._bar_count[key] = count
        expected = max(1.0, bar.average_daily_volume * count / 390.0)
        relative_volume = cumulative / expected
        gap_pct = self._regular_open.get(key, bar.open) / bar.prior_close - 1.0
        change_pct = bar.close / bar.prior_close - 1.0
        dollar_volume = cumulative * bar.close
        config = self.config
        if not (
            gap_pct >= config.min_gap_pct
            and change_pct >= config.min_change_pct
            and relative_volume >= config.min_relative_volume
            and config.min_price <= bar.close <= config.max_price
            and dollar_volume >= config.min_dollar_volume
            and bar.spread_bps <= config.max_spread_bps
        ):
            return None
        return MomentumCandidate(
            bar.symbol,
            bar.timestamp,
            bar.close,
            gap_pct,
            change_pct,
            relative_volume,
            dollar_volume,
            bar.spread_bps,
            bar.catalyst,
        )
