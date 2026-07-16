from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final, override

from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.signal_contract_models import (
    SignalActionability,
    SignalEntryType,
    TradeSignalEnvelope,
)
from trading_agent.swing_new_high_rvol import NewHighRvolConfig
from trading_agent.swing_shadow_models import SwingDailyBar, SwingDailySource
from trading_agent.swing_shadow_store import (
    ShadowEventKind,
    SwingShadowConflictError,
    SwingShadowEvent,
    SwingShadowWriter,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_TERMINAL_KINDS: Final = frozenset(
    {
        ShadowEventKind.STOPPED,
        ShadowEventKind.TARGETED,
        ShadowEventKind.TIME_EXIT,
        ShadowEventKind.EXPIRED,
    }
)
_MAX_HOLDING_SESSIONS: Final = NewHighRvolConfig().max_holding_sessions


class InvalidSwingShadowEngineError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing shadow 상태 전이를 안전하게 계산하지 못했습니다"


def advance_swing_shadow_session(
    writer: SwingShadowWriter,
    *,
    source: SwingDailySource,
    signals: tuple[TradeSignalEnvelope, ...] = (),
) -> tuple[SwingShadowEvent, ...]:
    try:
        _require_completed_source(source)
        appended: list[SwingShadowEvent] = []
        for signal in signals:
            _require_signal_matches_source(signal, source)
            created = writer.append_signal(
                signal,
                session_date=source.session_date,
                source_key=source.source_key,
            )
            if created is not None:
                appended.append(created)
        for signal in writer.signals():
            appended.extend(_advance_signal(writer, signal, source))
        return tuple(appended)
    except (SwingShadowConflictError, InvalidSwingShadowEngineError):
        raise
    except (ArithmeticError, TypeError, ValueError):
        raise InvalidSwingShadowEngineError from None


def _advance_signal(
    writer: SwingShadowWriter,
    signal: TradeSignalEnvelope,
    source: SwingDailySource,
) -> tuple[SwingShadowEvent, ...]:
    events = writer.events(signal.signal_id)
    if not events:
        raise InvalidSwingShadowEngineError
    latest = events[-1]
    if latest.kind in _TERMINAL_KINDS:
        return ()
    bar = _current_bar(source, signal.symbol)
    if latest.kind is ShadowEventKind.SIGNAL_CREATED:
        return _advance_pending(writer, signal, source, bar)
    if latest.kind is ShadowEventKind.ENTRY_FILLED:
        return _advance_open_position(writer, signal, source, bar, latest)
    raise InvalidSwingShadowEngineError


def _advance_pending(
    writer: SwingShadowWriter,
    signal: TradeSignalEnvelope,
    source: SwingDailySource,
    bar: SwingDailyBar,
) -> tuple[SwingShadowEvent, ...]:
    valid_session = signal.valid_until.astimezone(NEW_YORK).date()
    if source.session_date < valid_session:
        return ()
    if source.session_date > valid_session or bar.high < signal.entry_price:
        expired = _event(
            signal,
            source,
            kind=ShadowEventKind.EXPIRED,
        )
        return (expired,) if writer.append_event(expired) else ()
    entry = _event(
        signal,
        source,
        kind=ShadowEventKind.ENTRY_FILLED,
        price=max(bar.open, signal.entry_price),
    )
    appended = [entry] if writer.append_event(entry) else []
    if bar.low <= signal.stop_price:
        stopped = _event(
            signal,
            source,
            kind=ShadowEventKind.STOPPED,
            price=min(bar.open, signal.stop_price),
        )
        if writer.append_event(stopped):
            appended.append(stopped)
    elif bar.high >= signal.targets[0].price:
        targeted = _event(
            signal,
            source,
            kind=ShadowEventKind.TARGETED,
            price=max(bar.open, signal.targets[0].price),
        )
        if writer.append_event(targeted):
            appended.append(targeted)
    return tuple(appended)


def _advance_open_position(
    writer: SwingShadowWriter,
    signal: TradeSignalEnvelope,
    source: SwingDailySource,
    bar: SwingDailyBar,
    entry: SwingShadowEvent,
) -> tuple[SwingShadowEvent, ...]:
    if source.session_date <= entry.session_date:
        return ()
    if bar.low <= signal.stop_price:
        event = _event(
            signal,
            source,
            kind=ShadowEventKind.STOPPED,
            price=min(bar.open, signal.stop_price),
        )
    elif bar.high >= signal.targets[0].price:
        event = _event(
            signal,
            source,
            kind=ShadowEventKind.TARGETED,
            price=max(bar.open, signal.targets[0].price),
        )
    elif _completed_holding_sessions(entry.session_date, source.session_date) >= _MAX_HOLDING_SESSIONS:
        event = _event(
            signal,
            source,
            kind=ShadowEventKind.TIME_EXIT,
            price=bar.close,
        )
    else:
        return ()
    return (event,) if writer.append_event(event) else ()


def _event(
    signal: TradeSignalEnvelope,
    source: SwingDailySource,
    *,
    kind: ShadowEventKind,
    price: Decimal | None = None,
) -> SwingShadowEvent:
    return SwingShadowEvent(
        signal_id=signal.signal_id,
        kind=kind,
        session_date=source.session_date,
        observed_at=source.observed_at,
        source_key=source.source_key,
        price=price,
    )


def _require_completed_source(source: SwingDailySource) -> None:
    bounds = regular_session_bounds(source.session_date)
    if (
        source.observed_at.tzinfo is None
        or source.observed_at.utcoffset() is None
        or bounds is None
        or source.observed_at.astimezone(NEW_YORK) < bounds[1]
    ):
        raise InvalidSwingShadowEngineError


def _require_signal_matches_source(
    signal: TradeSignalEnvelope,
    source: SwingDailySource,
) -> None:
    evidence_ids = tuple(evidence.canonical_id for evidence in signal.evidence_refs)
    if (
        signal.strategy_lane.market_id is not MarketId.US_EQUITIES
        or signal.strategy_lane.agent_family is not AgentFamily.SWING_TRADING
        or signal.strategy_lane.strategy_id != "new_high_momentum"
        or signal.entry_type is not SignalEntryType.STOP_TRIGGER
        or signal.actionability is not SignalActionability.CONDITIONAL
        or signal.observed_at != source.observed_at
        or source.session_date != signal.observed_at.astimezone(NEW_YORK).date()
        or evidence_ids != (f"swing_shadow/daily_source:{source.source_key}",)
    ):
        raise InvalidSwingShadowEngineError


def _current_bar(source: SwingDailySource, symbol: str) -> SwingDailyBar:
    bars = source.bars_for(symbol)
    if not bars or bars[-1].session_date != source.session_date:
        raise InvalidSwingShadowEngineError
    return bars[-1]


def _completed_holding_sessions(entry_date: dt.date, current_date: dt.date) -> int:
    completed = 0
    current = entry_date + dt.timedelta(days=1)
    for _ in range(90):
        if current > current_date:
            return completed
        if regular_session_bounds(current) is not None:
            completed += 1
        current += dt.timedelta(days=1)
    raise InvalidSwingShadowEngineError


__all__ = (
    "InvalidSwingShadowEngineError",
    "advance_swing_shadow_session",
)
