from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol, assert_never, override
from zoneinfo import ZoneInfo

from trading_agent.kis_kr_market_client import KisKrMarketFetchRequest
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
)
from trading_agent.kis_kr_market_parsing import (
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
_KINDS: Final = (
    KisKrMarketReceiptKind.MINUTE_BARS,
    KisKrMarketReceiptKind.PRICE_STATUS,
    KisKrMarketReceiptKind.ORDER_BOOK,
)


class InvalidKisKrMarketCollectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR market collection input is invalid"


class KisKrMarketFetcher(Protocol):
    def fetch(self, source: KisKrMarketFetchRequest, /) -> KisKrMarketReceipt: ...


@dataclass(frozen=True, slots=True)
class KisKrMarketCollectionRequest:
    symbol: str
    session_date: dt.date
    clock: Callable[[], dt.datetime]

    def __post_init__(self) -> None:
        if not is_kr_instrument_symbol_v2(self.symbol) or type(self.session_date) is not dt.date:
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
    for kind in _KINDS:
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
    if local.date() != request.session_date or local.time() < _FIRST_COLLECTION or local.time() >= _COLLECTION_CLOSE:
        raise InvalidKisKrMarketCollectionError
    return current


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
            _ = parse_minute_envelope(receipt)
        case KisKrMarketReceiptKind.PRICE_STATUS:
            _ = parse_price_envelope(receipt)
        case KisKrMarketReceiptKind.ORDER_BOOK:
            _ = parse_quote_envelope(receipt)
        case unreachable:
            assert_never(unreachable)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
