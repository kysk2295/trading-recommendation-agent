from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never, final
from zoneinfo import ZoneInfo

from trading_agent.models import BarInput, MomentumCandidate, StrategySignal

NEW_YORK: Final = ZoneInfo("America/New_York")


class VwapReclaimPhase(StrEnum):
    SEEK_IMPULSE = "seek_impulse"
    SEEK_PULLBACK = "seek_pullback"
    SEEK_RECLAIM = "seek_reclaim"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class VwapReclaimConfig:
    min_extension_pct: float = 0.01
    touch_tolerance_bps: float = 20.0
    reclaim_buffer_bps: float = 5.0
    volume_multiplier: float = 1.2
    max_reclaim_bars: int = 5


class _VwapReclaimState:
    __slots__ = (
        "bars_after_pullback",
        "cumulative_price_volume",
        "cumulative_volume",
        "phase",
        "pullback_high",
        "pullback_low",
        "pullback_volume",
        "pullback_vwap",
    )

    def __init__(self) -> None:
        self.cumulative_price_volume = 0.0
        self.cumulative_volume = 0
        self.phase = VwapReclaimPhase.SEEK_IMPULSE
        self.pullback_high = 0.0
        self.pullback_low = 0.0
        self.pullback_volume = 0
        self.pullback_vwap = 0.0
        self.bars_after_pullback = 0


@final
class FirstPullbackVwapReclaim:
    name: str = "first_pullback_vwap_reclaim"

    def __init__(self, config: VwapReclaimConfig) -> None:
        self.config = config
        self._states: dict[tuple[str, dt.date], _VwapReclaimState] = {}

    def observe(
        self,
        bar: BarInput,
        candidate: MomentumCandidate | None,
    ) -> StrategySignal | None:
        exchange_time = bar.timestamp.astimezone(NEW_YORK).replace(
            second=0,
            microsecond=0,
        )
        session_open = dt.datetime.combine(
            exchange_time.date(),
            dt.time(9, 30),
            tzinfo=NEW_YORK,
        )
        session_close = dt.datetime.combine(
            exchange_time.date(),
            dt.time(16),
            tzinfo=NEW_YORK,
        )
        signal_at = exchange_time + dt.timedelta(minutes=1)
        if (
            exchange_time < session_open
            or exchange_time >= session_close
            or bar.volume <= 0
        ):
            return None
        state = self._states.setdefault(
            (bar.symbol, exchange_time.date()),
            _VwapReclaimState(),
        )
        typical_price = (bar.high + bar.low + bar.close) / 3.0
        state.cumulative_price_volume += typical_price * bar.volume
        state.cumulative_volume += bar.volume
        vwap = state.cumulative_price_volume / state.cumulative_volume
        tolerance = self.config.touch_tolerance_bps / 10_000.0
        match state.phase:
            case VwapReclaimPhase.SEEK_IMPULSE:
                if bar.close >= vwap * (1.0 + self.config.min_extension_pct):
                    state.phase = VwapReclaimPhase.SEEK_PULLBACK
                return None
            case VwapReclaimPhase.SEEK_PULLBACK:
                if (
                    bar.low <= vwap * (1.0 + tolerance)
                    and bar.close >= vwap * (1.0 - tolerance)
                ):
                    state.phase = VwapReclaimPhase.SEEK_RECLAIM
                    state.pullback_high = bar.high
                    state.pullback_low = bar.low
                    state.pullback_volume = bar.volume
                    state.pullback_vwap = vwap
                return None
            case VwapReclaimPhase.SEEK_RECLAIM:
                state.bars_after_pullback += 1
                if (
                    bar.close < vwap * (1.0 - tolerance)
                    or state.bars_after_pullback > self.config.max_reclaim_bars
                ):
                    state.phase = VwapReclaimPhase.DONE
                    return None
                volume_confirmed = bar.volume >= (
                    state.pullback_volume * self.config.volume_multiplier
                )
                reclaimed = (
                    bar.close > vwap * (1.0 + self.config.reclaim_buffer_bps / 10_000.0)
                    and bar.close > bar.open
                    and bar.high > state.pullback_high
                    and vwap >= state.pullback_vwap
                )
                if (
                    candidate is None
                    or not volume_confirmed
                    or not reclaimed
                    or signal_at >= session_close
                ):
                    return None
                state.phase = VwapReclaimPhase.DONE
                entry = bar.high * (
                    1.0 + self.config.reclaim_buffer_bps / 10_000.0
                )
                return StrategySignal(
                    bar.symbol,
                    signal_at,
                    self.name,
                    entry,
                    state.pullback_low,
                    (
                        f"첫 눌림목 VWAP 재돌파 {vwap:.4f}, "
                        f"RVOL {candidate.relative_volume:.2f}"
                    ),
                )
            case VwapReclaimPhase.DONE:
                return None
            case unreachable:
                assert_never(unreachable)
