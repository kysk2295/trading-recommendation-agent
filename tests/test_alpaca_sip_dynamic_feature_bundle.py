from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_feature_bridge as trade_fixtures
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests import test_alpaca_sip_dynamic_trade_history as history_fixtures
from trading_agent.alpaca_sip_dynamic_feature_bundle import (
    AlpacaSipDynamicFeatureBundleError,
    build_alpaca_sip_dynamic_feature_bundle,
)
from trading_agent.alpaca_sip_dynamic_quote_history import materialize_alpaca_sip_dynamic_quote_history_as_of
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_trade_history import materialize_alpaca_sip_dynamic_trade_history_as_of

_OFFSET_MS = 35 * 60 * 1_000
_OBSERVED = dynamic_fixtures._NOW + dt.timedelta(milliseconds=_OFFSET_MS + 11)
_FIRST = "1" * 32
_SECOND = "2" * 32


def test_same_epoch_trade_and_quote_build_deterministic_feature_bundle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append(
        store,
        _FIRST,
        dynamic_fixtures._frame(
            quote_fixtures._quote(100.01, 100.03, bid_size=300, ask_size=100),
            trade_fixtures._trade(101, 100.02),
        ),
    )

    first = _bundle(store)
    second = _bundle(store)

    assert first == second
    assert first.trade_confirmation.connection_epoch == _FIRST
    assert first.quote_confirmation.connection_epoch == _FIRST
    assert first.last_trade_vs_midpoint_bps == 0
    assert first.last_trade_inside_quote is True
    assert first.complete_trade_history is True
    assert first.complete_quote_history is True


def test_trade_outside_quote_is_measured_without_becoming_actionability(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append(
        store,
        _FIRST,
        dynamic_fixtures._frame(
            quote_fixtures._quote(100.00, 100.02),
            trade_fixtures._trade(101, 100.05),
        ),
    )

    bundle = _bundle(store)

    assert bundle.last_trade_vs_midpoint_bps > 0
    assert bundle.last_trade_inside_quote is False


def test_independently_complete_epochs_cannot_be_combined(tmp_path: Path) -> None:
    trade_store = _store(tmp_path / "trade")
    quote_store = _store(tmp_path / "quote")
    _append(trade_store, _FIRST, dynamic_fixtures._frame(trade_fixtures._trade(101, 100.02)))
    _append(quote_store, _SECOND, dynamic_fixtures._frame(quote_fixtures._quote(100.01, 100.03)))

    with pytest.raises(AlpacaSipDynamicFeatureBundleError):
        _ = build_alpaca_sip_dynamic_feature_bundle(
            quote_fixtures._snapshot(),
            _trade_history(trade_store),
            _quote_history(quote_store),
        )


def test_bundle_rejects_forged_trade_midpoint_derivation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append(
        store,
        _FIRST,
        dynamic_fixtures._frame(
            quote_fixtures._quote(100.01, 100.03),
            trade_fixtures._trade(101, 100.02),
        ),
    )
    bundle = _bundle(store)

    with pytest.raises(AlpacaSipDynamicFeatureBundleError):
        _ = dataclasses.replace(bundle, last_trade_vs_midpoint_bps=Decimal(1))


def _bundle(store: AlpacaSipDynamicReceiptStore):
    return build_alpaca_sip_dynamic_feature_bundle(
        quote_fixtures._snapshot(),
        _trade_history(store),
        _quote_history(store),
    )


def _trade_history(store: AlpacaSipDynamicReceiptStore):
    return materialize_alpaca_sip_dynamic_trade_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=_OBSERVED,
    )


def _quote_history(store: AlpacaSipDynamicReceiptStore):
    return materialize_alpaca_sip_dynamic_quote_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=_OBSERVED,
    )


def _append(store: AlpacaSipDynamicReceiptStore, epoch: str, payload: bytes) -> None:
    history_fixtures._append_epoch(store, epoch, _OFFSET_MS, payload)


def _store(tmp_path: Path) -> AlpacaSipDynamicReceiptStore:
    return AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")
