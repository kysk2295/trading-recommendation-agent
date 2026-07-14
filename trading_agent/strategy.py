from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Final, final
from zoneinfo import ZoneInfo

from trading_agent.models import BarInput, MomentumCandidate, StrategySignal

NEW_YORK: Final = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class OrbConfig:
    range_minutes: int = 5
    breakout_buffer_bps: float = 5.0
    volume_multiplier: float = 1.5


class _OpeningRangeState:
    __slots__ = ("bars", "emitted")

    def __init__(self) -> None:
        self.bars: dict[dt.datetime, BarInput] = {}
        self.emitted = False


@final
class OpeningRangeBreakout:
    name: str = "opening_range_breakout"

    def __init__(self, config: OrbConfig) -> None:
        self.config = config
        self._ranges: dict[tuple[str, dt.date], _OpeningRangeState] = {}

    def observe(self, bar: BarInput, candidate: MomentumCandidate | None) -> StrategySignal | None:
        exchange_time = bar.timestamp.astimezone(NEW_YORK).replace(second=0, microsecond=0)
        key = (bar.symbol, exchange_time.date())
        opening = self._ranges.setdefault(key, _OpeningRangeState())
        session_open = dt.datetime.combine(exchange_time.date(), dt.time(9, 30), tzinfo=NEW_YORK)
        range_end = session_open + dt.timedelta(minutes=self.config.range_minutes)
        session_close = dt.datetime.combine(exchange_time.date(), dt.time(16, 0), tzinfo=NEW_YORK)
        signal_at = exchange_time + dt.timedelta(minutes=1)
        if exchange_time < session_open or exchange_time >= session_close:
            return None
        if exchange_time < range_end:
            opening.bars[exchange_time] = bar
            return None
        if (
            candidate is None
            or opening.emitted
            or len(opening.bars) != self.config.range_minutes
            or signal_at >= session_close
        ):
            return None
        range_high = max(item.high for item in opening.bars.values())
        range_low = min(item.low for item in opening.bars.values())
        volume_confirmed = bar.volume >= (
            statistics.fmean(item.volume for item in opening.bars.values()) * self.config.volume_multiplier
        )
        if bar.close <= range_high or not volume_confirmed:
            return None
        opening.emitted = True
        entry = range_high * (1.0 + self.config.breakout_buffer_bps / 10_000.0)
        return StrategySignal(
            bar.symbol,
            signal_at,
            self.name,
            entry,
            range_low,
            (
                f"ORB {self.config.range_minutes}분 상단 돌파, "
                f"RVOL {candidate.relative_volume:.2f}, 갭 {candidate.gap_pct:.2%}"
            ),
        )
