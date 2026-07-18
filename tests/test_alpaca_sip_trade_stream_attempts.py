from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from websockets.exceptions import InvalidHandshake

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamError,
    AlpacaSipTradeStreamStores,
    open_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)
_CONFIG = AlpacaSipTradeStreamConfig(_DATE, "AAPL")


def test_stream_when_provider_reports_connection_limit_preserves_sanitized_attempt(
    tmp_path: Path,
) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    connection = FakeAttemptConnection([b'[{"T":"error","code":406,"msg":"connection limit exceeded"}]'])

    # When
    with (
        pytest.raises(AlpacaSipTradeStreamError),
        open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            _CONFIG,
            AlpacaSipTradeStreamStores(
                controls,
                AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
            ),
            connector=_connector(connection),
            _clock=iter(_times(3)).__next__,
        ),
    ):
        pass
    attempts = controls.load_connection_attempts(_CONFIG)

    # Then
    assert len(attempts) == 1
    assert attempts[0].stage.value == "connected_control"
    assert attempts[0].failure_code.value == "connection_limit"
    assert controls.control_count() == 1
    assert controls.load_terminal_status(attempts[0].connection_epoch) is None


def test_stream_when_opening_handshake_fails_preserves_attempt_without_control(
    tmp_path: Path,
) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")

    # When
    with (
        pytest.raises(AlpacaSipTradeStreamError),
        open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            _CONFIG,
            AlpacaSipTradeStreamStores(
                controls,
                AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
            ),
            connector=_handshake_failure_connector,
            _clock=iter(_times(2)).__next__,
        ),
    ):
        pass
    attempts = controls.load_connection_attempts(_CONFIG)

    # Then
    assert len(attempts) == 1
    assert attempts[0].stage.value == "connect"
    assert attempts[0].failure_code.value == "handshake_failed"
    assert controls.control_count() == 0


@pytest.mark.parametrize(
    ("code", "failure_code"),
    ((402, "authentication_failed"), (409, "insufficient_subscription")),
)
def test_stream_when_authentication_is_rejected_classifies_official_provider_code(
    tmp_path: Path,
    code: int,
    failure_code: str,
) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    error = json.dumps(({"T": "error", "code": code, "msg": "provider detail"},)).encode()
    connection = FakeAttemptConnection([_connected(), error])

    # When
    with (
        pytest.raises(AlpacaSipTradeStreamError),
        open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            _CONFIG,
            AlpacaSipTradeStreamStores(
                controls,
                AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
            ),
            connector=_connector(connection),
            _clock=iter(_times(4)).__next__,
        ),
    ):
        pass
    attempts = controls.load_connection_attempts(_CONFIG)

    # Then
    assert len(attempts) == 1
    assert attempts[0].stage.value == "authentication_control"
    assert attempts[0].failure_code.value == failure_code
    assert controls.control_count() == 2


class FakeAttemptConnection:
    __slots__ = ("_responses", "final_url")

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = responses
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self._responses.pop(0)


def _connector(connection: FakeAttemptConnection):
    @contextmanager
    def connector(_: str) -> Iterator[FakeAttemptConnection]:
        yield connection

    return connector


@contextmanager
def _handshake_failure_connector(_: str) -> Iterator[FakeAttemptConnection]:
    raise InvalidHandshake
    yield FakeAttemptConnection([])


def _times(count: int) -> tuple[dt.datetime, ...]:
    return tuple(_NOW + dt.timedelta(milliseconds=index) for index in range(count))


def _connected() -> bytes:
    return b'[{"T":"success","msg":"connected"}]'
