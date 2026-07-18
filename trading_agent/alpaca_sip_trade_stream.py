from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol, final

from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.sync.client import connect
from websockets.sync.connection import Connection

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_models import (
    AlpacaSipReceivedTradeFrame,
    AlpacaSipTradeHistoryError,
    parse_alpaca_sip_trade_frame,
)
from trading_agent.alpaca_sip_trade_store import (
    AlpacaSipTradeHistoryStore,
    StoredAlpacaSipTradeFrame,
)
from trading_agent.alpaca_sip_trade_stream_attempts import (
    AlpacaSipConnectionAttemptStage,
    AlpacaSipConnectionAttemptTracker,
)
from trading_agent.alpaca_sip_trade_stream_endpoint import (
    ALPACA_SIP_TRADE_STREAM_URL,
    final_alpaca_sip_connection_url,
    require_alpaca_sip_trade_stream_url,
)
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipControlStage,
    AlpacaSipRawControlFrame,
    AlpacaSipStreamTerminalRecord,
    AlpacaSipStreamTerminalStatus,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamEndpointError,
    AlpacaSipTradeStreamError,
    AlpacaSipTradeStreamProtocolError,
    parse_alpaca_sip_control_frame,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_MAX_FRAME_BYTES = 1_048_576


class AlpacaSipTradeStreamConnection(Protocol):
    final_url: str

    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...


type AlpacaSipTradeStreamConnector = Callable[
    [str],
    AbstractContextManager[AlpacaSipTradeStreamConnection],
]


@dataclass(frozen=True, slots=True)
class AlpacaSipTradeStreamStores:
    controls: AlpacaSipTradeStreamStore
    trades: AlpacaSipTradeHistoryStore

    def __post_init__(self) -> None:
        if type(self.controls) is not AlpacaSipTradeStreamStore or type(self.trades) is not AlpacaSipTradeHistoryStore:
            raise AlpacaSipTradeStreamProtocolError


@dataclass(frozen=True, slots=True)
class _ControlContext:
    store: AlpacaSipTradeStreamStore
    connection_epoch: str
    clock: Callable[[], dt.datetime]
    symbol: str


@dataclass(frozen=True, slots=True)
class _ReadySession:
    config: AlpacaSipTradeStreamConfig
    stores: AlpacaSipTradeStreamStores
    connection_epoch: str
    clock: Callable[[], dt.datetime]
    authorized_at: dt.datetime
    subscribed_at: dt.datetime

    def terminal(
        self,
        status: AlpacaSipStreamTerminalStatus,
        terminal_at: dt.datetime,
    ) -> AlpacaSipStreamTerminalRecord:
        return AlpacaSipStreamTerminalRecord(
            self.connection_epoch,
            self.config,
            self.authorized_at,
            self.subscribed_at,
            terminal_at,
            status,
        )


@final
class _WebsocketSipTradeConnection:
    def __init__(self, connection: Connection, final_url: str) -> None:
        self._connection = connection
        self.final_url = final_url

    def send(self, message: str) -> None:
        self._connection.send(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        return self._connection.recv(timeout=timeout)


@contextmanager
def connect_alpaca_sip_trade_stream(url: str) -> Iterator[AlpacaSipTradeStreamConnection]:
    with connect(
        url,
        proxy=None,
        compression=None,
        open_timeout=5.0,
        ping_interval=20.0,
        ping_timeout=5.0,
        close_timeout=5.0,
        max_size=_MAX_FRAME_BYTES,
        max_queue=8,
        user_agent_header=None,
    ) as connection:
        yield _WebsocketSipTradeConnection(
            connection,
            final_alpaca_sip_connection_url(connection, connection.request),
        )


@final
class ReadyAlpacaSipTradeStream:
    __slots__ = (
        "_connection",
        "_data_sequence",
        "_last_received_at",
        "_session",
    )

    def __init__(
        self,
        connection: AlpacaSipTradeStreamConnection,
        session: _ReadySession,
    ) -> None:
        self._connection = connection
        self._session = session
        self._data_sequence = 0
        self._last_received_at: dt.datetime | None = None

    @property
    def connection_epoch(self) -> str:
        return self._session.connection_epoch

    @property
    def last_received_at(self) -> dt.datetime | None:
        return self._last_received_at

    def receive_trade_frame(self, timeout_seconds: float) -> StoredAlpacaSipTradeFrame:
        if timeout_seconds <= 0:
            raise AlpacaSipTradeStreamProtocolError
        try:
            raw = self._connection.recv(timeout_seconds)
        except (ConnectionClosed, OSError, TimeoutError):
            raise AlpacaSipTradeStreamError from None
        payload = raw.encode() if isinstance(raw, str) else raw
        received = AlpacaSipReceivedTradeFrame(
            self._session.config.market_date,
            self._session.clock(),
            payload,
        )
        stored = self._session.stores.trades.append_frame(received)
        self._data_sequence += 1
        self._session.stores.controls.append_data_link(
            self.connection_epoch,
            self._data_sequence,
            stored,
        )
        messages = parse_alpaca_sip_trade_frame(stored.payload)
        if any(message.symbol != self._session.config.symbol for message in messages):
            raise AlpacaSipTradeStreamProtocolError
        self._last_received_at = stored.received_at
        return stored


@contextmanager
def open_alpaca_sip_trade_stream(
    credentials: AlpacaCredentials,
    config: AlpacaSipTradeStreamConfig,
    stores: AlpacaSipTradeStreamStores,
    *,
    connector: AlpacaSipTradeStreamConnector = connect_alpaca_sip_trade_stream,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> Iterator[ReadyAlpacaSipTradeStream]:
    epoch = uuid.uuid4().hex
    url = require_alpaca_sip_trade_stream_url(ALPACA_SIP_TRADE_STREAM_URL)
    attempt = AlpacaSipConnectionAttemptTracker(stores.controls.path, epoch, config, _clock)
    try:
        with connector(url) as connection:
            attempt.advance(AlpacaSipConnectionAttemptStage.ENDPOINT)
            _ = require_alpaca_sip_trade_stream_url(connection.final_url)
            attempt.advance(AlpacaSipConnectionAttemptStage.CONNECTED_CONTROL)
            control_context = _ControlContext(stores.controls, epoch, _clock, config.symbol)
            _ = _receive_control(connection, control_context, 1, AlpacaSipControlStage.CONNECTED)
            attempt.advance(AlpacaSipConnectionAttemptStage.AUTHENTICATION_CONTROL)
            connection.send(
                json.dumps(
                    {"action": "auth", "key": credentials.key_id, "secret": credentials.secret_key},
                    separators=(",", ":"),
                )
            )
            authorized_at = _receive_control(
                connection,
                control_context,
                2,
                AlpacaSipControlStage.AUTHENTICATED,
            )
            attempt.advance(AlpacaSipConnectionAttemptStage.SUBSCRIPTION_CONTROL)
            connection.send(json.dumps({"action": "subscribe", "trades": [config.symbol]}, separators=(",", ":")))
            subscribed_at = _receive_control(
                connection,
                control_context,
                3,
                AlpacaSipControlStage.SUBSCRIBED,
            )
            attempt.ready()
            session = _ReadySession(config, stores, epoch, _clock, authorized_at, subscribed_at)
            stream = ReadyAlpacaSipTradeStream(connection, session)
            try:
                yield stream
            except (AlpacaSipTradeHistoryError, AlpacaSipTradeStreamError, OSError, TimeoutError):
                stores.controls.append_terminal(session.terminal(AlpacaSipStreamTerminalStatus.FAILED, _clock()))
                raise
            if stream.last_received_at is None or stores.controls.data_link_count(epoch) == 0:
                stores.controls.append_terminal(session.terminal(AlpacaSipStreamTerminalStatus.FAILED, _clock()))
                raise AlpacaSipTradeStreamProtocolError
            stores.controls.append_terminal(
                session.terminal(
                    AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE,
                    stream.last_received_at,
                )
            )
    except AlpacaSipTradeStreamError as error:
        attempt.fail(error)
        raise
    except (ConnectionClosed, InvalidHandshake, OSError, TimeoutError) as error:
        attempt.fail(error)
        raise AlpacaSipTradeStreamError from None


def _receive_control(
    connection: AlpacaSipTradeStreamConnection,
    context: _ControlContext,
    sequence: int,
    stage: AlpacaSipControlStage,
) -> dt.datetime:
    raw = connection.recv(5.0)
    payload = raw.encode() if isinstance(raw, str) else raw
    received_at = context.clock()
    _ = context.store.append_control(AlpacaSipRawControlFrame(context.connection_epoch, sequence, received_at, payload))
    parse_alpaca_sip_control_frame(payload, stage, context.symbol)
    return received_at


__all__ = (
    "ALPACA_SIP_TRADE_STREAM_URL",
    "AlpacaSipTradeStreamConfig",
    "AlpacaSipTradeStreamEndpointError",
    "AlpacaSipTradeStreamError",
    "AlpacaSipTradeStreamProtocolError",
    "AlpacaSipTradeStreamStores",
    "ReadyAlpacaSipTradeStream",
    "connect_alpaca_sip_trade_stream",
    "open_alpaca_sip_trade_stream",
    "require_alpaca_sip_trade_stream_url",
)
