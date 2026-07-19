from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.test_kis_kr_market_projection import (
    _minute_body,
    _price_body,
    _quote_body,
    _receipt,
)
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import (
    InvalidKisKrMarketReceiptStoreError,
    KisKrMarketReceiptStore,
)


def test_store_appends_raw_receipts_before_projection_and_replays(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    receipts = (
        _receipt(KisKrMarketReceiptKind.MINUTE_BARS, _minute_body(), seconds=1),
        _receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=2),
        _receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=3),
    )

    first = tuple(store.append(receipt) for receipt in receipts)
    second = tuple(store.append(receipt) for receipt in receipts)

    assert first == (True, True, True)
    assert second == (False, False, False)
    assert store.receipts() == receipts
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_store_rejects_conflicting_logical_receipt(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    receipt = _receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=2)
    conflicting = _receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(vi_code="9"), seconds=2)
    assert store.append(receipt) is True

    with pytest.raises(InvalidKisKrMarketReceiptStoreError):
        _ = store.append(conflicting)

    assert store.receipts() == (receipt,)


def test_store_detects_schema_tamper_and_public_mode(tmp_path: Path) -> None:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    receipt = _receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=2)
    assert store.append(receipt) is True
    with sqlite3.connect(store.path) as connection:
        _ = connection.execute("DROP TRIGGER kis_kr_market_receipts_no_update")
        connection.commit()

    with pytest.raises(InvalidKisKrMarketReceiptStoreError):
        _ = store.receipts()

    private_store = KisKrMarketReceiptStore(tmp_path / "public.sqlite3")
    assert private_store.append(receipt) is True
    private_store.path.chmod(0o644)
    with pytest.raises(InvalidKisKrMarketReceiptStoreError):
        _ = private_store.receipts()
