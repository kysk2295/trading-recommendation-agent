from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never, final
from zoneinfo import ZoneInfo

from trading_agent.models import BarInput, MomentumCandidate, StrategySignal

NEW_YORK: Final = ZoneInfo("America/New_York")


class GapDrivePhase(StrEnum):
    SEEK_OPEN = "seek_open"
    OBSERVE_OPENING = "observe_opening"
    DONE = "done"


class GapDriveClassification(StrEnum):
    CONTINUATION = "continuation"
    GAP_FAILURE = "gap_failure"
    NEUTRAL = "neutral"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class GapAndGoConfig:
    opening_minutes: int = 5
    min_gap_pct: float = 0.04
    min_gap_retention: float = 0.50
    entry_buffer_bps: float = 5.0


class _GapDriveState:
    __slots__ = (
        "bars",
        "classification",
        "cumulative_price_volume",
        "cumulative_volume",
        "opening_low",
        "opening_price",
        "phase",
        "prior_close",
    )

    def __init__(self) -> None:
        self.phase = GapDrivePhase.SEEK_OPEN
        self.classification: GapDriveClassification | None = None
        self.bars = 0
        self.opening_price = 0.0
        self.opening_low = 0.0
        self.prior_close = 0.0
        self.cumulative_price_volume = 0.0
        self.cumulative_volume = 0

    def add_bar(self, bar: BarInput) -> None:
        self.bars += 1
        self.opening_low = (
            bar.low if self.bars == 1 else min(self.opening_low, bar.low)
        )
        typical_price = (bar.high + bar.low + bar.close) / 3.0
        self.cumulative_price_volume += typical_price * bar.volume
        self.cumulative_volume += bar.volume


@final
class FiveMinuteGapHold:
    name: str = "five_minute_gap_hold"

    def __init__(self, config: GapAndGoConfig) -> None:
        self.config = config
        self._states: dict[tuple[str, dt.date], _GapDriveState] = {}

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
        if (
            exchange_time < session_open
            or exchange_time >= session_close
            or bar.volume <= 0
        ):
            return None
        state = self._states.setdefault(
            (bar.symbol, exchange_time.date()),
            _GapDriveState(),
        )
        match state.phase:
            case GapDrivePhase.SEEK_OPEN:
                if exchange_time != session_open:
                    state.classification = GapDriveClassification.INELIGIBLE
                    state.phase = GapDrivePhase.DONE
                    return None
                gap_pct = bar.open / bar.prior_close - 1.0
                if gap_pct < self.config.min_gap_pct:
                    state.classification = GapDriveClassification.INELIGIBLE
                    state.phase = GapDrivePhase.DONE
                    return None
                state.opening_price = bar.open
                state.prior_close = bar.prior_close
                state.add_bar(bar)
                state.phase = GapDrivePhase.OBSERVE_OPENING
                if state.bars < self.config.opening_minutes:
                    return None
                return self._classify(state, bar, candidate)
            case GapDrivePhase.OBSERVE_OPENING:
                expected = session_open + dt.timedelta(minutes=state.bars)
                if exchange_time != expected:
                    state.classification = GapDriveClassification.INELIGIBLE
                    state.phase = GapDrivePhase.DONE
                    return None
                state.add_bar(bar)
                if state.bars < self.config.opening_minutes:
                    return None
                return self._classify(state, bar, candidate)
            case GapDrivePhase.DONE:
                return None
            case unreachable:
                assert_never(unreachable)

    def classification(
        self,
        symbol: str,
        session_date: dt.date,
    ) -> GapDriveClassification | None:
        state = self._states.get((symbol, session_date))
        return None if state is None else state.classification

    def _classify(
        self,
        state: _GapDriveState,
        bar: BarInput,
        candidate: MomentumCandidate | None,
    ) -> StrategySignal | None:
        state.phase = GapDrivePhase.DONE
        gap_size = state.opening_price - state.prior_close
        retained_floor = state.prior_close + gap_size * self.config.min_gap_retention
        if state.opening_low <= state.prior_close or bar.close < retained_floor:
            state.classification = GapDriveClassification.GAP_FAILURE
            return None
        session_vwap = state.cumulative_price_volume / state.cumulative_volume
        if (
            candidate is None
            or bar.close <= state.opening_price
            or bar.close < session_vwap
        ):
            state.classification = GapDriveClassification.NEUTRAL
            return None
        state.classification = GapDriveClassification.CONTINUATION
        signal_at = bar.timestamp.astimezone(NEW_YORK).replace(
            second=0,
            microsecond=0,
        ) + dt.timedelta(minutes=1)
        entry = bar.close * (1.0 + self.config.entry_buffer_bps / 10_000.0)
        return StrategySignal(
            bar.symbol,
            signal_at,
            self.name,
            entry,
            retained_floor,
            (
                f"첫 {self.config.opening_minutes}분 갭 유지·시가·VWAP 상회, "
                f"RVOL {candidate.relative_volume:.2f}"
            ),
        )
