from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final

from trading_agent.alpaca_sip_trade_event_projection import project_alpaca_sip_trade_events
from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipTradeHistoryError,
    AlpacaSipTradeProjectionError,
)
from trading_agent.alpaca_sip_trade_store import StoredAlpacaSipTradeFrame
from trading_agent.canonical_dataset_models import CanonicalDatasetBatch, CanonicalDatasetPartition
from trading_agent.data_capability_models import DataSourceId
from trading_agent.raw_object_manifest_models import RawReceipt, RawReceiptPayload
from trading_agent.raw_receipt_projection import project_raw_receipt_partition
from trading_agent.security_master_models import DataMarketDomain

_INSTRUMENT_ID: Final = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SOURCE: Final = DataSourceId(provider="alpaca", feed="sip")
_RAW_SOURCE: Final = "alpaca.sip.trades"


@dataclass(frozen=True, slots=True)
class AlpacaSipTradeInstrumentBinding:
    symbol: str
    instrument_id: str

    def __post_init__(self) -> None:
        if not self.symbol or _INSTRUMENT_ID.fullmatch(self.instrument_id) is None:
            raise AlpacaSipTradeHistoryError


@dataclass(frozen=True, slots=True)
class AlpacaSipTradeHistoryRequest:
    market_date: dt.date
    bindings: tuple[AlpacaSipTradeInstrumentBinding, ...]

    def __post_init__(self) -> None:
        symbols = tuple(binding.symbol for binding in self.bindings)
        if (
            type(self.market_date) is not dt.date
            or isinstance(self.market_date, dt.datetime)
            or not self.bindings
            or any(type(binding) is not AlpacaSipTradeInstrumentBinding for binding in self.bindings)
            or symbols != tuple(sorted(set(symbols)))
        ):
            raise AlpacaSipTradeHistoryError


def project_alpaca_sip_trade_history(
    frames: tuple[StoredAlpacaSipTradeFrame, ...],
    request: AlpacaSipTradeHistoryRequest,
) -> CanonicalDatasetBatch:
    try:
        _validate_input(frames, request)
        events = project_alpaca_sip_trade_events(
            frames,
            tuple((binding.symbol, binding.instrument_id) for binding in request.bindings),
            _SOURCE,
        )
        manifest = project_raw_receipt_partition(
            tuple(sorted((_raw_receipt(frame) for frame in frames), key=lambda item: item.receipt_id)),
            source_id=_RAW_SOURCE,
            market_date=request.market_date,
            parent_ledger_generation=max(frame.generation for frame in frames),
        )
        return CanonicalDatasetBatch(
            partition=CanonicalDatasetPartition(
                source_id=_SOURCE,
                market_domain=DataMarketDomain.US_EQUITIES,
                event_type="trade",
                market_date=request.market_date,
            ),
            raw_manifest=manifest,
            events=events,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise AlpacaSipTradeProjectionError from None


def _raw_receipt(frame: StoredAlpacaSipTradeFrame) -> RawReceipt:
    return RawReceipt.from_payload(
        receipt_id=frame.receipt_id,
        source_id=_RAW_SOURCE,
        market_date=frame.market_date,
        received_at=frame.received_at,
        payload_sha256=frame.payload_sha256,
        payload=RawReceiptPayload(frame.payload),
    )


def _validate_input(
    frames: tuple[StoredAlpacaSipTradeFrame, ...],
    request: AlpacaSipTradeHistoryRequest,
) -> None:
    generations = tuple(frame.generation for frame in frames)
    received = tuple(frame.received_at for frame in frames)
    if (
        type(request) is not AlpacaSipTradeHistoryRequest
        or type(frames) is not tuple
        or not frames
        or any(type(frame) is not StoredAlpacaSipTradeFrame for frame in frames)
        or any(frame.market_date != request.market_date for frame in frames)
        or generations != tuple(sorted(set(generations)))
        or received != tuple(sorted(received))
    ):
        raise AlpacaSipTradeHistoryError


__all__ = (
    "AlpacaSipTradeHistoryError",
    "AlpacaSipTradeHistoryRequest",
    "AlpacaSipTradeInstrumentBinding",
    "project_alpaca_sip_trade_history",
)
