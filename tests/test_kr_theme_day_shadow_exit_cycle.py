from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kis_kr_market_projection import (
    SESSION,
    _json_body,
    _minute_body,
    _minute_row,
    _opportunity,
    _price_body,
    _quote_body,
    _receipt,
)
from tests.test_kr_theme_day_intraday import _request
from tests.test_kr_theme_day_shadow_entry import _ledger
from trading_agent.kis_kr_market_models import KisKrMarketReceipt, KisKrMarketReceiptKind
from trading_agent.kis_kr_market_receipt_store import KisKrMarketReceiptStore
from trading_agent.kr_theme_day_intraday import run_kr_theme_day_intraday_entry
from trading_agent.kr_theme_day_shadow_entry_store import KrThemeDayShadowEntryStore
from trading_agent.kr_theme_day_shadow_exit_cycle import (
    KrThemeDayShadowExitCycleRequest,
    KrThemeDayShadowExitStores,
    run_kr_theme_day_shadow_exit_cycle,
)
from trading_agent.kr_theme_day_shadow_exit_store import KrThemeDayShadowExitStore


def test_exit_cycle_projects_target_and_skips_terminal_entry_on_restart(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    receipt_store = _entry_receipts(tmp_path)
    entry_store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    _ = run_kr_theme_day_intraday_entry(ledger, receipt_store, entry_store, _request())
    assert receipt_store.append(_exit_receipt(high="107")) is True
    exit_store = KrThemeDayShadowExitStore(tmp_path / "exits.sqlite3")
    trial_id = ledger.multi_market_trials()[0].registration.trial_id
    request = KrThemeDayShadowExitCycleRequest(
        trial_id=trial_id,
        evaluated_at=SESSION + dt.timedelta(minutes=6, seconds=3),
    )

    stores = KrThemeDayShadowExitStores(receipt_store, entry_store, exit_store)
    first = run_kr_theme_day_shadow_exit_cycle(stores, request)
    second = run_kr_theme_day_shadow_exit_cycle(stores, request)

    assert first.open_entry_count == 1
    assert first.created_exit_count == 1
    assert first.pending_entry_count == 0
    assert second.open_entry_count == 0
    assert second.created_exit_count == 0
    assert second.terminal_entry_count == 1
    assert len(exit_store.exits()) == 1


def test_exit_cycle_keeps_nonterminal_entry_pending(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "experiment.sqlite3")
    receipt_store = _entry_receipts(tmp_path)
    entry_store = KrThemeDayShadowEntryStore(tmp_path / "entries.sqlite3")
    _ = run_kr_theme_day_intraday_entry(ledger, receipt_store, entry_store, _request())
    assert receipt_store.append(_exit_receipt(high="105")) is True
    exit_store = KrThemeDayShadowExitStore(tmp_path / "exits.sqlite3")
    request = KrThemeDayShadowExitCycleRequest(
        trial_id=ledger.multi_market_trials()[0].registration.trial_id,
        evaluated_at=SESSION + dt.timedelta(minutes=6, seconds=3),
    )

    result = run_kr_theme_day_shadow_exit_cycle(
        KrThemeDayShadowExitStores(receipt_store, entry_store, exit_store),
        request,
    )

    assert result.created_exit_count == 0
    assert result.pending_entry_count == 1
    assert not exit_store.path.exists()


def _entry_receipts(tmp_path: Path) -> KisKrMarketReceiptStore:
    store = KisKrMarketReceiptStore(tmp_path / "receipts.sqlite3")
    assert store.append(_receipt(KisKrMarketReceiptKind.MINUTE_BARS, _minute_body(), seconds=2))
    assert store.append(_receipt(KisKrMarketReceiptKind.PRICE_STATUS, _price_body(), seconds=2))
    assert store.append(_receipt(KisKrMarketReceiptKind.ORDER_BOOK, _quote_body(), seconds=3))
    return store


def _exit_receipt(*, high: str) -> KisKrMarketReceipt:
    rows = (
        _minute_row("090000", "100", "101", "99", "101", "100", "10000"),
        _minute_row("090100", "101", "103", "100", "102", "100", "20100"),
        _minute_row("090200", "102", "102", "100", "100.8", "100", "30180"),
        _minute_row("090300", "101", "104", "101", "103", "180", "48540"),
        _minute_row("090400", "103", "104", "102", "103", "100", "58840"),
        _minute_row("090500", "103", high, "102", "104", "100", "69240"),
        _minute_row("090600", "104", "105", "103", "104", "10", "70280"),
    )
    return KisKrMarketReceipt(
        kind=KisKrMarketReceiptKind.MINUTE_BARS,
        symbol=_opportunity().candidates[0].symbol,
        received_at=SESSION + dt.timedelta(minutes=6, seconds=2),
        status_code=200,
        content_type="application/json",
        raw_payload=_json_body({"output1": {}, "output2": list(reversed(rows))}),
    )
