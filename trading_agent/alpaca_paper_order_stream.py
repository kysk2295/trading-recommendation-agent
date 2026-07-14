from __future__ import annotations

import datetime as dt
import json
import ssl
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Final, Literal, NewType, Protocol, assert_never, final, override

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.http11 import Request
from websockets.sync.client import connect
from websockets.sync.connection import Connection

from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_trade_updates import (
    AlpacaTradeUpdate,
    parse_alpaca_trade_update,
)

ALPACA_PAPER_ORDER_STREAM_URL: Final = "wss://paper-api.alpaca.markets/stream"
CONTROL_TIMEOUT_SECONDS: Final = 5.0
TRADE_UPDATES_STREAM: Final = "trade_updates"

PaperStreamEpoch = NewType("PaperStreamEpoch", str)


class PaperOrderStreamError(RuntimeError):
    pass


class NonPaperOrderStreamEndpointError(PaperOrderStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca 주문 스트림 주소는 paper 전용 고정값이어야 합니다"


class PaperOrderStreamAuthorizationError(PaperOrderStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 주문 스트림 인증에 실패했습니다"


class PaperOrderStreamSubscriptionError(PaperOrderStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 주문 스트림이 trade_updates를 승인하지 않았습니다"


class PaperOrderStreamHeartbeatTimeoutError(PaperOrderStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 주문 스트림 Pong이 제한시간 안에 도착하지 않았습니다"


class PaperOrderStreamProtocolError(PaperOrderStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 주문 스트림 제어 응답 형식이 올바르지 않습니다"


class InvalidPaperOrderStreamTimeoutError(PaperOrderStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 주문 스트림 timeout은 0보다 커야 합니다"


class PaperOrderStreamUnavailableError(PaperOrderStreamError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 주문 스트림 연결을 확인할 수 없습니다"


class PongWaiter(Protocol):
    def wait(self, timeout: float | None = None) -> bool: ...


class PaperStreamConnection(Protocol):
    final_url: str

    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def ping(self) -> PongWaiter: ...


type PaperStreamConnector = Callable[
    [str],
    AbstractContextManager[PaperStreamConnection],
]


@final
class _WebsocketPaperStreamConnection:
    def __init__(self, connection: Connection, final_url: str) -> None:
        self._connection = connection
        self.final_url = final_url

    def send(self, message: str) -> None:
        self._connection.send(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        return self._connection.recv(timeout=timeout)

    def ping(self) -> PongWaiter:
        return self._connection.ping()


@contextmanager
def _connect_paper_order_stream(url: str) -> Iterator[PaperStreamConnection]:
    with connect(
        url,
        proxy=None,
        compression=None,
        open_timeout=5.0,
        ping_interval=20.0,
        ping_timeout=5.0,
        close_timeout=5.0,
        max_size=262_144,
        max_queue=8,
        user_agent_header=None,
    ) as connection:
        request = connection.request
        final_url = _final_connection_url(connection, request)
        yield _WebsocketPaperStreamConnection(connection, final_url)


class AuthorizationData(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["authorized", "unauthorized"]
    action: Literal["authenticate"]


class AuthorizationMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream: Literal["authorization"]
    data: AuthorizationData


class ListeningData(BaseModel):
    model_config = ConfigDict(frozen=True)

    streams: tuple[str, ...]


class ListeningMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    stream: Literal["listening"]
    data: ListeningData


type PaperStreamControlMessage = AuthorizationMessage | ListeningMessage
CONTROL_MESSAGE_ADAPTER: Final = TypeAdapter(PaperStreamControlMessage)


@dataclass(frozen=True, slots=True)
class PaperOrderStreamHeartbeat:
    connection_epoch: PaperStreamEpoch
    authorized_at: dt.datetime
    subscribed_at: dt.datetime
    pong_at: dt.datetime


@final
class ReadyPaperOrderStream:
    def __init__(
        self,
        connection: PaperStreamConnection,
        authorized_at: dt.datetime,
        subscribed_at: dt.datetime,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._connection = connection
        self._connection_epoch = PaperStreamEpoch(uuid.uuid4().hex)
        self._authorized_at = authorized_at
        self._subscribed_at = subscribed_at
        self._clock = clock

    @property
    def connection_epoch(self) -> PaperStreamEpoch:
        return self._connection_epoch

    def receive_trade_update(self, timeout_seconds: float) -> AlpacaTradeUpdate:
        if timeout_seconds <= 0:
            raise InvalidPaperOrderStreamTimeoutError
        return parse_alpaca_trade_update(
            self._connection.recv(timeout_seconds)
        )

    def heartbeat(
        self,
        timeout_seconds: float,
    ) -> PaperOrderStreamHeartbeat:
        if timeout_seconds <= 0 or not self._connection.ping().wait(timeout_seconds):
            raise PaperOrderStreamHeartbeatTimeoutError
        return PaperOrderStreamHeartbeat(
            connection_epoch=self._connection_epoch,
            authorized_at=self._authorized_at,
            subscribed_at=self._subscribed_at,
            pong_at=self._clock(),
        )


def require_paper_order_stream_url(url: str) -> str:
    if url != ALPACA_PAPER_ORDER_STREAM_URL:
        raise NonPaperOrderStreamEndpointError
    return url


def authenticate_paper_order_stream(
    connection: PaperStreamConnection,
    credentials: AlpacaPaperCredentials,
    *,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> ReadyPaperOrderStream:
    connection.send(
        json.dumps(
            {
                "action": "auth",
                "key": credentials.key_id,
                "secret": credentials.secret_key,
            },
            separators=(",", ":"),
        )
    )
    authorization = _control_message(connection.recv(CONTROL_TIMEOUT_SECONDS))
    match authorization:
        case AuthorizationMessage(data=AuthorizationData(status="authorized")):
            pass
        case AuthorizationMessage():
            raise PaperOrderStreamAuthorizationError
        case ListeningMessage():
            raise PaperOrderStreamProtocolError
        case unreachable:
            assert_never(unreachable)
    authorized_at = clock()

    connection.send(
        json.dumps(
            {
                "action": "listen",
                "data": {"streams": [TRADE_UPDATES_STREAM]},
            },
            separators=(",", ":"),
        )
    )
    listening = _control_message(connection.recv(CONTROL_TIMEOUT_SECONDS))
    match listening:
        case ListeningMessage(data=ListeningData(streams=streams)):
            if streams != (TRADE_UPDATES_STREAM,):
                raise PaperOrderStreamSubscriptionError
        case AuthorizationMessage():
            raise PaperOrderStreamAuthorizationError
        case unreachable:
            assert_never(unreachable)
    return ReadyPaperOrderStream(connection, authorized_at, clock(), clock)


@contextmanager
def open_alpaca_paper_order_stream(
    credentials: AlpacaPaperCredentials,
    connector: PaperStreamConnector = _connect_paper_order_stream,
    *,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Iterator[ReadyPaperOrderStream]:
    url = require_paper_order_stream_url(ALPACA_PAPER_ORDER_STREAM_URL)
    try:
        with connector(url) as connection:
            _ = require_paper_order_stream_url(connection.final_url)
            yield authenticate_paper_order_stream(
                connection,
                credentials,
                clock=_clock,
            )
    except (ConnectionClosed, InvalidHandshake, OSError, TimeoutError) as error:
        raise PaperOrderStreamUnavailableError from error


def _control_message(raw: str | bytes) -> PaperStreamControlMessage:
    try:
        return CONTROL_MESSAGE_ADAPTER.validate_json(raw)
    except ValidationError as error:
        raise PaperOrderStreamProtocolError from error


def _final_connection_url(
    connection: Connection,
    request: Request | None,
) -> str:
    if request is None:
        return ""
    scheme = "wss" if isinstance(connection.socket, ssl.SSLSocket) else "ws"
    host = request.headers.get("Host", "")
    return f"{scheme}://{host}{request.path}"
