from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from trading_agent.ls_nws import LsNwsWireKind
from trading_agent.ls_nws_stream import (
    LS_NWS_STREAM_URL,
    InvalidLsNwsStreamTimeoutError,
    LsNwsStreamUnavailableError,
    UnsafeLsNwsStreamEndpointError,
    open_ls_nws_stream,
    require_ls_nws_stream_url,
)
from trading_agent.ls_token import LsAccessToken

ACCESS_TOKEN = "t" * 64
PRIVATE_ERROR = "private-stream-error"
RECEIVED_AT = dt.datetime(2026, 7, 15, 9, 1, 1, tzinfo=dt.UTC)


class FakeLsNwsConnection:
    __slots__ = ("_responses", "final_url", "sent", "timeouts")

    def __init__(
        self,
        responses: list[str | bytes | BaseException],
        *,
        final_url: str = LS_NWS_STREAM_URL,
    ) -> None:
        self._responses = responses
        self.final_url = final_url
        self.sent: list[str] = []
        self.timeouts: list[float | None] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        self.timeouts.append(timeout)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_open_stream_sends_only_canonical_nws_subscription_and_preserves_frames() -> None:
    text_payload = '{"header":{"tr_cd":"NWS"}}'
    binary_payload = b'{"header":{"tr_cd":"NWS"}}'
    connection = FakeLsNwsConnection(
        [text_payload, binary_payload, TimeoutError(PRIVATE_ERROR)]
    )
    connected_urls: list[str] = []

    @contextmanager
    def connector(url: str) -> Iterator[FakeLsNwsConnection]:
        connected_urls.append(url)
        yield connection

    timestamps = iter(
        (RECEIVED_AT, RECEIVED_AT + dt.timedelta(milliseconds=100))
    )
    with open_ls_nws_stream(
        LsAccessToken(ACCESS_TOKEN),
        connector=connector,
        _clock=lambda: next(timestamps),
    ) as stream:
        first = stream.receive_frame(2.0)
        second = stream.receive_frame(1.5)
        completed = stream.receive_frame(1.0)

    assert connected_urls == [LS_NWS_STREAM_URL]
    assert len(connection.sent) == 1
    subscription = json.loads(connection.sent[0])
    assert subscription == {
        "header": {"token": ACCESS_TOKEN, "tr_type": "3"},
        "body": {"tr_cd": "NWS", "tr_key": "NWS001"},
    }
    serialized = connection.sent[0].lower()
    for forbidden in (
        '"tr_type":"1"',
        '"tr_type":"2"',
        "account",
        "accno",
        "order",
        "/stock/",
    ):
        assert forbidden not in serialized
    assert first is not None
    assert first.sequence == 1
    assert first.received_at == RECEIVED_AT
    assert first.wire_kind is LsNwsWireKind.TEXT
    assert first.raw_payload == text_payload.encode()
    assert second is not None
    assert second.sequence == 2
    assert second.received_at == RECEIVED_AT + dt.timedelta(milliseconds=100)
    assert second.wire_kind is LsNwsWireKind.BINARY
    assert second.raw_payload == binary_payload
    assert completed is None
    assert connection.timeouts == [2.0, 1.5, 1.0]


@pytest.mark.parametrize(
    "url",
    (
        "ws://openapi.ls-sec.co.kr:9443/websocket",
        "wss://openapi.ls-sec.co.kr:29443/websocket",
        "wss://openapi.ls-sec.co.kr/websocket",
        "wss://openapi.ls-sec.co.kr:9443/websocket/extra",
        "wss://openapi.ls-sec.co.kr.evil.example:9443/websocket",
    ),
)
def test_stream_endpoint_guard_rejects_every_noncanonical_url(url: str) -> None:
    with pytest.raises(UnsafeLsNwsStreamEndpointError):
        _ = require_ls_nws_stream_url(url)


def test_stream_endpoint_guard_accepts_only_official_url() -> None:
    actual = require_ls_nws_stream_url(LS_NWS_STREAM_URL)

    assert actual == LS_NWS_STREAM_URL


def test_open_stream_rejects_changed_final_url_before_sending_token() -> None:
    connection = FakeLsNwsConnection(
        [],
        final_url="wss://openapi.ls-sec.co.kr:9443/redirected",
    )

    @contextmanager
    def connector(_: str) -> Iterator[FakeLsNwsConnection]:
        yield connection

    with (
        pytest.raises(UnsafeLsNwsStreamEndpointError),
        open_ls_nws_stream(
            LsAccessToken(ACCESS_TOKEN),
            connector=connector,
            _clock=lambda: RECEIVED_AT,
        ),
    ):
        pass

    assert connection.sent == []


@pytest.mark.parametrize("timeout", (0.0, -1.0))
def test_receive_frame_rejects_nonpositive_timeout(timeout: float) -> None:
    connection = FakeLsNwsConnection([])

    @contextmanager
    def connector(_: str) -> Iterator[FakeLsNwsConnection]:
        yield connection

    with (
        open_ls_nws_stream(
            LsAccessToken(ACCESS_TOKEN),
            connector=connector,
            _clock=lambda: RECEIVED_AT,
        ) as stream,
        pytest.raises(InvalidLsNwsStreamTimeoutError),
    ):
        _ = stream.receive_frame(timeout)

    assert connection.timeouts == []


def test_open_stream_converts_connection_error_without_rendering_token() -> None:
    @contextmanager
    def connector(_: str) -> Iterator[FakeLsNwsConnection]:
        raise OSError(f"{PRIVATE_ERROR}:{ACCESS_TOKEN}")
        yield FakeLsNwsConnection([])

    with (
        pytest.raises(LsNwsStreamUnavailableError) as captured,
        open_ls_nws_stream(
            LsAccessToken(ACCESS_TOKEN),
            connector=connector,
            _clock=lambda: RECEIVED_AT,
        ),
    ):
        pass

    _assert_private_absent(str(captured.value))


def test_receive_frame_converts_connection_error_without_rendering_payload() -> None:
    connection = FakeLsNwsConnection(
        [OSError(f"{PRIVATE_ERROR}:{ACCESS_TOKEN}")]
    )

    @contextmanager
    def connector(_: str) -> Iterator[FakeLsNwsConnection]:
        yield connection

    with (
        open_ls_nws_stream(
            LsAccessToken(ACCESS_TOKEN),
            connector=connector,
            _clock=lambda: RECEIVED_AT,
        ) as stream,
        pytest.raises(LsNwsStreamUnavailableError) as captured,
    ):
        _ = stream.receive_frame(1.0)

    _assert_private_absent(str(captured.value))


def _assert_private_absent(rendered: str) -> None:
    assert ACCESS_TOKEN not in rendered
    assert PRIVATE_ERROR not in rendered
