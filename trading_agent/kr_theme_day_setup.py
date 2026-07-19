from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Final, Literal, Self, assert_never, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2
from trading_agent.kr_theme_day_signal import KrThemeDaySetup
from trading_agent.kr_theme_lane import KR_THEME_OPPORTUNITY_LANE
from trading_agent.signal_contract_models import EvidenceRef, OpportunitySnapshot, TradeTarget

SEOUL: Final = ZoneInfo("Asia/Seoul")
_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_SESSION_OPEN: Final = dt.time(9)
_SESSION_CLOSE: Final = dt.time(15, 30)
_SETUP_VALIDITY: Final = dt.timedelta(seconds=30)
_MAX_EVALUATION_DELAY: Final = dt.timedelta(seconds=30)
_MIN_EXTENSION: Final = Decimal("0.01")
_TOUCH_TOLERANCE: Final = Decimal("0.002")
_RECLAIM_BUFFER: Final = Decimal("0.0005")
_VOLUME_MULTIPLIER: Final = Decimal("1.2")
_MAX_RECLAIM_BARS: Final = 5
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


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


class _Phase(StrEnum):
    SEEK_IMPULSE = "seek_impulse"
    SEEK_PULLBACK = "seek_pullback"
    SEEK_RECLAIM = "seek_reclaim"
    DONE = "done"


def derive_kr_theme_day_setup(source: KrThemeDaySetupInput) -> KrThemeDaySetup | None:
    request = _validated_input(source)
    _require_point_in_time_lineage(request)
    phase = _Phase.SEEK_IMPULSE
    cumulative_value = Decimal(0)
    cumulative_volume = Decimal(0)
    pullback: KrCompletedMinuteBar | None = None
    pullback_vwap = Decimal(0)
    bars_after_pullback = 0
    latest = request.bars[-1]
    for bar in request.bars:
        cumulative_value += bar.trading_value_krw
        cumulative_volume += Decimal(bar.volume)
        vwap = cumulative_value / cumulative_volume
        match phase:
            case _Phase.SEEK_IMPULSE:
                if bar.close >= vwap * (Decimal(1) + _MIN_EXTENSION):
                    phase = _Phase.SEEK_PULLBACK
            case _Phase.SEEK_PULLBACK:
                if (
                    bar.low <= vwap * (Decimal(1) + _TOUCH_TOLERANCE)
                    and bar.close >= vwap * (Decimal(1) - _TOUCH_TOLERANCE)
                    and bar.close <= vwap * (Decimal(1) + _TOUCH_TOLERANCE)
                ):
                    pullback = bar
                    pullback_vwap = vwap
                    phase = _Phase.SEEK_RECLAIM
            case _Phase.SEEK_RECLAIM:
                bars_after_pullback += 1
                if bar.close < vwap * (Decimal(1) - _TOUCH_TOLERANCE) or bars_after_pullback > _MAX_RECLAIM_BARS:
                    phase = _Phase.DONE
                elif pullback is not None and _is_reclaim(bar, vwap, pullback, pullback_vwap):
                    return _build_setup(request, bar, pullback) if bar is latest else None
            case _Phase.DONE:
                return None
            case unreachable:
                assert_never(unreachable)
    return None


def _validated_input(source: KrThemeDaySetupInput) -> KrThemeDaySetupInput:
    try:
        return KrThemeDaySetupInput.model_validate(source.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDaySetupError from None


def _require_point_in_time_lineage(request: KrThemeDaySetupInput) -> None:
    opportunity = request.opportunity
    bars = request.bars
    first_local = bars[0].start_at.astimezone(SEOUL)
    evidence_ids = tuple(bar.evidence_ref.canonical_id for bar in bars)
    valid = (
        opportunity.strategy_lane == KR_THEME_OPPORTUNITY_LANE
        and opportunity.candidates[0].symbol == bars[0].symbol
        and opportunity.observed_at <= bars[-1].observed_at
        and request.evaluated_at < opportunity.valid_until
        and bars[-1].observed_at <= request.evaluated_at
        and request.evaluated_at - bars[-1].observed_at <= _MAX_EVALUATION_DELAY
        and first_local.time() == _SESSION_OPEN
        and len(evidence_ids) == len(set(evidence_ids))
    )
    contiguous = all(
        current.symbol == bars[0].symbol
        and current.start_at == previous.end_at
        and current.observed_at >= previous.observed_at
        for previous, current in pairwise(bars)
    )
    if not valid or not contiguous:
        raise InvalidKrThemeDaySetupError


def _is_reclaim(
    bar: KrCompletedMinuteBar,
    vwap: Decimal,
    pullback: KrCompletedMinuteBar,
    pullback_vwap: Decimal,
) -> bool:
    return (
        Decimal(bar.volume) >= Decimal(pullback.volume) * _VOLUME_MULTIPLIER
        and bar.close > vwap * (Decimal(1) + _RECLAIM_BUFFER)
        and bar.close > bar.open
        and bar.high > pullback.high
        and vwap >= pullback_vwap
    )


def _build_setup(
    request: KrThemeDaySetupInput,
    trigger: KrCompletedMinuteBar,
    pullback: KrCompletedMinuteBar,
) -> KrThemeDaySetup:
    risk = trigger.close - pullback.low
    if risk <= 0:
        raise InvalidKrThemeDaySetupError
    evidence = tuple(sorted((bar.evidence_ref for bar in request.bars), key=lambda item: item.canonical_id))
    observed_at = trigger.observed_at
    valid_until = min(request.opportunity.valid_until, observed_at + _SETUP_VALIDITY)
    return KrThemeDaySetup(
        setup_id=_setup_id(request, trigger),
        opportunity_id=request.opportunity.opportunity_id,
        producer_strategy_version=request.producer_strategy_version,
        symbol=trigger.symbol,
        observed_at=observed_at,
        valid_until=valid_until,
        stop_price=pullback.low,
        targets=(
            TradeTarget(label="1r", price=trigger.close + risk),
            TradeTarget(label="2r", price=trigger.close + risk * Decimal(2)),
        ),
        max_slippage_bps=request.max_slippage_bps,
        invalidation_rule="Invalidate below the first completed-bar VWAP pullback low or when a KR market gate blocks.",
        rationale="Fresh rank-one theme leader reclaimed completed-bar session VWAP with volume confirmation.",
        evidence_refs=evidence,
    )


def _setup_id(request: KrThemeDaySetupInput, trigger: KrCompletedMinuteBar) -> str:
    material = "|".join(
        (
            request.opportunity.opportunity_id,
            request.producer_strategy_version,
            trigger.symbol,
            trigger.end_at.isoformat(),
            trigger.evidence_ref.canonical_id,
        )
    )
    return f"kr-theme-vwap-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def _positive(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
