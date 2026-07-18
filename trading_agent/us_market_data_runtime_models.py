from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Final, override

from trading_agent.intraday_feature_kernel import CompletedMinuteBar, IntradayFeatureSnapshot
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_intraday_volume_profile_models import (
    IntradayVolumeProfileError,
    IntradayVolumeProfileEvidence,
    validate_intraday_volume_profile,
)

_ERROR_MESSAGE: Final = "market data runtime input is invalid"


class MarketDataRuntimeError(ValueError):
    def __init__(self, *_args: object) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "MarketDataRuntimeError()"


class MarketDataRuntimeStatus(StrEnum):
    READY = "ready"
    NO_NEW_DATA = "no_new_data"
    BLOCKED_SUBSCRIPTION_POLICY = "blocked_subscription_policy"
    BLOCKED_SEQUENCE_GAP = "blocked_sequence_gap"


class MarketDataRuntimeIncidentKind(StrEnum):
    SEQUENCE_GAP = "sequence_gap"
    RECONNECT = "reconnect"


@dataclass(frozen=True, slots=True)
class RuntimeFeatureRequest:
    instrument_id: str
    volume_profile: IntradayVolumeProfileEvidence


@dataclass(frozen=True, slots=True)
class MarketDataRuntimeReceipt:
    source_id: str
    connection_epoch: str
    sequence: int
    receipt_id: str
    received_at: dt.datetime
    payload_sha256: str
    raw_payload: bytes = field(repr=False)
    instrument_id: str
    symbol: str
    completed_bar: CompletedMinuteBar


@dataclass(frozen=True, slots=True)
class MarketDataRuntimeBatch:
    source_id: str
    connection_epoch: str
    identity: ResearchInputIdentity
    receipts: tuple[MarketDataRuntimeReceipt, ...]


@dataclass(frozen=True, slots=True)
class MarketDataRuntimeIncident:
    kind: MarketDataRuntimeIncidentKind
    source_id: str
    previous_epoch: str | None
    current_epoch: str
    expected_sequence: int | None
    observed_sequence: int | None
    recorded_at: dt.datetime


@dataclass(frozen=True, slots=True)
class MarketDataRuntimeCheckpoint:
    source_id: str
    connection_epoch: str
    last_sequence: int
    gap_blocked: bool
    recorded_at: dt.datetime


@dataclass(frozen=True, slots=True)
class MarketDataRuntimeResult:
    status: MarketDataRuntimeStatus
    source_id: str
    connection_epoch: str | None
    last_sequence: int | None
    inserted_receipt_count: int
    duplicate_receipt_count: int
    feature_snapshots: tuple[IntradayFeatureSnapshot, ...]
    incidents: tuple[MarketDataRuntimeIncident, ...]


def build_market_data_runtime_receipt(
    *,
    source_id: str,
    connection_epoch: str,
    sequence: int,
    received_at: dt.datetime,
    raw_payload: bytes,
    instrument_id: str,
    symbol: str,
    completed_bar: CompletedMinuteBar,
) -> MarketDataRuntimeReceipt:
    if not _valid_text(source_id) or not _valid_text(connection_epoch):
        raise MarketDataRuntimeError
    if type(sequence) is not int or sequence <= 0:
        raise MarketDataRuntimeError
    if not _aware(received_at) or type(raw_payload) is not bytes or not raw_payload:
        raise MarketDataRuntimeError
    if not _valid_text(instrument_id) or not _valid_text(symbol):
        raise MarketDataRuntimeError
    if type(completed_bar) is not CompletedMinuteBar or not _valid_bar(completed_bar):
        raise MarketDataRuntimeError
    payload_sha256 = hashlib.sha256(raw_payload).hexdigest()
    identity = {
        "bar": {
            "close": str(completed_bar.close),
            "end_at": completed_bar.end_at.isoformat(),
            "high": str(completed_bar.high),
            "low": str(completed_bar.low),
            "open": str(completed_bar.open),
            "start_at": completed_bar.start_at.isoformat(),
            "volume": completed_bar.volume,
        },
        "connection_epoch": connection_epoch,
        "instrument_id": instrument_id,
        "payload_sha256": payload_sha256,
        "sequence": sequence,
        "source_id": source_id,
        "symbol": symbol,
    }
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    receipt_id = hashlib.sha256(encoded.encode()).hexdigest()
    return MarketDataRuntimeReceipt(
        source_id=source_id,
        connection_epoch=connection_epoch,
        sequence=sequence,
        receipt_id=receipt_id,
        received_at=received_at,
        payload_sha256=payload_sha256,
        raw_payload=raw_payload,
        instrument_id=instrument_id,
        symbol=symbol,
        completed_bar=completed_bar,
    )


def validate_runtime_request(request: RuntimeFeatureRequest) -> None:
    if type(request) is not RuntimeFeatureRequest:
        raise MarketDataRuntimeError
    if not _valid_text(request.instrument_id):
        raise MarketDataRuntimeError
    try:
        validate_intraday_volume_profile(request.volume_profile)
    except IntradayVolumeProfileError:
        raise MarketDataRuntimeError from None
    if request.volume_profile.instrument_id != request.instrument_id:
        raise MarketDataRuntimeError


def validate_runtime_request_for_evaluation(
    request: RuntimeFeatureRequest,
    evaluated_at: dt.datetime,
) -> None:
    validate_runtime_request(request)
    if (
        not _aware(evaluated_at)
        or request.volume_profile.target_session_date != evaluated_at.astimezone(NEW_YORK).date()
    ):
        raise MarketDataRuntimeError


def validate_market_data_runtime_receipt(receipt: MarketDataRuntimeReceipt) -> None:
    if type(receipt) is not MarketDataRuntimeReceipt:
        raise MarketDataRuntimeError
    rebuilt = build_market_data_runtime_receipt(
        source_id=receipt.source_id,
        connection_epoch=receipt.connection_epoch,
        sequence=receipt.sequence,
        received_at=receipt.received_at,
        raw_payload=receipt.raw_payload,
        instrument_id=receipt.instrument_id,
        symbol=receipt.symbol,
        completed_bar=receipt.completed_bar,
    )
    if rebuilt != receipt:
        raise MarketDataRuntimeError


def _valid_text(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 128


def _aware(value: object) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


def _valid_bar(bar: CompletedMinuteBar) -> bool:
    if not _aware(bar.start_at) or not _aware(bar.end_at):
        return False
    if bar.end_at - bar.start_at != dt.timedelta(minutes=1):
        return False
    prices = (bar.open, bar.high, bar.low, bar.close)
    if not all(type(price) is Decimal and price.is_finite() and price > 0 for price in prices):
        return False
    return (
        type(bar.volume) is int
        and bar.volume >= 0
        and bar.low <= min(bar.open, bar.close)
        and max(bar.open, bar.close) <= bar.high
    )


__all__ = (
    "MarketDataRuntimeBatch",
    "MarketDataRuntimeCheckpoint",
    "MarketDataRuntimeError",
    "MarketDataRuntimeIncident",
    "MarketDataRuntimeIncidentKind",
    "MarketDataRuntimeReceipt",
    "MarketDataRuntimeResult",
    "MarketDataRuntimeStatus",
    "RuntimeFeatureRequest",
    "build_market_data_runtime_receipt",
    "validate_market_data_runtime_receipt",
    "validate_runtime_request_for_evaluation",
)
