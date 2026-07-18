from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from trading_agent import alpaca_sip_trade_history_coverage as coverage_module
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_history import (
    AlpacaSipTradeHistoryRequest,
    AlpacaSipTradeInstrumentBinding,
    project_alpaca_sip_trade_history,
)
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamError,
    AlpacaSipTradeStreamStores,
    open_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipStreamTerminalStatus
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)


def test_session_evidence_when_disconnect_follows_data_preserves_failed_receipt_scope(
    tmp_path: Path,
) -> None:
    # Given
    connection = FakeRecoveryConnection([_connected(), _authenticated(), _subscribed(), _trade(), TimeoutError()])
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    trades = AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3")
    epoch = ""
    receipt_id = ""

    # When
    with (
        pytest.raises(AlpacaSipTradeStreamError),
        open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
            AlpacaSipTradeStreamStores(controls, trades),
            connector=_connector(connection),
            _clock=iter(_times(8)).__next__,
        ) as stream,
    ):
        stored = stream.receive_trade_frame(1.0)
        epoch = stream.connection_epoch
        receipt_id = stored.receipt_id
        _ = stream.receive_trade_frame(1.0)
    evidence = controls.load_session_evidence(epoch)

    # Then
    assert evidence is not None
    assert evidence.status is AlpacaSipStreamTerminalStatus.FAILED
    assert evidence.receipt_ids == (receipt_id,)
    assert evidence.config.symbol == "AAPL"


def test_session_history_when_reconnect_succeeds_keeps_distinct_ordered_epochs(
    tmp_path: Path,
) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    trades = AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3")
    stores = AlpacaSipTradeStreamStores(controls, trades)
    failed_connection = FakeRecoveryConnection(
        [_connected(), _authenticated(), _subscribed(), _trade(), TimeoutError()]
    )
    with (
        pytest.raises(AlpacaSipTradeStreamError),
        open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
            stores,
            connector=_connector(failed_connection),
            _clock=iter(_times(8)).__next__,
        ) as stream,
    ):
        _ = stream.receive_trade_frame(1.0)
        _ = stream.receive_trade_frame(1.0)
    recovered_connection = FakeRecoveryConnection(
        [_connected(), _authenticated(), _subscribed(), _correction_and_cancel()]
    )

    # When
    with open_alpaca_sip_trade_stream(
        AlpacaCredentials("fixture-key", "fixture-secret"),
        AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
        stores,
        connector=_connector(recovered_connection),
        _clock=iter(_times(8, offset=dt.timedelta(seconds=1))).__next__,
    ) as stream:
        _ = stream.receive_trade_frame(1.0)
    history = controls.load_session_history(AlpacaSipTradeStreamConfig(_DATE, "AAPL"))

    # Then
    assert tuple(item.status for item in history) == (
        AlpacaSipStreamTerminalStatus.FAILED,
        AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE,
    )
    assert history[0].connection_epoch != history[1].connection_epoch
    assert tuple(len(item.receipt_ids) for item in history) == (1, 1)


def test_multi_epoch_coverage_keeps_disconnect_gap_unattested(tmp_path: Path) -> None:
    # Given
    controls, trades = _capture_reconnected_history(tmp_path)
    history = controls.load_session_history(AlpacaSipTradeStreamConfig(_DATE, "AAPL"))
    batch = project_alpaca_sip_trade_history(
        trades.load_frames(_DATE),
        AlpacaSipTradeHistoryRequest(
            _DATE,
            (AlpacaSipTradeInstrumentBinding("AAPL", "us-equity-aapl"),),
        ),
    )

    # When
    coverage = coverage_module.assess_alpaca_sip_multi_epoch_trade_history_coverage(
        batch,
        history,
    )

    # Then
    assert coverage.raw_first_verified is True
    assert coverage.correction_observed is True
    assert coverage.tombstone_observed is True
    assert coverage.continuity_attested is False
    assert coverage.complete_history is False
    assert coverage.reason_codes == ("continuity_unattested",)


def _capture_reconnected_history(
    tmp_path: Path,
) -> tuple[AlpacaSipTradeStreamStore, AlpacaSipTradeHistoryStore]:
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    trades = AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3")
    stores = AlpacaSipTradeStreamStores(controls, trades)
    with (
        pytest.raises(AlpacaSipTradeStreamError),
        open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
            stores,
            connector=_connector(
                FakeRecoveryConnection([_connected(), _authenticated(), _subscribed(), _trade(), TimeoutError()])
            ),
            _clock=iter(_times(8)).__next__,
        ) as stream,
    ):
        _ = stream.receive_trade_frame(1.0)
        _ = stream.receive_trade_frame(1.0)
    with open_alpaca_sip_trade_stream(
        AlpacaCredentials("fixture-key", "fixture-secret"),
        AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
        stores,
        connector=_connector(
            FakeRecoveryConnection([_connected(), _authenticated(), _subscribed(), _correction_and_cancel()])
        ),
        _clock=iter(_times(8, offset=dt.timedelta(seconds=1))).__next__,
    ) as stream:
        _ = stream.receive_trade_frame(1.0)
    return controls, trades


class FakeRecoveryConnection:
    __slots__ = ("_responses", "final_url")

    def __init__(self, responses: list[bytes | Exception]) -> None:
        self._responses = responses
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _connector(connection: FakeRecoveryConnection):
    @contextmanager
    def connector(_: str) -> Iterator[FakeRecoveryConnection]:
        yield connection

    return connector


def _times(
    count: int,
    *,
    offset: dt.timedelta = dt.timedelta(0),
) -> tuple[dt.datetime, ...]:
    return tuple(_NOW + offset + dt.timedelta(milliseconds=index) for index in range(count))


def _connected() -> bytes:
    return b'[{"T":"success","msg":"connected"}]'


def _authenticated() -> bytes:
    return b'[{"T":"success","msg":"authenticated"}]'


def _subscribed() -> bytes:
    return (
        b'[{"T":"subscription","trades":["AAPL"],"quotes":[],"bars":[],"updatedBars":[],'
        b'"dailyBars":[],"statuses":[],"lulds":[],"corrections":["AAPL"],'
        b'"cancelErrors":["AAPL"]}]'
    )


def _trade() -> bytes:
    return (
        b'[{"T":"t","S":"AAPL","i":101,"x":"V","p":211.25,"s":40,"c":["@"],"t":"2026-07-17T14:29:59.123456Z","z":"C"}]'
    )


def _correction_and_cancel() -> bytes:
    return (
        b'[{"T":"c","S":"AAPL","x":"V","oi":101,"op":211.25,"os":40,"oc":["@"],'
        b'"ci":102,"cp":211.2,"cs":35,"cc":["@"],"t":"2026-07-17T14:30:00.123456Z","z":"C"},'
        b'{"T":"x","S":"AAPL","i":102,"x":"V","p":211.2,"s":35,"a":"C",'
        b'"t":"2026-07-17T14:30:00.123456Z","z":"C"}]'
    )
