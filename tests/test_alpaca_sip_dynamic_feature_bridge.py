from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_trade_history as history_fixtures
from tests.intraday_feature_kernel_fixtures import bars, identity
from tests.us_volume_profile_fixtures import volume_profile
from trading_agent.alpaca_sip_dynamic_feature_bridge import (
    AlpacaSipDynamicFeatureBridgeError,
    confirm_intraday_feature_with_dynamic_trade,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_trade_history import materialize_alpaca_sip_dynamic_trade_history_as_of
from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus, build_intraday_feature_snapshot

_OFFSET_MS = 35 * 60 * 1_000
_OBSERVED = dynamic_fixtures._NOW + dt.timedelta(milliseconds=_OFFSET_MS + 11)
_INSTRUMENT_ID = "us-eq-a"


def test_complete_history_confirms_latest_trade_against_ready_vwap(tmp_path: Path) -> None:
    first = _trade(101, 100.0)
    second = _trade(103, 102.0)
    history = _history(tmp_path, dynamic_fixtures._frame(first, second))
    snapshot = _snapshot()

    first_confirmation = confirm_intraday_feature_with_dynamic_trade(snapshot, history)
    second_confirmation = confirm_intraday_feature_with_dynamic_trade(snapshot, history)

    assert first_confirmation == second_confirmation
    assert first_confirmation.instrument_id == snapshot.instrument_id
    assert first_confirmation.provider_trade_id == 103
    assert first_confirmation.source_sequence == 4
    assert first_confirmation.source_message_index == 1
    assert first_confirmation.last_trade_price == 102
    assert first_confirmation.vwap == snapshot.vwap
    assert first_confirmation.last_trade_at_or_above_vwap is True
    assert first_confirmation.price_vs_vwap_bps > 0
    assert first_confirmation.complete_history is True


def test_confirmation_rejects_forged_price_vs_vwap_derivation(tmp_path: Path) -> None:
    confirmation = confirm_intraday_feature_with_dynamic_trade(
        _snapshot(),
        _history(tmp_path, dynamic_fixtures._frame(_trade(101, 102.0))),
    )

    with pytest.raises(AlpacaSipDynamicFeatureBridgeError):
        _ = dataclasses.replace(confirmation, price_vs_vwap_bps=Decimal(0))


def test_confirmation_rejects_trade_received_after_observation(tmp_path: Path) -> None:
    confirmation = confirm_intraday_feature_with_dynamic_trade(
        _snapshot(),
        _history(tmp_path, dynamic_fixtures._frame(_trade(101, 102.0))),
    )

    with pytest.raises(AlpacaSipDynamicFeatureBridgeError):
        _ = dataclasses.replace(
            confirmation,
            trade_received_at=confirmation.observed_at + dt.timedelta(microseconds=1),
        )


def test_multi_epoch_history_is_rejected_before_feature_confirmation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = dynamic_fixtures._frame(_trade(101, 101.0))
    history_fixtures._append_epoch(store, "1" * 32, _OFFSET_MS, payload, failed=True)
    history_fixtures._append_epoch(store, "2" * 32, _OFFSET_MS + 20, payload)
    history = materialize_alpaca_sip_dynamic_trade_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=_OBSERVED + dt.timedelta(milliseconds=20),
    )

    with pytest.raises(AlpacaSipDynamicFeatureBridgeError):
        _ = confirm_intraday_feature_with_dynamic_trade(
            dataclasses.replace(_snapshot(), observed_at=history.state.as_of),
            history,
        )


def test_unobserved_terminal_or_blocked_snapshot_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    history_fixtures._append_epoch(
        store,
        "1" * 32,
        _OFFSET_MS,
        dynamic_fixtures._frame(_trade(101, 101.0)),
    )
    early_at = dynamic_fixtures._NOW + dt.timedelta(milliseconds=_OFFSET_MS + 5)
    early_history = materialize_alpaca_sip_dynamic_trade_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=early_at,
    )
    blocked = dataclasses.replace(_snapshot(), status=FeatureSnapshotStatus.BLOCKED_GAP)

    for snapshot, history in (
        (dataclasses.replace(_snapshot(), observed_at=early_at), early_history),
        (blocked, _history(tmp_path / "blocked", dynamic_fixtures._frame(_trade(101, 101.0)))),
    ):
        with pytest.raises(AlpacaSipDynamicFeatureBridgeError):
            _ = confirm_intraday_feature_with_dynamic_trade(snapshot, history)


def test_unbound_instrument_and_old_trade_event_are_rejected(tmp_path: Path) -> None:
    unbound = _history(tmp_path / "unbound", dynamic_fixtures._frame(_trade(101, 101.0, symbol="BBB")))
    old = _trade(101, 101.0)
    old["t"] = "2026-07-17T14:34:59Z"
    old_history = _history(tmp_path / "old", dynamic_fixtures._frame(old))

    for history in (unbound, old_history):
        with pytest.raises(AlpacaSipDynamicFeatureBridgeError):
            _ = confirm_intraday_feature_with_dynamic_trade(_snapshot(), history)


def test_canceled_latest_trade_leaves_no_feature_confirmation(tmp_path: Path) -> None:
    cancel = dynamic_fixtures._cancel()
    cancel["i"] = 101
    cancel["p"] = 101.0
    cancel["s"] = 100
    cancel["t"] = "2026-07-17T14:35:00Z"
    history = _history(tmp_path, dynamic_fixtures._frame(_trade(101, 101.0), cancel))

    with pytest.raises(AlpacaSipDynamicFeatureBridgeError):
        _ = confirm_intraday_feature_with_dynamic_trade(_snapshot(), history)


def _snapshot():
    completed_bars = bars(35, dynamic_fixtures._NOW)
    return build_intraday_feature_snapshot(
        identity(),
        _INSTRUMENT_ID,
        _OBSERVED,
        completed_bars,
        volume_profile(_INSTRUMENT_ID, dynamic_fixtures._NOW.date(), expected_cumulative_volume=10_000),
    )


def _history(tmp_path: Path, payload: bytes):
    store = _store(tmp_path)
    history_fixtures._append_epoch(store, "1" * 32, _OFFSET_MS, payload)
    return materialize_alpaca_sip_dynamic_trade_history_as_of(
        store,
        dynamic_fixtures._plan(),
        as_of=_OBSERVED,
    )


def _store(tmp_path: Path) -> AlpacaSipDynamicReceiptStore:
    return AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")


def _trade(trade_id: int, price: float, *, symbol: str = "AAA"):
    trade = dynamic_fixtures._trade(symbol)
    trade["i"] = trade_id
    trade["p"] = price
    trade["t"] = "2026-07-17T14:35:00Z"
    return trade
