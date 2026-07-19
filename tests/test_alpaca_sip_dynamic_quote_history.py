from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_projection as fixtures
from tests import test_alpaca_sip_dynamic_trade_history as history_fixtures
from trading_agent.alpaca_sip_dynamic_quote_history import (
    AlpacaSipDynamicIncompleteQuoteHistoryError,
    AlpacaSipDynamicQuoteHistoryError,
    materialize_alpaca_sip_dynamic_quote_history_as_of,
    require_complete_alpaca_sip_dynamic_quote_history,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore

_FIRST = "1" * 32
_SECOND = "2" * 32


def test_single_complete_epoch_materializes_latest_quote_per_instrument(tmp_path: Path) -> None:
    store = _store(tmp_path)
    older = _quote("AAA", "2026-07-17T13:59:59Z", bid=10.0, ask=10.01)
    latest = _quote("AAA", "2026-07-17T14:00:00Z", bid=10.02, ask=10.03)
    history_fixtures._append_epoch(
        store,
        _FIRST,
        0,
        fixtures._frame(older, _quote("BBB", "2026-07-17T14:00:00Z"), latest),
    )

    history = _materialize(store)
    complete = require_complete_alpaca_sip_dynamic_quote_history(history)

    assert complete.complete_history is True
    assert complete.state.validated_quote_message_count == 3
    assert complete.state.observed_quote_message_count == 3
    assert tuple(item.instrument_id for item in complete.state.latest_quotes) == ("us-eq-a", "us-eq-b")
    assert complete.state.latest_quotes[0].bid_price == Decimal("10.02")
    assert complete.state.latest_quotes[0].source_message_index == 2


def test_quote_received_after_as_of_is_not_observed_and_terminal_is_incomplete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    history_fixtures._append_epoch(
        store,
        _FIRST,
        0,
        fixtures._frame(_quote("AAA", "2026-07-17T14:00:00Z")),
    )

    history = materialize_alpaca_sip_dynamic_quote_history_as_of(
        store,
        fixtures._plan(),
        as_of=fixtures._NOW + dt.timedelta(milliseconds=3),
    )

    assert history.state.observed_quote_message_count == 0
    assert history.state.latest_quotes == ()
    assert history.terminal_observed is False
    with pytest.raises(AlpacaSipDynamicIncompleteQuoteHistoryError):
        _ = require_complete_alpaca_sip_dynamic_quote_history(history)


def test_reconnect_quotes_replay_but_complete_history_stays_blocked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = fixtures._frame(_quote("AAA", "2026-07-17T14:00:00Z", bid=10.0, ask=10.01))
    second = fixtures._frame(_quote("AAA", "2026-07-17T14:00:00.020Z", bid=10.02, ask=10.03))
    history_fixtures._append_epoch(store, _FIRST, 0, first, failed=True)
    history_fixtures._append_epoch(store, _SECOND, 20, second)

    history = _materialize(store)

    assert history.state.connection_epochs == (_FIRST, _SECOND)
    assert history.state.latest_quotes[0].current_connection_epoch == _SECOND
    assert history.gap_count == 1
    assert history.complete_history is False
    with pytest.raises(AlpacaSipDynamicIncompleteQuoteHistoryError):
        _ = require_complete_alpaca_sip_dynamic_quote_history(history)


def test_crossed_quote_fails_closed_during_history_materialization(tmp_path: Path) -> None:
    store = _store(tmp_path)
    crossed = _quote("AAA", "2026-07-17T14:00:00Z", bid=10.02, ask=10.01)
    history_fixtures._append_epoch(store, _FIRST, 0, fixtures._frame(crossed))

    with pytest.raises(AlpacaSipDynamicQuoteHistoryError):
        _ = _materialize(store)


def _store(tmp_path: Path) -> AlpacaSipDynamicReceiptStore:
    return AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")


def _materialize(store: AlpacaSipDynamicReceiptStore):
    return materialize_alpaca_sip_dynamic_quote_history_as_of(
        store,
        fixtures._plan(),
        as_of=fixtures._NOW + dt.timedelta(seconds=1),
    )


def _quote(
    symbol: str,
    timestamp: str,
    *,
    bid: float = 10.0,
    ask: float = 10.01,
):
    quote = fixtures._quote(symbol, timestamp=timestamp)
    quote["bp"] = bid
    quote["ap"] = ask
    return quote
