from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_feature_bridge as trade_feature_fixtures
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_trade_history as history_fixtures
from trading_agent.alpaca_sip_dynamic_quote_feature_bridge import (
    AlpacaSipDynamicQuoteFeatureBridgeError,
    confirm_intraday_feature_with_dynamic_quote,
)
from trading_agent.alpaca_sip_dynamic_quote_history import materialize_alpaca_sip_dynamic_quote_history_as_of
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus, IntradayFeatureSnapshot

_OFFSET_MS = 35 * 60 * 1_000
_OBSERVED = dynamic_fixtures._NOW + dt.timedelta(milliseconds=_OFFSET_MS + 11)
_FIRST = "1" * 32
_SECOND = "2" * 32


def test_complete_quote_confirms_causal_microstructure_against_vwap(tmp_path: Path) -> None:
    history = _history(
        tmp_path,
        dynamic_fixtures._frame(
            _quote(100.0, 100.02, bid_size=100, ask_size=200),
            _quote(100.01, 100.03, bid_size=300, ask_size=100),
        ),
    )

    first = confirm_intraday_feature_with_dynamic_quote(_snapshot(), history)
    second = confirm_intraday_feature_with_dynamic_quote(_snapshot(), history)

    assert first == second
    assert first.instrument_id == "us-eq-a"
    assert first.source_sequence == 4
    assert first.source_message_index == 1
    assert first.bid_price == Decimal("100.01")
    assert first.ask_price == Decimal("100.03")
    assert first.midpoint == Decimal("100.02")
    assert first.microprice == Decimal("100.025")
    assert first.order_book_imbalance == Decimal("0.5")
    assert first.spread_bps > 0
    assert first.complete_history is True


def test_wide_quote_is_measured_without_becoming_actionability(tmp_path: Path) -> None:
    history = _history(tmp_path, dynamic_fixtures._frame(_quote(100.0, 101.0)))

    confirmation = confirm_intraday_feature_with_dynamic_quote(_snapshot(), history)

    assert confirmation.spread_bps > 25


def test_quote_at_strict_five_second_boundary_is_stale(tmp_path: Path) -> None:
    observed = dynamic_fixtures._NOW + dt.timedelta(milliseconds=_OFFSET_MS + 5_000)
    store = _store(tmp_path)
    history_fixtures._append_epoch(
        store,
        _FIRST,
        _OFFSET_MS,
        dynamic_fixtures._frame(_quote(100.0, 100.02)),
    )
    history = materialize_alpaca_sip_dynamic_quote_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=observed,
    )

    with pytest.raises(AlpacaSipDynamicQuoteFeatureBridgeError):
        _ = confirm_intraday_feature_with_dynamic_quote(
            dataclasses.replace(_snapshot(), observed_at=observed),
            history,
        )


def test_multi_epoch_history_is_rejected_before_quote_confirmation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = dynamic_fixtures._frame(_quote(100.0, 100.02))
    history_fixtures._append_epoch(store, _FIRST, _OFFSET_MS, payload, failed=True)
    history_fixtures._append_epoch(store, _SECOND, _OFFSET_MS + 20, payload)
    as_of = _OBSERVED + dt.timedelta(milliseconds=20)
    history = materialize_alpaca_sip_dynamic_quote_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=as_of,
    )

    with pytest.raises(AlpacaSipDynamicQuoteFeatureBridgeError):
        _ = confirm_intraday_feature_with_dynamic_quote(
            dataclasses.replace(_snapshot(), observed_at=as_of),
            history,
        )


def test_blocked_snapshot_or_unbound_instrument_is_rejected(tmp_path: Path) -> None:
    blocked = dataclasses.replace(_snapshot(), status=FeatureSnapshotStatus.BLOCKED_GAP)
    unbound = _history(tmp_path / "unbound", dynamic_fixtures._frame(_quote(10.0, 10.01, symbol="BBB")))
    complete = _history(tmp_path / "blocked", dynamic_fixtures._frame(_quote(100.0, 100.02)))

    for snapshot, history in ((blocked, complete), (_snapshot(), unbound)):
        with pytest.raises(AlpacaSipDynamicQuoteFeatureBridgeError):
            _ = confirm_intraday_feature_with_dynamic_quote(snapshot, history)


def test_zero_total_displayed_size_is_rejected(tmp_path: Path) -> None:
    history = _history(
        tmp_path,
        dynamic_fixtures._frame(_quote(100.0, 100.02, bid_size=0, ask_size=0)),
    )

    with pytest.raises(AlpacaSipDynamicQuoteFeatureBridgeError):
        _ = confirm_intraday_feature_with_dynamic_quote(_snapshot(), history)


def _snapshot() -> IntradayFeatureSnapshot:
    return dataclasses.replace(trade_feature_fixtures._snapshot(), observed_at=_OBSERVED)


def _history(tmp_path: Path, payload: bytes):
    store = _store(tmp_path)
    history_fixtures._append_epoch(store, _FIRST, _OFFSET_MS, payload)
    return materialize_alpaca_sip_dynamic_quote_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=_OBSERVED,
    )


def _store(tmp_path: Path) -> AlpacaSipDynamicReceiptStore:
    return AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")


def _quote(
    bid: float,
    ask: float,
    *,
    symbol: str = "AAA",
    bid_size: int = 100,
    ask_size: int = 100,
):
    quote = dynamic_fixtures._quote(symbol, timestamp="2026-07-17T14:35:00Z")
    quote["bp"] = bid
    quote["ap"] = ask
    quote["bs"] = bid_size
    quote["as"] = ask_size
    return quote
