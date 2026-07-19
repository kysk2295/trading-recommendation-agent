from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from tests.test_kis_kr_market_projection import (
    SESSION,
    _minute_body,
    _opportunity,
    _price_body,
    _quote_body,
    _receipt,
)
from tests.test_kr_theme_day_shadow_entry import VERSION, _ledger
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_intraday import (
    KrThemeDayIntradayEntryRequest,
    KrThemeDayIntradayStatus,
    run_kr_theme_day_intraday_entry,
)
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore


def test_intraday_entry_replays_raw_receipts_into_trial_bound_shadow_entry(tmp_path: Path) -> None:
    receipt_store = _receipt_store(tmp_path)
    entry_store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    request = _request()

    first = run_kr_theme_day_intraday_entry(ledger, receipt_store, entry_store, request)
    second = run_kr_theme_day_intraday_entry(ledger, receipt_store, entry_store, request)

    assert first.status is KrThemeDayIntradayStatus.ENTRY_CREATED
    assert second.status is KrThemeDayIntradayStatus.ENTRY_REPLAYED
    assert first.signal is not None
    assert first.signal.entry_price == Decimal("103")
    assert first.entry is not None
    assert first.entry.fill_price == Decimal("103.206")
    assert entry_store.entries() == (first.entry,)


def test_intraday_entry_records_no_setup_without_creating_entry(tmp_path: Path) -> None:
    receipt_store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    no_reclaim = _minute_body(excluded_hour="090300")
    assert receipt_store.append(_receipt(KisKrMarketReceiptKind.MINUTE_BARS, no_reclaim, seconds=2))
    assert receipt_store.append(_receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=2))
    assert receipt_store.append(_receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=3))
    entry_store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")

    outcome = run_kr_theme_day_intraday_entry(
        _ledger(tmp_path / "experiment.sqlite3"),
        receipt_store,
        entry_store,
        _request(),
    )

    assert outcome.status is KrThemeDayIntradayStatus.NO_SETUP
    assert outcome.signal is None
    assert outcome.entry is None
    assert not entry_store.path.exists()


def _receipt_store(tmp_path: Path) -> KisKrMarketReceiptStore:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    assert store.append(_receipt(KisKrMarketReceiptKind.MINUTE_BARS, _minute_body(), seconds=2))
    assert store.append(_receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=2))
    assert store.append(_receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=3))
    return store


def _request() -> KrThemeDayIntradayEntryRequest:
    evaluated_at = SESSION + dt.timedelta(minutes=4, seconds=4)
    return KrThemeDayIntradayEntryRequest(
        opportunity=_opportunity(),
        producer_strategy_version=VERSION,
        evaluated_at=evaluated_at,
        filled_at=evaluated_at + dt.timedelta(seconds=1),
        max_slippage_bps=Decimal("20"),
    )
