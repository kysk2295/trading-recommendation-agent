from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from decimal import Decimal
from typing import Final, Protocol

from trading_agent.alpaca_models import AlpacaBar
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipMinutePage,
    AlpacaSipMinutePageRequest,
    AlpacaSipRuntimeBar,
    AlpacaSipRuntimeContext,
    AlpacaSipRuntimeError,
)
from trading_agent.intraday_feature_kernel import CompletedMinuteBar
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeBatch,
    MarketDataRuntimeCheckpoint,
    build_market_data_runtime_receipt,
)
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionChannel,
)

_SOURCE_ID: Final = "alpaca.sip.us_equities"
_ONE_MINUTE: Final = dt.timedelta(minutes=1)


class AlpacaSipRuntimeEvidenceProjectorProtocol(Protocol):
    def project(
        self,
        page_set: AlpacaSipMinutePage,
        instrument_id: str,
        bars: tuple[AlpacaSipRuntimeBar, ...],
    ) -> ResearchInputIdentity: ...


class AlpacaSipRuntimeAdapter:
    __slots__ = ("_context", "_page_client", "_projector")

    source_id: str = _SOURCE_ID

    def __init__(
        self,
        page_client: AlpacaSipMinutePageClient,
        projector: AlpacaSipRuntimeEvidenceProjectorProtocol,
        context: AlpacaSipRuntimeContext,
    ) -> None:
        self._page_client = page_client
        self._projector = projector
        self._context = context

    def read_batch(
        self,
        desired: tuple[DesiredMarketDataSubscription, ...],
        checkpoint: MarketDataRuntimeCheckpoint | None,
    ) -> MarketDataRuntimeBatch:
        try:
            subscription, now, session_open, session_close = self._validate(desired, checkpoint)
            completed_boundary = now.replace(second=0, microsecond=0)
            if completed_boundary <= session_open:
                raise AlpacaSipRuntimeError
            page_set = self._page_client.fetch_page(
                AlpacaSipMinutePageRequest(
                    session_date=self._context.session_date,
                    symbol=subscription.symbol,
                    start_at=session_open,
                    end_at=min(completed_boundary, session_close) - dt.timedelta(microseconds=1),
                )
            )
            bars = _runtime_bars(page_set, session_open, min(completed_boundary, session_close))
            identity = self._projector.project(page_set, subscription.instrument_id, bars)
            recovering = checkpoint is not None and checkpoint.gap_blocked and _contiguous_from_open(bars)
            connection_epoch = _connection_epoch(
                self._context.session_date,
                subscription,
                checkpoint,
                identity,
                recovering=recovering,
            )
            after_sequence = None if checkpoint is None or recovering else checkpoint.last_sequence
            receipts = tuple(
                build_market_data_runtime_receipt(
                    source_id=self.source_id,
                    connection_epoch=connection_epoch,
                    sequence=bar.sequence,
                    received_at=page_set.pages[bar.page_index].received_at,
                    raw_payload=bar.canonical_payload,
                    instrument_id=subscription.instrument_id,
                    symbol=subscription.symbol,
                    completed_bar=bar.completed_bar,
                )
                for bar in bars
                if after_sequence is None or bar.sequence > after_sequence
            )
            return MarketDataRuntimeBatch(self.source_id, connection_epoch, identity, receipts)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            raise AlpacaSipRuntimeError from None

    def _validate(
        self,
        desired: tuple[DesiredMarketDataSubscription, ...],
        checkpoint: MarketDataRuntimeCheckpoint | None,
    ) -> tuple[DesiredMarketDataSubscription, dt.datetime, dt.datetime, dt.datetime]:
        now = self._context.clock()
        bounds = regular_session_bounds(self._context.session_date)
        if (
            type(self._context) is not AlpacaSipRuntimeContext
            or type(desired) is not tuple
            or len(desired) != 1
            or type(desired[0]) is not DesiredMarketDataSubscription
            or not _valid_checkpoint(checkpoint)
            or type(now) is not dt.datetime
            or now.tzinfo is None
            or now.utcoffset() is None
            or bounds is None
        ):
            raise AlpacaSipRuntimeError
        subscription = desired[0]
        if (
            not subscription.instrument_id
            or not subscription.symbol
            or subscription.instrument_id != self._context.instrument_id
            or subscription.symbol != self._context.symbol
            or subscription.channels != (SubscriptionChannel.QUOTE, SubscriptionChannel.TRADE)
            or not bounds[0] < now < bounds[1]
        ):
            raise AlpacaSipRuntimeError
        return subscription, now, bounds[0], bounds[1]


def _runtime_bars(
    page_set: AlpacaSipMinutePage,
    session_open: dt.datetime,
    completed_boundary: dt.datetime,
) -> tuple[AlpacaSipRuntimeBar, ...]:
    bars: list[AlpacaSipRuntimeBar] = []
    for page in page_set.pages:
        for wire_bar in page.payload.bars.get(page_set.request.symbol, ()):
            bars.append(_runtime_bar(wire_bar, page.page_index, session_open, completed_boundary))
    sequences = tuple(bar.sequence for bar in bars)
    if not bars or sequences != tuple(sorted(set(sequences))):
        raise AlpacaSipRuntimeError
    return tuple(bars)


def normalize_alpaca_sip_runtime_bars(
    page_set: AlpacaSipMinutePage,
    session_open: dt.datetime,
    completed_boundary: dt.datetime,
) -> tuple[AlpacaSipRuntimeBar, ...]:
    try:
        return _runtime_bars(page_set, session_open, completed_boundary)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise AlpacaSipRuntimeError from None


def _runtime_bar(
    wire_bar: AlpacaBar,
    page_index: int,
    session_open: dt.datetime,
    completed_boundary: dt.datetime,
) -> AlpacaSipRuntimeBar:
    start_at = wire_bar.timestamp.astimezone(session_open.tzinfo)
    if (
        start_at.second != 0
        or start_at.microsecond != 0
        or start_at < session_open
        or start_at >= completed_boundary
        or wire_bar.volume < 0
        or any(not math.isfinite(value) for value in (wire_bar.open, wire_bar.high, wire_bar.low, wire_bar.close))
    ):
        raise AlpacaSipRuntimeError
    sequence = int((start_at - session_open) / _ONE_MINUTE) + 1
    completed_bar = CompletedMinuteBar(
        start_at=start_at,
        end_at=start_at + _ONE_MINUTE,
        open=Decimal(str(wire_bar.open)),
        high=Decimal(str(wire_bar.high)),
        low=Decimal(str(wire_bar.low)),
        close=Decimal(str(wire_bar.close)),
        volume=wire_bar.volume,
    )
    payload = json.dumps(
        wire_bar.model_dump(by_alias=True, mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return AlpacaSipRuntimeBar(sequence, page_index, payload, completed_bar)


def _connection_epoch(
    session_date: dt.date,
    subscription: DesiredMarketDataSubscription,
    checkpoint: MarketDataRuntimeCheckpoint | None,
    identity: ResearchInputIdentity,
    *,
    recovering: bool,
) -> str:
    if checkpoint is not None and not recovering:
        return checkpoint.connection_epoch
    base = f"{session_date.isoformat()}:{subscription.instrument_id}:{subscription.symbol}"
    recovery = "" if checkpoint is None else f":{checkpoint.connection_epoch}:{identity.identity_sha256}"
    encoded = f"{base}{recovery}".encode()
    return f"alpaca-sip-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _contiguous_from_open(bars: tuple[AlpacaSipRuntimeBar, ...]) -> bool:
    return tuple(bar.sequence for bar in bars) == tuple(range(1, bars[-1].sequence + 1))


def _valid_checkpoint(checkpoint: MarketDataRuntimeCheckpoint | None) -> bool:
    if checkpoint is None:
        return True
    return (
        type(checkpoint) is MarketDataRuntimeCheckpoint
        and checkpoint.source_id == _SOURCE_ID
        and bool(checkpoint.connection_epoch)
        and type(checkpoint.last_sequence) is int
        and checkpoint.last_sequence > 0
        and type(checkpoint.gap_blocked) is bool
        and type(checkpoint.recorded_at) is dt.datetime
        and checkpoint.recorded_at.tzinfo is not None
        and checkpoint.recorded_at.utcoffset() is not None
    )


__all__ = ("AlpacaSipRuntimeAdapter", "normalize_alpaca_sip_runtime_bars")
