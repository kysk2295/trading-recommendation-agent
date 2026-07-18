from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal

from trading_agent.intraday_feature_kernel import CompletedMinuteBar
from trading_agent.us_market_data_runtime_models import (
    MarketDataRuntimeError,
    MarketDataRuntimeIncident,
    MarketDataRuntimeIncidentKind,
    MarketDataRuntimeReceipt,
)


def receipt_row(receipt: MarketDataRuntimeReceipt) -> tuple[str | int | bytes, ...]:
    bar = receipt.completed_bar
    return (
        receipt.source_id,
        receipt.connection_epoch,
        receipt.sequence,
        receipt.receipt_id,
        receipt.received_at.isoformat(),
        receipt.payload_sha256,
        receipt.raw_payload,
        receipt.instrument_id,
        receipt.symbol,
        bar.start_at.isoformat(),
        bar.end_at.isoformat(),
        str(bar.open),
        str(bar.high),
        str(bar.low),
        str(bar.close),
        bar.volume,
    )


def incident_row(incident: MarketDataRuntimeIncident) -> tuple[str | int | None, ...]:
    return (
        incident.kind.value,
        incident.source_id,
        incident.previous_epoch,
        incident.current_epoch,
        incident.expected_sequence,
        incident.observed_sequence,
        incident.recorded_at.isoformat(),
    )


def incident_key(row: tuple[str | int | None, ...]) -> str:
    encoded = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def incident_from_row(
    row: tuple[str, str, str | None, str, int | None, int | None, str],
) -> MarketDataRuntimeIncident:
    return MarketDataRuntimeIncident(
        MarketDataRuntimeIncidentKind(row[0]),
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
        datetime_from_text(row[6]),
    )


def bar_from_row(row: tuple[str, str, str, str, str, str, int]) -> CompletedMinuteBar:
    return CompletedMinuteBar(
        datetime_from_text(row[0]),
        datetime_from_text(row[1]),
        Decimal(row[2]),
        Decimal(row[3]),
        Decimal(row[4]),
        Decimal(row[5]),
        row[6],
    )


def datetime_from_text(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataRuntimeError
    return parsed
