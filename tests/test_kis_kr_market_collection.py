from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.test_kis_kr_market_projection import (
    SESSION,
    _json_body,
    _minute_body,
    _minute_row,
    _price_body,
    _quote_body,
)
from trading_agent.kis_kr_market_client import (
    KisKrMarketFetchRequest,
    KisKrMarketTransportError,
)
from trading_agent.kis_kr_market_collection import (
    KisKrMarketCollectionPhase,
    KisKrMarketCollectionRequest,
    collect_kis_kr_market_receipts,
)
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
)
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore

REQUESTED = SESSION + dt.timedelta(minutes=4, seconds=1)


class _Fetcher:
    def __init__(
        self,
        *,
        fail_on: KisKrMarketReceiptKind | None = None,
        wrong_eod_minute: bool = False,
    ) -> None:
        self.fail_on = fail_on
        self.wrong_eod_minute = wrong_eod_minute
        self.requests: list[KisKrMarketFetchRequest] = []

    def fetch(self, request: KisKrMarketFetchRequest) -> KisKrMarketReceipt:
        self.requests.append(request)
        if request.kind is self.fail_on:
            raise KisKrMarketTransportError
        payloads = {
            KisKrMarketReceiptKind.MINUTE_BARS: _minute_body(),
            KisKrMarketReceiptKind.PRICE_STATUS: _price_body(),
            KisKrMarketReceiptKind.ORDER_BOOK: _quote_body(),
        }
        if (
            request.kind is KisKrMarketReceiptKind.MINUTE_BARS
            and request.minute_end_at is not None
            and request.minute_end_at.hour == 15
            and not self.wrong_eod_minute
        ):
            payloads[KisKrMarketReceiptKind.MINUTE_BARS] = _json_body(
                {
                    "output1": {},
                    "output2": [_minute_row("152900", "100", "101", "99", "100", "100", "10000")],
                }
            )
        delay = 2 if request.kind is KisKrMarketReceiptKind.ORDER_BOOK else 1
        return KisKrMarketReceipt(
            kind=request.kind,
            symbol=request.symbol,
            received_at=request.requested_at + dt.timedelta(seconds=delay),
            status_code=200,
            content_type="application/json",
            raw_payload=payloads[request.kind],
        )


def test_collection_appends_each_valid_raw_response_and_replays(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    fetcher = _Fetcher()
    request = KisKrMarketCollectionRequest(
        symbol="005930",
        session_date=SESSION.date(),
        clock=lambda: REQUESTED,
    )

    first = collect_kis_kr_market_receipts(fetcher, store, request)
    second = collect_kis_kr_market_receipts(fetcher, store, request)

    assert first.receipt_count == 3
    assert first.created_count == 3
    assert second.created_count == 0
    assert len(store.receipts()) == 3
    assert tuple(item.kind for item in fetcher.requests[:3]) == (
        KisKrMarketReceiptKind.MINUTE_BARS,
        KisKrMarketReceiptKind.PRICE_STATUS,
        KisKrMarketReceiptKind.ORDER_BOOK,
    )
    assert fetcher.requests[0].minute_end_at == SESSION + dt.timedelta(minutes=3)


def test_collection_preserves_first_raw_receipt_before_later_transport_failure(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    fetcher = _Fetcher(fail_on=KisKrMarketReceiptKind.PRICE_STATUS)
    request = KisKrMarketCollectionRequest(
        symbol="005930",
        session_date=SESSION.date(),
        clock=lambda: REQUESTED,
    )

    with pytest.raises(KisKrMarketTransportError):
        _ = collect_kis_kr_market_receipts(fetcher, store, request)

    assert tuple(item.kind for item in store.receipts()) == (KisKrMarketReceiptKind.MINUTE_BARS,)


def test_collection_blocks_outside_current_session_before_fetch(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    fetcher = _Fetcher()
    request = KisKrMarketCollectionRequest(
        symbol="005930",
        session_date=SESSION.date(),
        clock=lambda: SESSION - dt.timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="KIS KR market collection input is invalid"):
        _ = collect_kis_kr_market_receipts(fetcher, store, request)

    assert fetcher.requests == []
    assert not store.path.exists()


def test_eod_collection_fetches_only_last_completed_minute(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    fetcher = _Fetcher()
    requested = SESSION.replace(hour=15, minute=30, second=5)
    request = KisKrMarketCollectionRequest(
        symbol="005930",
        session_date=SESSION.date(),
        clock=lambda: requested,
        phase=KisKrMarketCollectionPhase.EOD_MINUTE,
    )

    result = collect_kis_kr_market_receipts(fetcher, store, request)

    assert result.receipt_count == 1
    assert result.created_count == 1
    assert tuple(item.kind for item in fetcher.requests) == (KisKrMarketReceiptKind.MINUTE_BARS,)
    assert fetcher.requests[0].minute_end_at == SESSION.replace(hour=15, minute=29)


def test_eod_collection_preserves_but_rejects_wrong_minute_response(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    fetcher = _Fetcher(wrong_eod_minute=True)
    requested = SESSION.replace(hour=15, minute=30, second=5)
    request = KisKrMarketCollectionRequest(
        symbol="005930",
        session_date=SESSION.date(),
        clock=lambda: requested,
        phase=KisKrMarketCollectionPhase.EOD_MINUTE,
    )

    with pytest.raises(ValueError, match="KIS KR market collection input is invalid"):
        _ = collect_kis_kr_market_receipts(fetcher, store, request)

    assert len(store.receipts()) == 1
