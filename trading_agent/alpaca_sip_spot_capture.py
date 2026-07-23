from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import override

from trading_agent.alpaca_sip_runtime_adapter import (
    normalize_alpaca_sip_runtime_bars,
)
from trading_agent.alpaca_sip_runtime_evidence import (
    AlpacaSipRuntimeEvidenceProjector,
)
from trading_agent.alpaca_sip_runtime_models import AlpacaSipMinutePage
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import regular_session_bounds
from trading_agent.us_market_data_runtime_models import (
    build_market_data_runtime_receipt,
)
from trading_agent.us_market_data_runtime_store import MarketDataRuntimeStore

SOURCE_ID = "alpaca.sip.us_equities"


class AlpacaSipSpotCaptureError(ValueError):
    @override
    def __str__(self) -> str:
        return "bounded Alpaca SIP spot capture is invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipSpotCaptureResult:
    identity: ResearchInputIdentity
    latest_completed_at: dt.datetime
    receipt_count: int
    inserted_receipt_count: int


def materialize_alpaca_sip_spot_capture(
    page_set: AlpacaSipMinutePage,
    instrument_id: str,
    projector: AlpacaSipRuntimeEvidenceProjector,
    runtime_store: MarketDataRuntimeStore,
) -> AlpacaSipSpotCaptureResult:
    try:
        bounds = regular_session_bounds(page_set.request.session_date)
        if (
            type(page_set) is not AlpacaSipMinutePage
            or type(instrument_id) is not str
            or not instrument_id
            or type(projector) is not AlpacaSipRuntimeEvidenceProjector
            or type(runtime_store) is not MarketDataRuntimeStore
            or bounds is None
            or page_set.request.start_at != bounds[0]
        ):
            raise AlpacaSipSpotCaptureError
        completed_boundary = page_set.request.end_at + dt.timedelta(microseconds=1)
        bars = normalize_alpaca_sip_runtime_bars(
            page_set,
            bounds[0],
            completed_boundary,
        )
        if (
            tuple(item.sequence for item in bars) != tuple(range(1, len(bars) + 1))
            or bars[-1].completed_bar.end_at != completed_boundary
            or any(page_set.pages[item.page_index].received_at < item.completed_bar.end_at for item in bars)
        ):
            raise AlpacaSipSpotCaptureError
        identity = projector.project(page_set, instrument_id, bars)
        epoch = _connection_epoch(
            page_set.request.session_date,
            instrument_id,
            page_set.request.symbol,
            identity,
        )
        receipts = tuple(
            build_market_data_runtime_receipt(
                source_id=SOURCE_ID,
                connection_epoch=epoch,
                sequence=item.sequence,
                received_at=page_set.pages[item.page_index].received_at,
                raw_payload=item.canonical_payload,
                instrument_id=instrument_id,
                symbol=page_set.request.symbol,
                completed_bar=item.completed_bar,
            )
            for item in bars
        )
        inserted = 0
        with runtime_store.writer() as writer:
            for receipt in receipts:
                inserted += writer.append_receipt(receipt)
        return AlpacaSipSpotCaptureResult(
            identity=identity,
            latest_completed_at=receipts[-1].completed_bar.end_at,
            receipt_count=len(receipts),
            inserted_receipt_count=inserted,
        )
    except AlpacaSipSpotCaptureError:
        raise
    except (AttributeError, IndexError, OSError, TypeError, ValueError):
        raise AlpacaSipSpotCaptureError from None


def _connection_epoch(
    session_date: dt.date,
    instrument_id: str,
    symbol: str,
    identity: ResearchInputIdentity,
) -> str:
    material = "|".join(
        (
            session_date.isoformat(),
            instrument_id,
            symbol,
            identity.dataset_id,
        )
    )
    return f"alpaca-sip-spot-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


__all__ = (
    "SOURCE_ID",
    "AlpacaSipSpotCaptureError",
    "AlpacaSipSpotCaptureResult",
    "materialize_alpaca_sip_spot_capture",
)
