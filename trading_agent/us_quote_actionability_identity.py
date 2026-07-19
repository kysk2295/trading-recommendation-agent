from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import TypedDict


class _QuoteIdentityMaterial(TypedDict):
    provider: str
    exchange: str
    symbol: str
    provider_observed_at: str
    received_at: str
    bid: str
    ask: str
    bid_size: int
    ask_size: int


class _AssessmentIdentityMaterial(TypedDict):
    base_signal_id: str
    scan_started_at: str


class _SignalIdentityMaterial(TypedDict):
    base_signal_id: str
    quote_id: str


type _IdentityMaterial = _QuoteIdentityMaterial | _AssessmentIdentityMaterial | _SignalIdentityMaterial


def quote_identity(
    *,
    exchange: str,
    symbol: str,
    provider_observed_at: dt.datetime,
    received_at: dt.datetime,
    bid: Decimal,
    ask: Decimal,
    bid_size: int,
    ask_size: int,
) -> str:
    return _identity(
        "us-quote:",
        _QuoteIdentityMaterial(
            provider="kis",
            exchange=exchange,
            symbol=symbol,
            provider_observed_at=_timestamp_text(provider_observed_at),
            received_at=_timestamp_text(received_at),
            bid=_decimal_text(bid),
            ask=_decimal_text(ask),
            bid_size=bid_size,
            ask_size=ask_size,
        ),
    )


def assessment_identity(*, base_signal_id: str, scan_started_at: dt.datetime) -> str:
    return _identity(
        "us-quote-assessment:",
        _AssessmentIdentityMaterial(
            base_signal_id=base_signal_id,
            scan_started_at=_timestamp_text(scan_started_at),
        ),
    )


def derived_signal_identity(base_signal_id: str, quote_id: str) -> str:
    return _identity(
        "us-quote-signal:",
        _SignalIdentityMaterial(base_signal_id=base_signal_id, quote_id=quote_id),
    )


def _identity(prefix: str, material: _IdentityMaterial) -> str:
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"{prefix}{hashlib.sha256(encoded).hexdigest()}"


def _timestamp_text(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat()


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = (
    "assessment_identity",
    "derived_signal_identity",
    "quote_identity",
)
