from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_projection as fixtures
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicRawReceipt,
    AlpacaSipDynamicReceiptKind,
)
from trading_agent.alpaca_sip_dynamic_trade_state import (
    AlpacaSipDynamicTradeStateError,
    materialize_alpaca_sip_dynamic_trades_as_of,
)


def test_correction_replaces_active_values_and_preserves_provider_aliases(tmp_path: Path) -> None:
    store = fixtures._store(
        tmp_path,
        fixtures._frame(fixtures._trade("AAA"), fixtures._correction()),
    )

    state = materialize_alpaca_sip_dynamic_trades_as_of(
        store,
        fixtures._plan(),
        fixtures._EPOCH,
        as_of=fixtures._NOW + dt.timedelta(milliseconds=4),
    )

    assert state.validated_trade_message_count == 2
    assert state.observed_trade_message_count == 2
    assert state.duplicate_trade_message_count == 0
    assert len(state.active_trades) == 1
    active = state.active_trades[0]
    assert active.provider_root_trade_id == 101
    assert active.current_trade_id == 102
    assert active.trade_id_aliases == (101, 102)
    assert active.price == Decimal("10.01")
    assert active.size == 90
    assert active.conditions == ("@",)


def test_cancel_can_target_original_alias_after_correction(tmp_path: Path) -> None:
    cancel = fixtures._cancel()
    cancel["i"] = 101
    store = fixtures._store(
        tmp_path,
        fixtures._frame(fixtures._trade("AAA"), fixtures._correction(), cancel),
    )

    state = _materialize(store)

    assert state.validated_trade_message_count == 3
    assert state.active_trades == ()


def test_as_of_excludes_later_received_correction(tmp_path: Path) -> None:
    store = fixtures._store(tmp_path, fixtures._frame(fixtures._trade("AAA")))
    _append_data(store, fixtures._frame(fixtures._correction()), sequence=5, milliseconds=5)

    state = materialize_alpaca_sip_dynamic_trades_as_of(
        store,
        fixtures._plan(),
        fixtures._EPOCH,
        as_of=fixtures._NOW + dt.timedelta(milliseconds=4),
    )

    assert state.validated_trade_message_count == 2
    assert state.observed_trade_message_count == 1
    assert state.active_trades[0].current_trade_id == 101
    assert state.active_trades[0].price == Decimal("10.0")


@pytest.mark.parametrize("payload", ("missing", "mismatch", "after_cancel", "resurrected"))
def test_invalid_trade_chain_fails_closed(tmp_path: Path, payload: str) -> None:
    messages = _invalid_messages(payload)
    store = fixtures._store(tmp_path, fixtures._frame(*messages))

    with pytest.raises(AlpacaSipDynamicTradeStateError):
        _ = _materialize(store)


def test_received_time_regression_fails_closed(tmp_path: Path) -> None:
    store = fixtures._store(tmp_path, fixtures._frame(fixtures._trade("AAA")))
    _append_data(store, fixtures._frame(fixtures._correction()), sequence=5, milliseconds=3)

    with pytest.raises(AlpacaSipDynamicTradeStateError):
        _ = _materialize(store)


def test_quote_only_replay_has_no_active_trade(tmp_path: Path) -> None:
    store = fixtures._store(tmp_path, fixtures._frame(fixtures._quote("BBB")))

    state = _materialize(store)

    assert state.validated_trade_message_count == 0
    assert state.observed_trade_message_count == 0
    assert state.active_trades == ()


def _materialize(store):
    return materialize_alpaca_sip_dynamic_trades_as_of(
        store,
        fixtures._plan(),
        fixtures._EPOCH,
        as_of=fixtures._NOW + dt.timedelta(seconds=1),
    )


def _append_data(store, payload: bytes, *, sequence: int, milliseconds: int) -> None:
    _ = store.append_raw(
        fixtures._plan(),
        AlpacaSipDynamicRawReceipt(
            fixtures._EPOCH,
            sequence,
            fixtures._NOW + dt.timedelta(milliseconds=milliseconds),
            AlpacaSipDynamicReceiptKind.DATA,
            payload,
        ),
    )


def _invalid_messages(case: str):
    correction = fixtures._correction()
    if case == "missing":
        return (correction,)
    if case == "mismatch":
        correction["op"] = 20.0
        return (fixtures._trade("AAA"), correction)
    if case == "after_cancel":
        cancel = fixtures._cancel()
        cancel["i"] = 101
        cancel["p"] = 10.0
        cancel["s"] = 100
        return (fixtures._trade("AAA"), cancel, correction)
    if case == "resurrected":
        cancel = fixtures._cancel()
        cancel["i"] = 101
        cancel["p"] = 10.0
        cancel["s"] = 100
        conflict = fixtures._trade("AAA")
        conflict["p"] = 11.0
        return (fixtures._trade("AAA"), cancel, conflict)
    raise AssertionError
