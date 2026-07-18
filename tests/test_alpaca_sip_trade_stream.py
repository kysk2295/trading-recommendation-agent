from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_history import (
    AlpacaSipTradeHistoryRequest,
    AlpacaSipTradeInstrumentBinding,
    project_alpaca_sip_trade_history,
)
from trading_agent.alpaca_sip_trade_history_coverage import (
    assess_alpaca_sip_bounded_trade_history_coverage,
)
from trading_agent.alpaca_sip_trade_models import AlpacaSipTradeHistoryError
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamEndpointError,
    AlpacaSipTradeStreamProtocolError,
    AlpacaSipTradeStreamStores,
    open_alpaca_sip_trade_stream,
    require_alpaca_sip_trade_stream_url,
)
from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipStreamTerminalStatus
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)


class FakeSipConnection:
    __slots__ = ("_responses", "final_url", "sent")

    def __init__(self, responses: list[bytes], final_url: str = ALPACA_SIP_TRADE_STREAM_URL) -> None:
        self._responses = responses
        self.final_url = final_url
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self._responses.pop(0)


def test_stream_when_trade_subscription_is_exact_persists_bounded_complete_history(tmp_path: Path) -> None:
    # Given
    connection = FakeSipConnection([_connected(), _authenticated(), _subscribed(), _trade_chain()])
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    trades = AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3")
    times = iter(_times(6))

    # When
    with open_alpaca_sip_trade_stream(
        _credentials(),
        AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
        AlpacaSipTradeStreamStores(controls, trades),
        connector=_connector(connection),
        _clock=times.__next__,
    ) as stream:
        stored = stream.receive_trade_frame(1.0)
        epoch = stream.connection_epoch
    attestation = controls.load_attestation(epoch)
    assert attestation is not None
    batch = project_alpaca_sip_trade_history(
        (stored,),
        AlpacaSipTradeHistoryRequest(
            _DATE,
            (AlpacaSipTradeInstrumentBinding("AAPL", "us-equity-aapl"),),
        ),
    )
    coverage = assess_alpaca_sip_bounded_trade_history_coverage(batch, attestation)

    # Then
    assert json.loads(connection.sent[0]) == {
        "action": "auth",
        "key": "fixture-key",
        "secret": "fixture-secret",
    }
    assert json.loads(connection.sent[1]) == {"action": "subscribe", "trades": ["AAPL"]}
    assert controls.control_count() == 3
    assert controls.data_link_count(epoch) == 1
    assert attestation.completed_at == stored.received_at
    assert coverage.complete_history is True
    assert coverage.reason_codes == ()


def test_stream_when_subscription_omits_correction_channel_fails_after_raw_receipt(tmp_path: Path) -> None:
    # Given
    subscription = json.loads(_subscribed())
    subscription[0]["corrections"] = []
    connection = FakeSipConnection([_connected(), _authenticated(), json.dumps(subscription).encode()])
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")

    # When / Then
    with (
        pytest.raises(AlpacaSipTradeStreamProtocolError),
        open_alpaca_sip_trade_stream(
            _credentials(),
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
            AlpacaSipTradeStreamStores(
                controls,
                AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
            ),
            connector=_connector(connection),
            _clock=iter(_times(4)).__next__,
        ),
    ):
        pass
    assert controls.control_count() == 3


def test_stream_when_data_frame_is_not_trade_preserves_raw_and_withholds_attestation(tmp_path: Path) -> None:
    # Given
    connection = FakeSipConnection([_connected(), _authenticated(), _subscribed(), b'[{"T":"q","S":"AAPL"}]'])
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    trades = AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3")
    epoch = ""

    # When / Then
    with (
        pytest.raises(AlpacaSipTradeHistoryError),
        open_alpaca_sip_trade_stream(
            _credentials(),
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
            AlpacaSipTradeStreamStores(controls, trades),
            connector=_connector(connection),
            _clock=iter(_times(6)).__next__,
        ) as stream,
    ):
        epoch = stream.connection_epoch
        _ = stream.receive_trade_frame(1.0)
    assert trades.frame_count() == 1
    assert controls.data_link_count(epoch) == 1
    assert controls.load_attestation(epoch) is None


@pytest.mark.parametrize(
    "url",
    (
        "ws://stream.data.alpaca.markets/v2/sip",
        "wss://stream.data.alpaca.markets/v2/iex",
        "wss://stream.data.alpaca.markets.evil.example/v2/sip",
        "wss://stream.data.alpaca.markets/v2/sip/extra",
    ),
)
def test_stream_endpoint_when_noncanonical_is_rejected(url: str) -> None:
    # Given / When / Then
    with pytest.raises(AlpacaSipTradeStreamEndpointError):
        require_alpaca_sip_trade_stream_url(url)


def test_stream_when_final_url_changes_rejects_before_credentials_are_sent(tmp_path: Path) -> None:
    # Given
    connection = FakeSipConnection([], "wss://stream.data.alpaca.markets/v2/iex")

    # When / Then
    with (
        pytest.raises(AlpacaSipTradeStreamEndpointError),
        open_alpaca_sip_trade_stream(
            _credentials(),
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
            AlpacaSipTradeStreamStores(
                AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3"),
                AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
            ),
            connector=_connector(connection),
            _clock=iter(_times(2)).__next__,
        ),
    ):
        pass
    assert connection.sent == []


def test_stream_when_no_data_arrives_records_failure_without_attestation(tmp_path: Path) -> None:
    # Given
    connection = FakeSipConnection([_connected(), _authenticated(), _subscribed()])
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    epoch = ""

    # When / Then
    with (
        pytest.raises(AlpacaSipTradeStreamProtocolError),
        open_alpaca_sip_trade_stream(
            _credentials(),
            AlpacaSipTradeStreamConfig(_DATE, "AAPL"),
            AlpacaSipTradeStreamStores(
                controls,
                AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
            ),
            connector=_connector(connection),
            _clock=iter(_times(6)).__next__,
        ) as stream,
    ):
        epoch = stream.connection_epoch
    assert controls.load_attestation(epoch) is None
    assert controls.load_terminal_status(epoch) is AlpacaSipStreamTerminalStatus.FAILED


def _connector(connection: FakeSipConnection):
    @contextmanager
    def connector(_: str) -> Iterator[FakeSipConnection]:
        yield connection

    return connector


def _credentials() -> AlpacaCredentials:
    return AlpacaCredentials("fixture-key", "fixture-secret")


def _times(count: int) -> tuple[dt.datetime, ...]:
    return tuple(_NOW + dt.timedelta(milliseconds=index) for index in range(count))


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


def _trade_chain() -> bytes:
    return (
        b'[{"T":"t","S":"AAPL","i":101,"x":"V","p":211.25,"s":40,"c":["@"],'
        b'"t":"2026-07-17T14:29:59.123456Z","z":"C"},'
        b'{"T":"c","S":"AAPL","x":"V","oi":101,"op":211.25,"os":40,"oc":["@"],'
        b'"ci":102,"cp":211.2,"cs":35,"cc":["@"],"t":"2026-07-17T14:29:59.123456Z","z":"C"},'
        b'{"T":"x","S":"AAPL","i":102,"x":"V","p":211.2,"s":35,"a":"C",'
        b'"t":"2026-07-17T14:29:59.123456Z","z":"C"}]'
    )
