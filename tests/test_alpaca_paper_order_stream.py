from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    ALPACA_PAPER_ORDER_STREAM_URL,
    NonPaperOrderStreamEndpointError,
    PaperOrderStreamAuthorizationError,
    PaperOrderStreamHeartbeatTimeoutError,
    PaperOrderStreamSubscriptionError,
    PaperOrderStreamUnavailableError,
    authenticate_paper_order_stream,
    open_alpaca_paper_order_stream,
    require_paper_order_stream_url,
)


class FakePongWaiter:
    __slots__ = ("_received",)

    def __init__(self, received: bool) -> None:
        self._received = received

    def wait(self, timeout: float | None = None) -> bool:
        _ = timeout
        return self._received


class FakePaperStreamConnection:
    __slots__ = (
        "_pong_received",
        "_responses",
        "final_url",
        "ping_count",
        "sent",
    )

    def __init__(
        self,
        responses: list[str | bytes],
        *,
        final_url: str = ALPACA_PAPER_ORDER_STREAM_URL,
        pong_received: bool = True,
    ) -> None:
        self._responses = responses
        self._pong_received = pong_received
        self.final_url = final_url
        self.sent: list[str] = []
        self.ping_count = 0

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self._responses.pop(0)

    def ping(self) -> FakePongWaiter:
        self.ping_count += 1
        return FakePongWaiter(self._pong_received)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _authorized() -> bytes:
    return b'{"stream":"authorization","data":{"status":"authorized","action":"authenticate"}}'


def _listening(*streams: str) -> bytes:
    joined = ",".join(f'"{stream}"' for stream in streams)
    return f'{{"stream":"listening","data":{{"streams":[{joined}]}}}}'.encode()


def test_stream_authenticates_subscribes_and_confirms_ping_pong() -> None:
    # Given
    connection = FakePaperStreamConnection(
        [_authorized(), _listening("trade_updates")]
    )
    connected_at = dt.datetime(2026, 7, 14, 13, 36, tzinfo=dt.UTC)

    # When
    timestamps = iter(
        (
            connected_at,
            connected_at + dt.timedelta(milliseconds=250),
            connected_at + dt.timedelta(seconds=1),
        )
    )
    stream = authenticate_paper_order_stream(
        connection,
        _credentials(),
        clock=lambda: next(timestamps),
    )
    heartbeat = stream.heartbeat(2.0)

    # Then
    assert '"action":"auth"' in connection.sent[0]
    assert '"action":"listen"' in connection.sent[1]
    assert connection.ping_count == 1
    assert heartbeat.authorized_at == connected_at
    assert heartbeat.subscribed_at == connected_at + dt.timedelta(milliseconds=250)
    assert heartbeat.pong_at == connected_at + dt.timedelta(seconds=1)
    assert heartbeat.connection_epoch != ""


def test_stream_rejects_unauthorized_response_without_rendering_credentials() -> None:
    # Given
    connection = FakePaperStreamConnection(
        [b'{"stream":"authorization","data":{"status":"unauthorized","action":"authenticate"}}']
    )

    # When / Then
    with pytest.raises(PaperOrderStreamAuthorizationError) as captured:
        _ = authenticate_paper_order_stream(
            connection,
            _credentials(),
            clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
        )
    rendered = str(captured.value)
    assert "test-key" not in rendered
    assert "test-secret" not in rendered


def test_stream_requires_trade_updates_in_listening_acknowledgement() -> None:
    # Given
    connection = FakePaperStreamConnection([_authorized(), _listening()])

    # When / Then
    with pytest.raises(PaperOrderStreamSubscriptionError, match="trade_updates"):
        _ = authenticate_paper_order_stream(
            connection,
            _credentials(),
            clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
        )


def test_stream_rejects_extra_listening_streams() -> None:
    connection = FakePaperStreamConnection(
        [_authorized(), _listening("trade_updates", "account_updates")]
    )

    with pytest.raises(PaperOrderStreamSubscriptionError):
        _ = authenticate_paper_order_stream(
            connection,
            _credentials(),
            clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
        )


def test_stream_rejects_malformed_authorization_action() -> None:
    malformed = json.dumps(
        {
            "stream": "authorization",
            "data": {"status": "authorized", "action": "auth"},
        }
    )
    connection = FakePaperStreamConnection([malformed])

    with pytest.raises(Exception, match="제어 응답 형식"):
        _ = authenticate_paper_order_stream(
            connection,
            _credentials(),
            clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
        )


def test_stream_heartbeat_fails_when_pong_does_not_arrive() -> None:
    # Given
    connection = FakePaperStreamConnection(
        [_authorized(), _listening("trade_updates")],
        pong_received=False,
    )
    connected_at = dt.datetime(2026, 7, 14, tzinfo=dt.UTC)
    stream = authenticate_paper_order_stream(
        connection,
        _credentials(),
        clock=lambda: connected_at,
    )

    # When / Then
    with pytest.raises(PaperOrderStreamHeartbeatTimeoutError):
        _ = stream.heartbeat(0.1)


@pytest.mark.parametrize(
    "url",
    (
        "wss://api.alpaca.markets/stream",
        "ws://paper-api.alpaca.markets/stream",
        "wss://paper-api.alpaca.markets.evil.example/stream",
        "wss://paper-api.alpaca.markets/stream/extra",
    ),
)
def test_stream_endpoint_guard_rejects_every_nonpaper_url(url: str) -> None:
    # Given / When / Then
    with pytest.raises(NonPaperOrderStreamEndpointError, match="paper 전용"):
        _ = require_paper_order_stream_url(url)


def test_stream_endpoint_guard_accepts_only_canonical_paper_url() -> None:
    # Given / When
    actual = require_paper_order_stream_url(ALPACA_PAPER_ORDER_STREAM_URL)
    final_url = require_paper_order_stream_url(ALPACA_PAPER_ORDER_STREAM_URL)

    # Then
    assert actual == ALPACA_PAPER_ORDER_STREAM_URL
    assert final_url == ALPACA_PAPER_ORDER_STREAM_URL


@pytest.mark.parametrize(
    "final_url",
    (
        "wss://api.alpaca.markets/stream",
        "wss://paper-api.alpaca.markets/other",
        "ws://paper-api.alpaca.markets/stream",
    ),
)
def test_stream_redirect_guard_rejects_every_nonpaper_final_url(
    final_url: str,
) -> None:
    # Given / When / Then
    with pytest.raises(NonPaperOrderStreamEndpointError):
        _ = require_paper_order_stream_url(final_url)


def test_open_stream_uses_fixed_url_and_checks_final_host_before_auth() -> None:
    # Given
    connection = FakePaperStreamConnection(
        [_authorized(), _listening("trade_updates")]
    )
    connected_urls: list[str] = []

    @contextmanager
    def connector(url: str) -> Iterator[FakePaperStreamConnection]:
        connected_urls.append(url)
        yield connection

    # When
    with open_alpaca_paper_order_stream(
        _credentials(),
        connector=connector,
        _clock=iter(
            (
                dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
                dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
                dt.datetime(2026, 7, 14, 0, 0, 1, tzinfo=dt.UTC),
            )
        ).__next__,
    ) as stream:
        heartbeat = stream.heartbeat(1.0)

    # Then
    assert connected_urls == [ALPACA_PAPER_ORDER_STREAM_URL]
    assert heartbeat.pong_at.second == 1


def test_open_stream_rejects_redirected_host_before_sending_credentials() -> None:
    # Given
    connection = FakePaperStreamConnection(
        [],
        final_url="wss://paper-api.alpaca.markets/redirected",
    )

    @contextmanager
    def connector(_: str) -> Iterator[FakePaperStreamConnection]:
        yield connection

    # When / Then
    with pytest.raises(NonPaperOrderStreamEndpointError), open_alpaca_paper_order_stream(
        _credentials(),
        connector=connector,
        _clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
    ):
        pass
    assert connection.sent == []


def test_open_stream_converts_network_timeout_to_sanitized_error() -> None:
    # Given
    connection = FakePaperStreamConnection([])

    @contextmanager
    def connector(_: str) -> Iterator[FakePaperStreamConnection]:
        if connection.sent == []:
            raise TimeoutError
        yield connection

    # When / Then
    with pytest.raises(PaperOrderStreamUnavailableError) as captured, open_alpaca_paper_order_stream(
        _credentials(),
        connector=connector,
        _clock=lambda: dt.datetime(2026, 7, 14, tzinfo=dt.UTC),
    ):
        pass
    assert "test-key" not in str(captured.value)
    assert "test-secret" not in str(captured.value)
