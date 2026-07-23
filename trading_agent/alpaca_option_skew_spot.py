from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading_agent.alpaca_models import AlpacaBar
from trading_agent.alpaca_option_skew_models import AlpacaOptionSkewError
from trading_agent.alpaca_option_surface import AlpacaOptionSurface
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeReceipt,
)

_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class SourceBackedSpot:
    receipt: MarketDataRuntimeReceipt
    event: CanonicalEventEnvelope


def select_source_backed_spot(
    receipts: tuple[MarketDataRuntimeReceipt, ...],
    events: tuple[CanonicalEventEnvelope, ...],
    call: AlpacaOptionSurface,
    put: AlpacaOptionSurface,
    underlying_instrument_id: str,
) -> SourceBackedSpot:
    event_by_hash = {event.content_hash: event for event in events if _is_spot_event(event, underlying_instrument_id)}
    latest_allowed = min(
        call.surface_observed_at,
        put.surface_observed_at,
    )
    candidates = tuple(
        SourceBackedSpot(receipt, event_by_hash[receipt.payload_sha256])
        for receipt in receipts
        if receipt.symbol == call.underlying_symbol
        and receipt.completed_bar.end_at <= latest_allowed
        and receipt.received_at >= receipt.completed_bar.end_at
        and receipt.received_at <= latest_allowed
        and receipt.payload_sha256 in event_by_hash
        and event_by_hash[receipt.payload_sha256].normalized_at <= latest_allowed
        and receipt.completed_bar.start_at.astimezone(_NEW_YORK).date() == latest_allowed.astimezone(_NEW_YORK).date()
        and _event_matches_receipt(
            event_by_hash[receipt.payload_sha256],
            receipt,
            underlying_instrument_id,
        )
    )
    if not candidates:
        raise AlpacaOptionSkewError
    latest_completed_at = max(item.receipt.completed_bar.end_at for item in candidates)
    latest = tuple(item for item in candidates if item.receipt.completed_bar.end_at == latest_completed_at)
    semantics = {
        (
            item.receipt.payload_sha256,
            item.receipt.completed_bar.start_at,
            item.receipt.completed_bar.close,
            item.event.event_id,
        )
        for item in latest
    }
    if len(semantics) != 1:
        raise AlpacaOptionSkewError
    return min(latest, key=lambda item: item.receipt.receipt_id)


def _is_spot_event(
    event: CanonicalEventEnvelope,
    instrument_id: str,
) -> bool:
    return (
        event.source_id.provider == "alpaca"
        and event.source_id.feed == "sip"
        and event.event_type == "minute_bar"
        and event.operation is CanonicalEventOperation.ORIGINAL
        and event.event_time is not None
        and event.quality_flags == ("complete", "sip")
        and event.entity_refs
        == (
            CanonicalEntityRef(
                entity_type=CanonicalEntityType.INSTRUMENT,
                entity_id=instrument_id,
            ),
        )
    )


def _event_matches_receipt(
    event: CanonicalEventEnvelope,
    receipt: MarketDataRuntimeReceipt,
    instrument_id: str,
) -> bool:
    bar = receipt.completed_bar
    return (
        event.event_time == bar.start_at
        and event.provider_time == bar.start_at
        and event.received_at == receipt.received_at
        and event.provider_event_id == f"{instrument_id}:{bar.start_at.isoformat()}"
        and event.content_hash == receipt.payload_sha256
        and _payload_matches_bar(receipt)
    )


def _payload_matches_bar(receipt: MarketDataRuntimeReceipt) -> bool:
    wire = AlpacaBar.model_validate_json(receipt.raw_payload)
    bar = receipt.completed_bar
    return (
        wire.timestamp == bar.start_at
        and Decimal(str(wire.open)) == bar.open
        and Decimal(str(wire.high)) == bar.high
        and Decimal(str(wire.low)) == bar.low
        and Decimal(str(wire.close)) == bar.close
        and wire.volume == bar.volume
    )


__all__ = (
    "SourceBackedSpot",
    "select_source_backed_spot",
)
