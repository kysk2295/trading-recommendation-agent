from __future__ import annotations

import datetime as dt
import json
import ssl
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Final, Protocol, final, override

from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.http11 import Request
from websockets.sync.client import connect
from websockets.sync.connection import Connection

from trading_agent.ls_nws import (
    MAX_LS_NWS_FRAME_BYTES,
    LsNwsRawFrame,
    LsNwsWireKind,
)
from trading_agent.ls_token import LsAccessToken

LS_NWS_STREAM_URL: Final = "wss://openapi.ls-sec.co.kr:9443/websocket"


class UnsafeLsNwsStreamEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "LS NWS WebSocket endpoint는 공식 고정값이어야 합니다"


class InvalidLsNwsStreamTimeoutError(ValueError):
    @override
    def __str__(self) -> str:
        return "LS NWS receive timeout은 0보다 커야 합니다"


class LsNwsStreamUnavailableError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "LS NWS WebSocket 연결을 확인할 수 없습니다"


class LsNwsStreamConnection(Protocol):
    final_url: str

    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...


type LsNwsStreamConnector = Callable[
    [str],
    AbstractContextManager[LsNwsStreamConnection],
]


@final
class _WebsocketLsNwsConnection:
    def __init__(self, connection: Connection, final_url: str) -> None:
        self._connection = connection
        self.final_url = final_url

    def send(self, message: str) -> None:
        self._connection.send(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        return self._connection.recv(timeout=timeout)


@contextmanager
def _connect_ls_nws_stream(url: str) -> Iterator[LsNwsStreamConnection]:
    with connect(
        url,
        proxy=None,
        compression=None,
        open_timeout=5.0,
        ping_interval=20.0,
        ping_timeout=5.0,
        close_timeout=5.0,
        max_size=MAX_LS_NWS_FRAME_BYTES,
        max_queue=8,
        user_agent_header=None,
    ) as connection:
        yield _WebsocketLsNwsConnection(
            connection,
            _final_connection_url(connection, connection.request),
        )


@final
class ReadyLsNwsStream:
    def __init__(
        self,
        connection: LsNwsStreamConnection,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._sequence = 0

    def receive_frame(self, timeout_seconds: float) -> LsNwsRawFrame | None:
        if timeout_seconds <= 0:
            raise InvalidLsNwsStreamTimeoutError
        try:
            raw = self._connection.recv(timeout_seconds)
        except TimeoutError:
            return None
        except (ConnectionClosed, OSError):
            raise LsNwsStreamUnavailableError from None
        self._sequence += 1
        if isinstance(raw, str):
            payload = raw.encode("utf-8")
            wire_kind = LsNwsWireKind.TEXT
        else:
            payload = raw
            wire_kind = LsNwsWireKind.BINARY
        return LsNwsRawFrame(
            sequence=self._sequence,
            received_at=self._clock(),
            wire_kind=wire_kind,
            raw_payload=payload,
        )


def require_ls_nws_stream_url(url: str) -> str:
    if url != LS_NWS_STREAM_URL:
        raise UnsafeLsNwsStreamEndpointError
    return url


@contextmanager
def open_ls_nws_stream(
    access_token: LsAccessToken,
    connector: LsNwsStreamConnector = _connect_ls_nws_stream,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Iterator[ReadyLsNwsStream]:
    url = require_ls_nws_stream_url(LS_NWS_STREAM_URL)
    try:
        with connector(url) as connection:
            _ = require_ls_nws_stream_url(connection.final_url)
            connection.send(
                json.dumps(
                    {
                        "header": {
                            "token": access_token.value,
                            "tr_type": "3",
                        },
                        "body": {
                            "tr_cd": "NWS",
                            "tr_key": "NWS001",
                        },
                    },
                    separators=(",", ":"),
                )
            )
            yield ReadyLsNwsStream(connection, _clock)
    except (ConnectionClosed, InvalidHandshake, OSError, TimeoutError):
        raise LsNwsStreamUnavailableError from None


def _final_connection_url(
    connection: Connection,
    request: Request | None,
) -> str:
    if request is None:
        return ""
    scheme = "wss" if isinstance(connection.socket, ssl.SSLSocket) else "ws"
    host = request.headers.get("Host", "")
    return f"{scheme}://{host}{request.path}"
