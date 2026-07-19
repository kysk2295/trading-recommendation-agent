from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from websockets.exceptions import InvalidHandshake

from trading_agent import alpaca_sip_trade_stream_supervisor as supervisor_module
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamStores,
    open_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)
_CONFIG = AlpacaSipTradeStreamConfig(_DATE, "AAPL")


def test_supervisor_when_connection_limit_is_recorded_stops_without_retry(tmp_path: Path) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    stores = AlpacaSipTradeStreamStores(
        controls,
        AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
    )
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        with open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            _CONFIG,
            stores,
            connector=_connection_limit_connector,
            _clock=iter(_times(4)).__next__,
        ):
            raise AssertionError

    # When
    result = supervisor_module.run_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        controls,
        supervisor_module.AlpacaSipReconnectPolicy(3, 1.0),
        sleeper=sleeps.append,
    )

    # Then
    assert result.status.value == "blocked"
    assert result.stop_reason is not None
    assert result.stop_reason.value == "non_retryable_failure"
    assert result.operation_count == 1
    assert result.total_connection_count == 1
    assert calls == 1
    assert sleeps == []


def test_supervisor_when_handshake_recovers_uses_new_epoch_without_continuity_claim(
    tmp_path: Path,
) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    stores = AlpacaSipTradeStreamStores(
        controls,
        AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
    )
    sleeps: list[float] = []
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        connector = _handshake_failure_connector if calls == 1 else _ready_connector
        with open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            _CONFIG,
            stores,
            connector=connector,
            _clock=iter(_times(8, offset=dt.timedelta(seconds=calls - 1))).__next__,
        ) as stream:
            _ = stream.receive_trade_frame(1.0)
            return stream.connection_epoch

    # When
    result = supervisor_module.run_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        controls,
        supervisor_module.AlpacaSipReconnectPolicy(3, 1.0),
        sleeper=sleeps.append,
    )

    # Then
    assert result.status.value == "ready"
    assert result.operation_count == 2
    assert result.total_connection_count == 2
    assert result.continuity_attested is False
    assert result.final_connection_epoch is not None
    assert sleeps == [1.0]
    assert len(controls.load_connection_attempts(_CONFIG)) == 1
    assert len(controls.load_session_history(_CONFIG)) == 1


def test_supervisor_when_restart_reads_exhausted_budget_does_not_open_connection(
    tmp_path: Path,
) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    stores = AlpacaSipTradeStreamStores(
        controls,
        AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
    )
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        with open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            _CONFIG,
            stores,
            connector=_handshake_failure_connector,
            _clock=iter(_times(3, offset=dt.timedelta(seconds=calls))).__next__,
        ):
            raise AssertionError

    _ = supervisor_module.run_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        controls,
        supervisor_module.AlpacaSipReconnectPolicy(3, 1.0),
        sleeper=sleeps.append,
    )
    first_calls = calls

    # When
    result = supervisor_module.run_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        controls,
        supervisor_module.AlpacaSipReconnectPolicy(3, 1.0),
        sleeper=sleeps.append,
    )

    # Then
    assert result.status.value == "blocked"
    assert result.stop_reason is not None
    assert result.stop_reason.value == "retry_budget_exhausted"
    assert result.operation_count == 0
    assert result.total_connection_count == 3
    assert calls == first_calls == 3
    assert sleeps == [1.0, 2.0]


def test_supervisor_when_provider_internal_error_recovers_retries_once(tmp_path: Path) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    stores = AlpacaSipTradeStreamStores(
        controls,
        AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
    )
    sleeps: list[float] = []
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        connector = _provider_internal_connector if calls == 1 else _ready_connector
        with open_alpaca_sip_trade_stream(
            AlpacaCredentials("fixture-key", "fixture-secret"),
            _CONFIG,
            stores,
            connector=connector,
            _clock=iter(_times(8, offset=dt.timedelta(seconds=calls - 1))).__next__,
        ) as stream:
            _ = stream.receive_trade_frame(1.0)
            return stream.connection_epoch

    # When
    result = supervisor_module.run_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        controls,
        supervisor_module.AlpacaSipReconnectPolicy(3, 1.0),
        sleeper=sleeps.append,
    )

    # Then
    assert result.status.value == "ready"
    assert result.operation_count == 2
    assert sleeps == [1.0]
    assert controls.load_connection_attempts(_CONFIG)[0].failure_code.value == "provider_internal_error"


def test_supervisor_when_shutdown_is_requested_stops_before_operation(tmp_path: Path) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    calls = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise AssertionError

    # When
    result = supervisor_module.run_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        controls,
        supervisor_module.AlpacaSipReconnectPolicy(3, 1.0),
        sleeper=sleeps.append,
        shutdown_requested=lambda: True,
    )

    # Then
    assert result.status.value == "stopped"
    assert result.stop_reason is not None
    assert result.stop_reason.value == "graceful_shutdown"
    assert result.operation_count == 0
    assert result.total_connection_count == 0
    assert calls == 0
    assert sleeps == []


class _ConnectionLimitConnection:
    __slots__ = ("final_url",)

    def __init__(self) -> None:
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return b'[{"T":"error","code":406,"msg":"connection limit exceeded"}]'


@contextmanager
def _connection_limit_connector(_: str) -> Iterator[_ConnectionLimitConnection]:
    yield _ConnectionLimitConnection()


class _ProviderInternalConnection(_ConnectionLimitConnection):
    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return b'[{"T":"error","code":500,"msg":"internal error"}]'


@contextmanager
def _provider_internal_connector(_: str) -> Iterator[_ProviderInternalConnection]:
    yield _ProviderInternalConnection()


class _ReadyConnection:
    __slots__ = ("_responses", "final_url")

    def __init__(self) -> None:
        self._responses = [_connected(), _authenticated(), _subscribed(), _trade()]
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL

    def send(self, message: str) -> None:
        _ = message

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        return self._responses.pop(0)


@contextmanager
def _ready_connector(_: str) -> Iterator[_ReadyConnection]:
    yield _ReadyConnection()


@contextmanager
def _handshake_failure_connector(_: str) -> Iterator[_ReadyConnection]:
    raise InvalidHandshake
    yield _ReadyConnection()


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
