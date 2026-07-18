from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, assert_never
from zoneinfo import ZoneInfo

from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeCancelMessage,
    AlpacaSipTradeCorrectionMessage,
    AlpacaSipTradeHistoryError,
    AlpacaSipTradeMessage,
    parse_alpaca_sip_trade_frame,
)
from trading_agent.alpaca_sip_trade_payloads import cancel_payload, correction_payload, trade_payload
from trading_agent.alpaca_sip_trade_store import StoredAlpacaSipTradeFrame
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.data_capability_models import DataSourceId

_NEW_YORK: Final = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class _ProjectionContext:
    market_date: dt.date
    receipt_id: str
    received_at: dt.datetime
    normalized_at: dt.datetime
    sequence: str
    instrument_id: str


@dataclass(frozen=True, slots=True)
class _EventSeed:
    provider_event_id: str
    operation: CanonicalEventOperation
    correction_of: str | None
    payload: bytes
    quality_flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ActiveTrade:
    event: CanonicalEventEnvelope
    exchange: str
    tape: str
    price: Decimal
    size: int
    conditions: tuple[str, ...]


class _TradeProjectionState:
    __slots__ = ("_aliases", "_bindings", "_events", "_message_offset", "_seen_aliases", "_source")

    def __init__(self, bindings: tuple[tuple[str, str], ...], source: DataSourceId) -> None:
        self._bindings = dict(bindings)
        self._source = source
        self._events: list[CanonicalEventEnvelope] = []
        self._aliases: dict[tuple[str, int], _ActiveTrade] = {}
        self._seen_aliases: set[tuple[str, int]] = set()
        self._message_offset = 0

    def project(self, frame: StoredAlpacaSipTradeFrame) -> None:
        for index, message in enumerate(parse_alpaca_sip_trade_frame(frame.payload)):
            instrument_id = self._bindings.get(message.symbol)
            if (
                instrument_id is None
                or message.timestamp > frame.received_at
                or message.timestamp.astimezone(_NEW_YORK).date() != frame.market_date
            ):
                raise AlpacaSipTradeHistoryError
            context = _ProjectionContext(
                frame.market_date,
                frame.receipt_id,
                frame.received_at,
                frame.received_at + dt.timedelta(microseconds=self._message_offset),
                f"{frame.generation}:{index}",
                instrument_id,
            )
            self._message_offset += 1
            match message:
                case AlpacaSipTradeMessage():
                    active = self._original(message, context)
                case AlpacaSipTradeCorrectionMessage():
                    active = self._correction(message, context)
                case AlpacaSipTradeCancelMessage():
                    active = self._cancel(message, context)
                case unreachable:
                    assert_never(unreachable)
            self._events.append(active.event)

    def events(self) -> tuple[CanonicalEventEnvelope, ...]:
        return tuple(sorted(self._events, key=lambda event: event.event_id))

    def _original(self, message: AlpacaSipTradeMessage, context: _ProjectionContext) -> _ActiveTrade:
        alias = (message.symbol, message.trade_id)
        if alias in self._seen_aliases:
            raise AlpacaSipTradeHistoryError
        provider_event_id = f"{context.market_date.isoformat()}:{message.symbol}:{message.trade_id}"
        event = _event(
            message,
            context,
            _EventSeed(
                provider_event_id,
                CanonicalEventOperation.ORIGINAL,
                None,
                trade_payload(message),
                ("sip", "trade"),
            ),
            self._source,
        )
        active = _ActiveTrade(event, message.exchange, message.tape, message.price, message.size, message.conditions)
        self._aliases[alias] = active
        self._seen_aliases.add(alias)
        return active

    def _correction(
        self,
        message: AlpacaSipTradeCorrectionMessage,
        context: _ProjectionContext,
    ) -> _ActiveTrade:
        target_alias = (message.symbol, message.original_trade_id)
        corrected_alias = (message.symbol, message.corrected_trade_id)
        target = self._aliases.get(target_alias)
        expected = (
            message.exchange,
            message.tape,
            message.original_price,
            message.original_size,
            message.original_conditions,
        )
        if target is None or _state_values(target) != expected or corrected_alias in self._seen_aliases:
            raise AlpacaSipTradeHistoryError
        event = _event(
            message,
            context,
            _EventSeed(
                target.event.provider_event_id or "",
                CanonicalEventOperation.CORRECTION,
                target.event.event_id,
                correction_payload(message),
                ("corrected", "sip", "trade"),
            ),
            self._source,
        )
        active = _ActiveTrade(
            event,
            message.exchange,
            message.tape,
            message.corrected_price,
            message.corrected_size,
            message.corrected_conditions,
        )
        self._replace_aliases(target, active)
        self._aliases[corrected_alias] = active
        self._seen_aliases.add(corrected_alias)
        return active

    def _cancel(self, message: AlpacaSipTradeCancelMessage, context: _ProjectionContext) -> _ActiveTrade:
        target = self._aliases.get((message.symbol, message.trade_id))
        expected = (message.exchange, message.tape, message.price, message.size)
        if target is None or _state_values(target)[:4] != expected:
            raise AlpacaSipTradeHistoryError
        flag = "canceled" if message.action == "C" else "error"
        event = _event(
            message,
            context,
            _EventSeed(
                target.event.provider_event_id or "",
                CanonicalEventOperation.TOMBSTONE,
                target.event.event_id,
                cancel_payload(message),
                (flag, "sip", "trade"),
            ),
            self._source,
        )
        for alias in tuple(alias for alias, active in self._aliases.items() if active == target):
            del self._aliases[alias]
        return _ActiveTrade(event, target.exchange, target.tape, target.price, target.size, target.conditions)

    def _replace_aliases(self, target: _ActiveTrade, successor: _ActiveTrade) -> None:
        for alias, active in tuple(self._aliases.items()):
            if active == target:
                self._aliases[alias] = successor


def project_alpaca_sip_trade_events(
    frames: tuple[StoredAlpacaSipTradeFrame, ...],
    bindings: tuple[tuple[str, str], ...],
    source: DataSourceId,
) -> tuple[CanonicalEventEnvelope, ...]:
    state = _TradeProjectionState(bindings, source)
    for frame in frames:
        state.project(frame)
    return state.events()


def _event(
    message: AlpacaSipTradeMessage | AlpacaSipTradeCorrectionMessage | AlpacaSipTradeCancelMessage,
    context: _ProjectionContext,
    seed: _EventSeed,
    source: DataSourceId,
) -> CanonicalEventEnvelope:
    content_hash = hashlib.sha256(seed.payload).hexdigest()
    identity = f"{seed.operation.value}:{seed.provider_event_id}:{seed.correction_of}:{context.sequence}:{content_hash}"
    return CanonicalEventEnvelope(
        event_id=f"trade-{hashlib.sha256(identity.encode()).hexdigest()}",
        source_id=source,
        provider_event_id=seed.provider_event_id,
        entity_refs=(CanonicalEntityRef(entity_type=CanonicalEntityType.INSTRUMENT, entity_id=context.instrument_id),),
        event_type="trade",
        event_time=message.timestamp,
        provider_time=message.timestamp,
        received_at=context.received_at,
        normalized_at=context.normalized_at,
        sequence_or_offset=context.sequence,
        operation=seed.operation,
        correction_of=seed.correction_of,
        raw_receipt_ref=context.receipt_id,
        content_hash=content_hash,
        quality_flags=seed.quality_flags,
    )


def _state_values(active: _ActiveTrade) -> tuple[str, str, Decimal, int, tuple[str, ...]]:
    return active.exchange, active.tape, active.price, active.size, active.conditions


__all__ = ("project_alpaca_sip_trade_events",)
