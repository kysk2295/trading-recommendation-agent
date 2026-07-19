from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_projection as fixtures
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicRawReceipt,
    AlpacaSipDynamicReceiptKind,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
from trading_agent.alpaca_sip_dynamic_trade_history import (
    AlpacaSipDynamicIncompleteTradeHistoryError,
    AlpacaSipDynamicTradeHistoryError,
    materialize_alpaca_sip_dynamic_trade_history_as_of,
    require_complete_alpaca_sip_dynamic_trade_history,
)

_FIRST = "1" * 32
_SECOND = "2" * 32


def test_reconnect_chain_replays_but_remains_continuity_unattested(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_epoch(store, _FIRST, 0, fixtures._frame(fixtures._trade("AAA")), failed=True)
    _append_epoch(store, _SECOND, 20, fixtures._frame(fixtures._correction(), fixtures._cancel()))

    history = _materialize(store)

    assert history.state.connection_epochs == (_FIRST, _SECOND)
    assert history.state.validated_trade_message_count == 3
    assert history.state.active_trades == ()
    assert history.gap_count == 1
    assert history.continuity_attested is False
    assert history.complete_history is False
    assert history.reason_codes == ("continuity_unattested",)
    with pytest.raises(AlpacaSipDynamicIncompleteTradeHistoryError):
        _ = require_complete_alpaca_sip_dynamic_trade_history(history)


def test_exact_duplicate_across_reconnect_is_counted_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = fixtures._frame(fixtures._trade("AAA"))
    _append_epoch(store, _FIRST, 0, original, failed=True)
    _append_epoch(store, _SECOND, 20, original)

    history = _materialize(store)

    assert history.state.validated_trade_message_count == 2
    assert history.state.duplicate_trade_message_count == 1
    assert len(history.state.active_trades) == 1
    assert history.state.active_trades[0].current_trade_id == 101


def test_conflicting_duplicate_provider_trade_id_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_epoch(store, _FIRST, 0, fixtures._frame(fixtures._trade("AAA")), failed=True)
    conflict = fixtures._trade("AAA")
    conflict["p"] = 11.0
    _append_epoch(store, _SECOND, 20, fixtures._frame(conflict))

    with pytest.raises(AlpacaSipDynamicTradeHistoryError):
        _ = _materialize(store)


def test_overlapping_epoch_receipt_time_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_epoch(store, _FIRST, 0, fixtures._frame(fixtures._trade("AAA")), failed=True)
    _append_epoch(store, _SECOND, 5, fixtures._frame(fixtures._correction()))

    with pytest.raises(AlpacaSipDynamicTradeHistoryError):
        _ = _materialize(store)


def test_single_bounded_epoch_passes_complete_history_gate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_epoch(store, _FIRST, 0, fixtures._frame(fixtures._trade("AAA")))

    history = _materialize(store)
    complete = require_complete_alpaca_sip_dynamic_trade_history(history)

    assert complete.gap_count == 0
    assert complete.terminal_observed is True
    assert complete.continuity_attested is True
    assert complete.complete_history is True
    assert complete.reason_codes == ()


def test_control_only_failed_epoch_is_preserved_without_projection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_epoch(store, _FIRST, 0, None, failed=True)
    _append_epoch(store, _SECOND, 20, fixtures._frame(fixtures._trade("AAA")))

    history = _materialize(store)

    assert history.state.connection_epochs == (_FIRST, _SECOND)
    assert history.state.validated_trade_message_count == 1
    assert history.complete_history is False


def test_single_complete_before_terminal_time_remains_incomplete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_epoch(store, _FIRST, 0, fixtures._frame(fixtures._trade("AAA")))

    history = materialize_alpaca_sip_dynamic_trade_history_as_of(
        store,
        fixtures._plan(),
        as_of=fixtures._NOW + dt.timedelta(milliseconds=5),
    )

    assert history.terminal_observed is False
    assert history.continuity_attested is False
    assert history.complete_history is False
    with pytest.raises(AlpacaSipDynamicIncompleteTradeHistoryError):
        _ = require_complete_alpaca_sip_dynamic_trade_history(history)


def test_epoch_after_bounded_complete_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_epoch(store, _FIRST, 0, fixtures._frame(fixtures._trade("AAA")))
    _append_epoch(store, _SECOND, 20, fixtures._frame(fixtures._trade("BBB")), failed=True)

    with pytest.raises(AlpacaSipDynamicTradeHistoryError):
        _ = _materialize(store)


def _store(tmp_path: Path) -> AlpacaSipDynamicReceiptStore:
    return AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")


def _append_epoch(
    store: AlpacaSipDynamicReceiptStore,
    epoch: str,
    offset_ms: int,
    data: bytes | None,
    *,
    failed: bool = False,
) -> None:
    plan = fixtures._plan()
    store.bind_connection(epoch, plan, fixtures._NOW + dt.timedelta(milliseconds=offset_ms))
    controls = (fixtures._connected(), fixtures._authenticated(), fixtures._ack())
    payloads = controls if data is None else (*controls, data)
    for sequence, payload in enumerate(payloads, start=1):
        _ = store.append_raw(
            plan,
            AlpacaSipDynamicRawReceipt(
                epoch,
                sequence,
                fixtures._NOW + dt.timedelta(milliseconds=offset_ms + sequence),
                AlpacaSipDynamicReceiptKind.CONTROL if sequence <= 3 else AlpacaSipDynamicReceiptKind.DATA,
                payload,
            ),
        )
    status = AlpacaSipDynamicTerminalStatus.FAILED if failed else AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE
    _ = AlpacaSipDynamicTerminalStore(store.path).append(
        plan,
        epoch,
        fixtures._NOW + dt.timedelta(milliseconds=offset_ms + 10),
        status,
    )


def _materialize(store: AlpacaSipDynamicReceiptStore):
    return materialize_alpaca_sip_dynamic_trade_history_as_of(
        store,
        fixtures._plan(),
        as_of=fixtures._NOW + dt.timedelta(seconds=1),
    )
