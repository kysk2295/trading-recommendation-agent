from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import final

from websockets.exceptions import InvalidHandshake

from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipProviderStreamError,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamEndpointError,
    AlpacaSipTradeStreamProtocolError,
)
from trading_agent.alpaca_sip_trade_stream_sqlite import (
    AlpacaSipStreamWriter,
    require_alpaca_sip_stream_schema,
    require_private_alpaca_sip_stream_file,
)


class AlpacaSipConnectionAttemptStage(StrEnum):
    CONNECT = "connect"
    ENDPOINT = "endpoint"
    CONNECTED_CONTROL = "connected_control"
    AUTHENTICATION_CONTROL = "authentication_control"
    SUBSCRIPTION_CONTROL = "subscription_control"


class AlpacaSipConnectionFailureCode(StrEnum):
    TRANSPORT_FAILED = "transport_failed"
    HANDSHAKE_FAILED = "handshake_failed"
    ENDPOINT_REJECTED = "endpoint_rejected"
    AUTHENTICATION_FAILED = "authentication_failed"
    CONNECTION_LIMIT = "connection_limit"
    INSUFFICIENT_SUBSCRIPTION = "insufficient_subscription"
    PROVIDER_INTERNAL_ERROR = "provider_internal_error"
    PROVIDER_REJECTED = "provider_rejected"
    PROTOCOL_INVALID = "protocol_invalid"


@dataclass(frozen=True, slots=True)
class AlpacaSipFailedConnectionAttempt:
    connection_epoch: str
    config: AlpacaSipTradeStreamConfig
    failed_at: dt.datetime
    stage: AlpacaSipConnectionAttemptStage
    failure_code: AlpacaSipConnectionFailureCode

    def __post_init__(self) -> None:
        aware = self.failed_at.tzinfo is not None and self.failed_at.utcoffset() is not None
        if (
            len(self.connection_epoch) != 32
            or any(character not in "0123456789abcdef" for character in self.connection_epoch)
            or type(self.config) is not AlpacaSipTradeStreamConfig
            or not aware
            or type(self.stage) is not AlpacaSipConnectionAttemptStage
            or type(self.failure_code) is not AlpacaSipConnectionFailureCode
        ):
            raise AlpacaSipTradeStreamProtocolError


@final
class AlpacaSipConnectionAttemptStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(self, attempt: AlpacaSipFailedConnectionAttempt) -> None:
        try:
            content_hash = _content_hash(attempt)
            row = (
                attempt.connection_epoch,
                attempt.config.symbol,
                attempt.config.market_date.isoformat(),
                attempt.failed_at.astimezone(dt.UTC).isoformat(),
                attempt.stage.value,
                attempt.failure_code.value,
                content_hash,
            )
            with AlpacaSipStreamWriter(self.path) as connection:
                existing = connection.execute(
                    "SELECT connection_epoch,symbol,market_date,failed_at,stage,failure_code,"
                    "content_sha256 FROM connection_attempts WHERE connection_epoch=?",
                    (attempt.connection_epoch,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSipTradeStreamProtocolError
                    return
                _ = connection.execute("INSERT INTO connection_attempts VALUES (?,?,?,?,?,?,?)", row)
                connection.commit()
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeStreamProtocolError from None

    def load(self, config: AlpacaSipTradeStreamConfig) -> tuple[AlpacaSipFailedConnectionAttempt, ...]:
        try:
            if type(config) is not AlpacaSipTradeStreamConfig:
                raise AlpacaSipTradeStreamProtocolError
            require_private_alpaca_sip_stream_file(self.path)
            if not self.path.exists():
                return ()
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                require_alpaca_sip_stream_schema(connection)
                if connection.execute("PRAGMA user_version").fetchone() == (1,):
                    return ()
                rows = connection.execute(
                    "SELECT connection_epoch,symbol,market_date,failed_at,stage,failure_code,"
                    "content_sha256 FROM connection_attempts WHERE symbol=? AND market_date=? "
                    "ORDER BY failed_at,connection_epoch",
                    (config.symbol, config.market_date.isoformat()),
                ).fetchall()
                return tuple(_from_row(connection, row) for row in rows)
        except (AttributeError, OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipTradeStreamProtocolError from None


@final
class AlpacaSipConnectionAttemptTracker:
    __slots__ = ("_active", "_clock", "_config", "_epoch", "_stage", "_store")

    def __init__(
        self,
        path: Path,
        epoch: str,
        config: AlpacaSipTradeStreamConfig,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._store = AlpacaSipConnectionAttemptStore(path)
        self._epoch = epoch
        self._config = config
        self._clock = clock
        self._stage = AlpacaSipConnectionAttemptStage.CONNECT
        self._active = True

    def advance(self, stage: AlpacaSipConnectionAttemptStage) -> None:
        self._stage = stage

    def ready(self) -> None:
        self._active = False

    def fail(self, error: BaseException) -> None:
        if self._active:
            self._store.append(
                AlpacaSipFailedConnectionAttempt(
                    self._epoch,
                    self._config,
                    self._clock(),
                    self._stage,
                    _failure_code(error),
                )
            )


def _failure_code(error: BaseException) -> AlpacaSipConnectionFailureCode:
    if isinstance(error, InvalidHandshake):
        return AlpacaSipConnectionFailureCode.HANDSHAKE_FAILED
    if isinstance(error, AlpacaSipTradeStreamEndpointError):
        return AlpacaSipConnectionFailureCode.ENDPOINT_REJECTED
    if isinstance(error, AlpacaSipProviderStreamError):
        return {
            402: AlpacaSipConnectionFailureCode.AUTHENTICATION_FAILED,
            406: AlpacaSipConnectionFailureCode.CONNECTION_LIMIT,
            409: AlpacaSipConnectionFailureCode.INSUFFICIENT_SUBSCRIPTION,
            500: AlpacaSipConnectionFailureCode.PROVIDER_INTERNAL_ERROR,
        }.get(error.code, AlpacaSipConnectionFailureCode.PROVIDER_REJECTED)
    if isinstance(error, AlpacaSipTradeStreamProtocolError):
        return AlpacaSipConnectionFailureCode.PROTOCOL_INVALID
    return AlpacaSipConnectionFailureCode.TRANSPORT_FAILED


def _from_row(
    connection: sqlite3.Connection,
    row: tuple[str, str, str, str, str, str, str],
) -> AlpacaSipFailedConnectionAttempt:
    attempt = AlpacaSipFailedConnectionAttempt(
        row[0],
        AlpacaSipTradeStreamConfig(dt.date.fromisoformat(row[2]), row[1]),
        dt.datetime.fromisoformat(row[3]),
        AlpacaSipConnectionAttemptStage(row[4]),
        AlpacaSipConnectionFailureCode(row[5]),
    )
    controls = connection.execute(
        "SELECT count(*) FROM control_frames WHERE connection_epoch=?",
        (attempt.connection_epoch,),
    ).fetchone()[0]
    limits = {
        AlpacaSipConnectionAttemptStage.CONNECT: (0, 0),
        AlpacaSipConnectionAttemptStage.ENDPOINT: (0, 0),
        AlpacaSipConnectionAttemptStage.CONNECTED_CONTROL: (0, 1),
        AlpacaSipConnectionAttemptStage.AUTHENTICATION_CONTROL: (1, 2),
        AlpacaSipConnectionAttemptStage.SUBSCRIPTION_CONTROL: (2, 3),
    }
    terminal = connection.execute(
        "SELECT 1 FROM terminal_sessions WHERE connection_epoch=?",
        (attempt.connection_epoch,),
    ).fetchone()
    if not limits[attempt.stage][0] <= controls <= limits[attempt.stage][1] or terminal is not None:
        raise AlpacaSipTradeStreamProtocolError
    if row[6] != _content_hash(attempt):
        raise AlpacaSipTradeStreamProtocolError
    return attempt


def _content_hash(attempt: AlpacaSipFailedConnectionAttempt) -> str:
    content = {
        "connection_epoch": attempt.connection_epoch,
        "failed_at": attempt.failed_at.astimezone(dt.UTC).isoformat(),
        "failure_code": attempt.failure_code.value,
        "market_date": attempt.config.market_date.isoformat(),
        "stage": attempt.stage.value,
        "symbol": attempt.config.symbol,
    }
    return hashlib.sha256(json.dumps(content, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


__all__ = (
    "AlpacaSipConnectionAttemptStage",
    "AlpacaSipConnectionAttemptStore",
    "AlpacaSipConnectionAttemptTracker",
    "AlpacaSipConnectionFailureCode",
    "AlpacaSipFailedConnectionAttempt",
)
