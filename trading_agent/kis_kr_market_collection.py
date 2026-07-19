from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, assert_never, override
from zoneinfo import ZoneInfo

from trading_agent.kis_kr_market_client import KisKrMarketFetchRequest
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
)
from trading_agent.kis_kr_market_parsing import (
    parse_bar_start,
    parse_minute_envelope,
    parse_price_envelope,
    parse_quote_envelope,
)
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_instrument import is_kr_instrument_symbol_v2

KST: Final = ZoneInfo("Asia/Seoul")
_FIRST_COLLECTION: Final = dt.time(9, 1)
_COLLECTION_CLOSE: Final = dt.time(15, 30)
_MAX_RESPONSE_DELAY: Final = dt.timedelta(seconds=30)
_INTRADAY_KINDS: Final = (
    KisKrMarketReceiptKind.MINUTE_BARS,
    KisKrMarketReceiptKind.PRICE_STATUS,
    KisKrMarketReceiptKind.ORDER_BOOK,
)


class InvalidKisKrMarketCollectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR market collection input is invalid"


class KisKrMarketCollectionPhase(StrEnum):
    INTRADAY = "intraday"
    EOD_MINUTE = "eod_minute"


class KisKrMarketFetcher(Protocol):
    def fetch(self, source: KisKrMarketFetchRequest, /) -> KisKrMarketReceipt: ...


@dataclass(frozen=True, slots=True)
class KisKrMarketCollectionRequest:
    symbol: str
    session_date: dt.date
    clock: Callable[[], dt.datetime]
    phase: KisKrMarketCollectionPhase = KisKrMarketCollectionPhase.INTRADAY

    def __post_init__(self) -> None:
        if (
            not is_kr_instrument_symbol_v2(self.symbol)
            or type(self.session_date) is not dt.date
            or type(self.phase) is not KisKrMarketCollectionPhase
        ):
            raise InvalidKisKrMarketCollectionError


@dataclass(frozen=True, slots=True)
class KisKrMarketCollectionResult:
    receipt_count: int
    created_count: int


def collect_kis_kr_market_receipts(
    fetcher: KisKrMarketFetcher,
    store: KisKrMarketReceiptStore,
    request: KisKrMarketCollectionRequest,
) -> KisKrMarketCollectionResult:
    created = 0
    receipts: list[KisKrMarketReceipt] = []
    for kind in _kinds(request.phase):
        requested_at = _current(request)
        source = KisKrMarketFetchRequest(
            kind=kind,
            symbol=request.symbol,
            requested_at=requested_at,
            minute_end_at=_minute_start(requested_at) if kind is KisKrMarketReceiptKind.MINUTE_BARS else None,
        )
        receipt = fetcher.fetch(source)
        created += int(store.append(receipt))
        _require_response(source, receipt)
        receipts.append(receipt)
    return KisKrMarketCollectionResult(len(receipts), created)


def _current(request: KisKrMarketCollectionRequest) -> dt.datetime:
    current = request.clock()
    if not _aware(current):
        raise InvalidKisKrMarketCollectionError
    local = current.astimezone(KST)
    if local.date() != request.session_date:
        raise InvalidKisKrMarketCollectionError
    match request.phase:
        case KisKrMarketCollectionPhase.INTRADAY:
            valid_time = _FIRST_COLLECTION <= local.time() < _COLLECTION_CLOSE
        case KisKrMarketCollectionPhase.EOD_MINUTE:
            valid_time = _COLLECTION_CLOSE <= local.time() < dt.time(15, 31)
        case unreachable:
            assert_never(unreachable)
    if not valid_time:
        raise InvalidKisKrMarketCollectionError
    return current


def _kinds(phase: KisKrMarketCollectionPhase) -> tuple[KisKrMarketReceiptKind, ...]:
    match phase:
        case KisKrMarketCollectionPhase.INTRADAY:
            return _INTRADAY_KINDS
        case KisKrMarketCollectionPhase.EOD_MINUTE:
            return (KisKrMarketReceiptKind.MINUTE_BARS,)
        case unreachable:
            assert_never(unreachable)


def _minute_start(requested_at: dt.datetime) -> dt.datetime:
    local = requested_at.astimezone(KST)
    return local.replace(second=0, microsecond=0) - dt.timedelta(minutes=1)


def _require_response(source: KisKrMarketFetchRequest, receipt: KisKrMarketReceipt) -> None:
    if (
        receipt.kind is not source.kind
        or receipt.symbol != source.symbol
        or receipt.received_at < source.requested_at
        or receipt.received_at - source.requested_at > _MAX_RESPONSE_DELAY
    ):
        raise InvalidKisKrMarketCollectionError
    match receipt.kind:
        case KisKrMarketReceiptKind.MINUTE_BARS:
            envelope = parse_minute_envelope(receipt)
            if source.minute_end_at is None or all(
                parse_bar_start(row) != source.minute_end_at for row in envelope.output2
            ):
                raise InvalidKisKrMarketCollectionError
        case KisKrMarketReceiptKind.PRICE_STATUS:
            _ = parse_price_envelope(receipt)
        case KisKrMarketReceiptKind.ORDER_BOOK:
            _ = parse_quote_envelope(receipt)
        case unreachable:
            assert_never(unreachable)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
