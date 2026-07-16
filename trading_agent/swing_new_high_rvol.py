from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, override

from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)
from trading_agent.signal_contract_models import (
    EvidenceRef,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)
from trading_agent.swing_shadow_models import SwingDailySource
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_BASIS_POINTS: Final = Decimal(10_000)
_STRATEGY_VERSION: Final = "new_high_rvol_20d_1p5_v1"
_STRATEGY_LANE: Final = StrategyLaneRef(
    market_id=MarketId.US_EQUITIES,
    agent_family=AgentFamily.SWING_TRADING,
    strategy_id="new_high_momentum",
)


class InvalidNewHighRvolProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing 신고가·상대거래량 신호의 인과성을 확인하지 못했습니다"


@dataclass(frozen=True, slots=True)
class NewHighRvolConfig:
    lookback_sessions: int = 20
    minimum_rvol: Decimal = Decimal("1.5")
    entry_buffer_bps: Decimal = Decimal("50")
    stop_loss_bps: Decimal = Decimal("800")
    target_r_multiple: Decimal = Decimal("2")
    max_holding_sessions: int = 10


_DEFAULT_CONFIG: Final = NewHighRvolConfig()


def project_new_high_rvol_signals(
    source: SwingDailySource,
    *,
    config: NewHighRvolConfig = _DEFAULT_CONFIG,
) -> tuple[TradeSignalEnvelope, ...]:
    try:
        if config != _DEFAULT_CONFIG:
            raise InvalidNewHighRvolProjectionError
        _require_completed_source(source)
        valid_until = _next_regular_close(source.session_date)
        expected_dates = _regular_sessions_ending(
            source.session_date,
            count=config.lookback_sessions + 1,
        )
        signals = tuple(
            _project_symbol(
                source,
                symbol=symbol,
                valid_until=valid_until,
                expected_dates=expected_dates,
                config=config,
            )
            for symbol in source.symbols
        )
        return tuple(signal for signal in signals if signal is not None)
    except InvalidNewHighRvolProjectionError:
        raise
    except (ArithmeticError, TypeError, ValueError):
        raise InvalidNewHighRvolProjectionError from None


def _project_symbol(
    source: SwingDailySource,
    *,
    symbol: str,
    valid_until: dt.datetime,
    expected_dates: tuple[dt.date, ...],
    config: NewHighRvolConfig,
) -> TradeSignalEnvelope | None:
    bars = source.bars_for(symbol)
    if len(bars) < len(expected_dates) or tuple(
        bar.session_date for bar in bars[-len(expected_dates) :]
    ) != expected_dates:
        raise InvalidNewHighRvolProjectionError
    history = bars[-len(expected_dates) : -1]
    current = bars[-1]
    average_volume = Decimal(sum(bar.volume for bar in history)) / Decimal(len(history))
    if current.close <= max(bar.close for bar in history) or current.volume < average_volume * config.minimum_rvol:
        return None
    entry = current.close * (Decimal(1) + config.entry_buffer_bps / _BASIS_POINTS)
    stop = entry * (Decimal(1) - config.stop_loss_bps / _BASIS_POINTS)
    target = entry + config.target_r_multiple * (entry - stop)
    return TradeSignalEnvelope(
        signal_id=_signal_id(source, symbol),
        strategy_lane=_STRATEGY_LANE,
        producer_strategy_version=_STRATEGY_VERSION,
        symbol=symbol,
        observed_at=source.observed_at,
        valid_until=valid_until,
        side=SignalSide.LONG,
        entry_type=SignalEntryType.STOP_TRIGGER,
        entry_price=entry,
        stop_price=stop,
        targets=(TradeTarget(label="2r", price=target),),
        actionability=SignalActionability.CONDITIONAL,
        invalidation_rule="Invalidate if the next regular session closes before the trigger.",
        rationale="20-session new high with completed-day relative volume confirmation.",
        evidence_refs=(
            EvidenceRef(
                namespace="swing_shadow/daily_source",
                record_id=source.source_key,
                observed_at=source.observed_at,
            ),
        ),
    )


def _require_completed_source(source: SwingDailySource) -> None:
    bounds = regular_session_bounds(source.session_date)
    if (
        source.observed_at.tzinfo is None
        or source.observed_at.utcoffset() is None
        or bounds is None
        or source.observed_at.astimezone(NEW_YORK) < bounds[1]
    ):
        raise InvalidNewHighRvolProjectionError


def _next_regular_close(session_date: dt.date) -> dt.datetime:
    next_date = session_date + dt.timedelta(days=1)
    for _ in range(14):
        bounds = regular_session_bounds(next_date)
        if bounds is not None:
            return bounds[1]
        next_date += dt.timedelta(days=1)
    raise InvalidNewHighRvolProjectionError


def _regular_sessions_ending(
    session_date: dt.date,
    *,
    count: int,
) -> tuple[dt.date, ...]:
    sessions: list[dt.date] = []
    current = session_date
    for _ in range(90):
        if regular_session_bounds(current) is not None:
            sessions.append(current)
            if len(sessions) == count:
                return tuple(reversed(sessions))
        current -= dt.timedelta(days=1)
    raise InvalidNewHighRvolProjectionError


def _signal_id(source: SwingDailySource, symbol: str) -> str:
    material = "|".join(
        (
            _STRATEGY_VERSION,
            source.source_key,
            symbol,
            source.session_date.isoformat(),
        )
    )
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()[:16]
    return f"swing-new-high-rvol-{source.session_date:%Y%m%d}-{symbol}-{digest}"


__all__ = (
    "InvalidNewHighRvolProjectionError",
    "NewHighRvolConfig",
    "project_new_high_rvol_signals",
)
