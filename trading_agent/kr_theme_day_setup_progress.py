from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Protocol, Self, assert_never, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2
from trading_agent.kr_price_grid import is_valid_kr_equity_price
from trading_agent.kr_theme_day_signal import KrThemeDaySetup
from trading_agent.signal_contract_models import EvidenceRef, OpportunitySnapshot

SEOUL: Final = ZoneInfo("Asia/Seoul")
_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_SESSION_OPEN: Final = dt.time(9)
_SESSION_CLOSE: Final = dt.time(15, 30)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
MIN_EXTENSION: Final = Decimal("0.01")
TOUCH_TOLERANCE: Final = Decimal("0.002")
RECLAIM_BUFFER: Final = Decimal("0.0005")
VOLUME_MULTIPLIER: Final = Decimal("1.2")
MAX_RECLAIM_BARS: Final = 5


class InvalidKrThemeDaySetupError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day setup input is invalid"


class KrCompletedMinuteBar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    symbol: str
    start_at: dt.datetime
    end_at: dt.datetime
    observed_at: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    trading_value_krw: Decimal
    evidence_ref: EvidenceRef

    @model_validator(mode="after")
    def validate_bar(self) -> Self:
        average_price = self.trading_value_krw / Decimal(self.volume) if self.volume > 0 else Decimal(0)
        local_start = self.start_at.astimezone(SEOUL) if _aware(self.start_at) else self.start_at
        local_end = self.end_at.astimezone(SEOUL) if _aware(self.end_at) else self.end_at
        if (
            not is_kr_instrument_symbol_v2(self.symbol)
            or not all(_aware(value) for value in (self.start_at, self.end_at, self.observed_at))
            or self.end_at - self.start_at != _ONE_MINUTE
            or self.end_at > self.observed_at
            or local_start.second != 0
            or local_start.microsecond != 0
            or local_start.date() != local_end.date()
            or local_start.time() < _SESSION_OPEN
            or local_end.time() > _SESSION_CLOSE
            or not all(_positive(value) for value in (self.open, self.high, self.low, self.close))
            or self.low > min(self.open, self.close)
            or self.high < max(self.open, self.close)
            or type(self.volume) is not int
            or self.volume <= 0
            or not _positive(self.trading_value_krw)
            or not self.low <= average_price <= self.high
            or self.evidence_ref.observed_at != self.observed_at
        ):
            raise InvalidKrThemeDaySetupError
        return self


class KrThemeDaySetupInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity: OpportunitySnapshot
    bars: tuple[KrCompletedMinuteBar, ...]
    producer_strategy_version: str
    evaluated_at: dt.datetime
    max_slippage_bps: Decimal

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if (
            not self.bars
            or _IDENTIFIER.fullmatch(self.producer_strategy_version) is None
            or not _aware(self.evaluated_at)
            or not _positive(self.max_slippage_bps)
        ):
            raise InvalidKrThemeDaySetupError
        return self


class KrThemeDaySetupPhase(StrEnum):
    NO_IMPULSE = "NO_IMPULSE"
    IMPULSE_ONLY = "IMPULSE_ONLY"
    PULLBACK_FOUND = "PULLBACK_FOUND"
    RECLAIM_CONFIRMED = "RECLAIM_CONFIRMED"
    SETUP_EXPIRED = "SETUP_EXPIRED"


class KrThemeDayConditionalSetup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger_rule: str
    trigger_price: Decimal
    stop_price: Decimal
    target_prices: tuple[Decimal, Decimal]
    invalidation_rule: str
    valid_until: dt.datetime
    rationale: str
    evidence_refs: tuple[EvidenceRef, ...]

    @model_validator(mode="after")
    def validate_levels(self) -> Self:
        levels = (self.trigger_price, self.stop_price, *self.target_prices)
        if (
            not all(is_valid_kr_equity_price(price) for price in levels)
            or self.stop_price >= self.trigger_price
            or any(target <= self.trigger_price for target in self.target_prices)
            or not _aware(self.valid_until)
            or not self.evidence_refs
        ):
            raise InvalidKrThemeDaySetupError
        return self


class KrThemeDaySetupAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: KrThemeDaySetupPhase
    reason: str
    evidence_refs: tuple[EvidenceRef, ...]
    conditional: KrThemeDayConditionalSetup | None = None
    setup: KrThemeDaySetup | None = None

    @model_validator(mode="after")
    def validate_phase_payload(self) -> Self:
        match self.phase:
            case (
                KrThemeDaySetupPhase.NO_IMPULSE | KrThemeDaySetupPhase.IMPULSE_ONLY | KrThemeDaySetupPhase.SETUP_EXPIRED
            ):
                valid = self.conditional is None and self.setup is None
            case KrThemeDaySetupPhase.PULLBACK_FOUND:
                valid = self.conditional is not None and self.setup is None
            case KrThemeDaySetupPhase.RECLAIM_CONFIRMED:
                valid = self.conditional is None and self.setup is not None
            case unreachable:
                assert_never(unreachable)
        if not valid or not self.reason or not self.evidence_refs:
            raise InvalidKrThemeDaySetupError
        return self


class KrSetupProgressBar(Protocol):
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    trading_value_krw: Decimal


class KrSetupScanPhase(StrEnum):
    NO_IMPULSE = "NO_IMPULSE"
    IMPULSE_ONLY = "IMPULSE_ONLY"
    PULLBACK_FOUND = "PULLBACK_FOUND"
    RECLAIM_FOUND = "RECLAIM_FOUND"
    SETUP_EXPIRED = "SETUP_EXPIRED"


class _ScanState(StrEnum):
    SEEK_IMPULSE = "seek_impulse"
    SEEK_PULLBACK = "seek_pullback"
    SEEK_RECLAIM = "seek_reclaim"


@dataclass(frozen=True, slots=True)
class KrSetupScan:
    phase: KrSetupScanPhase
    current_vwap: Decimal
    pullback_index: int | None = None
    trigger_index: int | None = None


def scan_kr_theme_day_setup[T: KrSetupProgressBar](bars: Sequence[T]) -> KrSetupScan:
    state = _ScanState.SEEK_IMPULSE
    cumulative_value = Decimal(0)
    cumulative_volume = Decimal(0)
    pullback_index: int | None = None
    pullback_vwap = Decimal(0)
    bars_after_pullback = 0
    current_vwap = Decimal(0)
    for index, bar in enumerate(bars):
        cumulative_value += bar.trading_value_krw
        cumulative_volume += Decimal(bar.volume)
        current_vwap = cumulative_value / cumulative_volume
        match state:
            case _ScanState.SEEK_IMPULSE:
                if bar.close >= current_vwap * (Decimal(1) + MIN_EXTENSION):
                    state = _ScanState.SEEK_PULLBACK
            case _ScanState.SEEK_PULLBACK:
                if _is_pullback(bar, current_vwap):
                    pullback_index = index
                    pullback_vwap = current_vwap
                    state = _ScanState.SEEK_RECLAIM
            case _ScanState.SEEK_RECLAIM:
                bars_after_pullback += 1
                if bar.close < current_vwap * (Decimal(1) - TOUCH_TOLERANCE) or bars_after_pullback > MAX_RECLAIM_BARS:
                    return KrSetupScan(KrSetupScanPhase.SETUP_EXPIRED, current_vwap, pullback_index)
                if pullback_index is not None and _is_reclaim(
                    bar,
                    current_vwap,
                    bars[pullback_index],
                    pullback_vwap,
                ):
                    phase = KrSetupScanPhase.RECLAIM_FOUND if index == len(bars) - 1 else KrSetupScanPhase.SETUP_EXPIRED
                    return KrSetupScan(phase, current_vwap, pullback_index, index)
            case unreachable:
                assert_never(unreachable)
    match state:
        case _ScanState.SEEK_IMPULSE:
            phase = KrSetupScanPhase.NO_IMPULSE
        case _ScanState.SEEK_PULLBACK:
            phase = KrSetupScanPhase.IMPULSE_ONLY
        case _ScanState.SEEK_RECLAIM:
            phase = KrSetupScanPhase.PULLBACK_FOUND
        case unreachable:
            assert_never(unreachable)
    return KrSetupScan(phase, current_vwap, pullback_index)


def _is_pullback(bar: KrSetupProgressBar, vwap: Decimal) -> bool:
    return (
        bar.low <= vwap * (Decimal(1) + TOUCH_TOLERANCE)
        and bar.close >= vwap * (Decimal(1) - TOUCH_TOLERANCE)
        and bar.close <= vwap * (Decimal(1) + TOUCH_TOLERANCE)
    )


def _is_reclaim(
    bar: KrSetupProgressBar,
    vwap: Decimal,
    pullback: KrSetupProgressBar,
    pullback_vwap: Decimal,
) -> bool:
    return (
        Decimal(bar.volume) >= Decimal(pullback.volume) * VOLUME_MULTIPLIER
        and bar.close > vwap * (Decimal(1) + RECLAIM_BUFFER)
        and bar.close > bar.open
        and bar.high > pullback.high
        and vwap >= pullback_vwap
    )


def _positive(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
