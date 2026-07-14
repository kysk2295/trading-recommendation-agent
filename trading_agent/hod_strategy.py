from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never, final
from zoneinfo import ZoneInfo

from trading_agent.models import BarInput, MomentumCandidate, StrategySignal

NEW_YORK: Final = ZoneInfo("America/New_York")


class HodBreakoutPhase(StrEnum):
    SEEK_HOD = "seek_hod"
    SEEK_BASE = "seek_base"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class HodBreakoutConfig:
    min_hod_gain_pct: float = 0.03
    breakout_buffer_bps: float = 5.0
    volume_multiplier: float = 1.5
    min_base_bars: int = 2
    max_base_bars: int = 8
    max_pullback_pct: float = 0.03


class _HodBreakoutState:
    __slots__ = (
        "base_bars",
        "base_low",
        "base_volume",
        "hod",
        "phase",
    )

    def __init__(self) -> None:
        self.phase = HodBreakoutPhase.SEEK_HOD
        self.hod = 0.0
        self.base_bars = 0
        self.base_low = 0.0
        self.base_volume = 0

    def reset_base(self) -> None:
        self.base_bars = 0
        self.base_low = 0.0
        self.base_volume = 0

    def add_base_bar(self, bar: BarInput) -> None:
        self.base_bars += 1
        self.base_low = bar.low if self.base_bars == 1 else min(self.base_low, bar.low)
        self.base_volume += bar.volume


@final
class FirstHodVolumeBreakout:
    name: str = "first_hod_volume_breakout"

    def __init__(self, config: HodBreakoutConfig) -> None:
        self.config = config
        self._states: dict[tuple[str, dt.date], _HodBreakoutState] = {}

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
            _HodBreakoutState(),
        )
        match state.phase:
            case HodBreakoutPhase.SEEK_HOD:
                if bar.high >= bar.prior_close * (
                    1.0 + self.config.min_hod_gain_pct
                ):
                    state.hod = bar.high
                    state.phase = HodBreakoutPhase.SEEK_BASE
                return None
            case HodBreakoutPhase.SEEK_BASE:
                return self._observe_base(
                    state,
                    bar,
                    candidate,
                    signal_at,
                    session_close,
                )
            case HodBreakoutPhase.DONE:
                return None
            case unreachable:
                assert_never(unreachable)

    def _observe_base(
        self,
        state: _HodBreakoutState,
        bar: BarInput,
        candidate: MomentumCandidate | None,
        signal_at: dt.datetime,
        session_close: dt.datetime,
    ) -> StrategySignal | None:
        breakout = state.hod * (
            1.0 + self.config.breakout_buffer_bps / 10_000.0
        )
        if bar.high > state.hod and state.base_bars < self.config.min_base_bars:
            state.hod = bar.high
            state.reset_base()
            return None
        if bar.high >= breakout:
            return self._evaluate_first_breakout(
                state,
                bar,
                candidate,
                signal_at,
                session_close,
                breakout,
            )
        if bar.high > state.hod:
            state.hod = bar.high
            state.reset_base()
            return None
        if bar.low < state.hod * (1.0 - self.config.max_pullback_pct):
            state.phase = HodBreakoutPhase.DONE
            return None
        state.add_base_bar(bar)
        if state.base_bars > self.config.max_base_bars:
            state.phase = HodBreakoutPhase.DONE
        return None

    def _evaluate_first_breakout(
        self,
        state: _HodBreakoutState,
        bar: BarInput,
        candidate: MomentumCandidate | None,
        signal_at: dt.datetime,
        session_close: dt.datetime,
        breakout: float,
    ) -> StrategySignal | None:
        state.phase = HodBreakoutPhase.DONE
        average_base_volume = state.base_volume / state.base_bars
        confirmed = (
            bar.close >= breakout
            and bar.close > bar.open
            and bar.volume >= average_base_volume * self.config.volume_multiplier
        )
        if candidate is None or not confirmed or signal_at >= session_close:
            return None
        entry = bar.high * (1.0 + self.config.breakout_buffer_bps / 10_000.0)
        return StrategySignal(
            bar.symbol,
            signal_at,
            self.name,
            entry,
            state.base_low,
            (
                f"첫 HOD {state.hod:.4f} 돌파·거래량 재확대, "
                f"RVOL {candidate.relative_volume:.2f}"
            ),
        )
