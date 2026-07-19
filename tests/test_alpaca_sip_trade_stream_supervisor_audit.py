from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from websockets.exceptions import InvalidHandshake

from trading_agent import alpaca_sip_trade_stream_supervisor_audit as audit_module
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_trade_store import AlpacaSipTradeHistoryStore
from trading_agent.alpaca_sip_trade_stream import (
    ALPACA_SIP_TRADE_STREAM_URL,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamStores,
    open_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore
from trading_agent.alpaca_sip_trade_stream_supervisor import (
    AlpacaSipReconnectPolicy,
    AlpacaSipTradeStreamSupervisorError,
)
from trading_agent.alpaca_sip_trade_stream_supervisor_audit_store import (
    AlpacaSipSupervisorAuditStore,
)

_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 14, 30, tzinfo=dt.UTC)
_CONFIG = AlpacaSipTradeStreamConfig(_DATE, "AAPL")


def test_audited_supervisor_when_shutdown_precedes_operation_records_terminal_chain(
    tmp_path: Path,
) -> None:
    # Given
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise AssertionError

    audit = AlpacaSipSupervisorAuditStore(tmp_path / "audit.sqlite3")

    # When
    result = audit_module.run_audited_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3"),
        AlpacaSipReconnectPolicy(3, 1.0),
        run_id="a" * 64,
        audit_store=audit,
        clock=iter((_NOW, _NOW + dt.timedelta(milliseconds=1))).__next__,
        sleeper=lambda _seconds: None,
        shutdown_requested=lambda: True,
    )
    events = audit.events("a" * 64)

    # Then
    assert result.status.value == "stopped"
    assert tuple(event.kind.value for event in events) == ("started", "stopped")
    assert tuple(event.sequence for event in events) == (1, 2)
    assert events[0].previous_event_id is None
    assert events[1].previous_event_id == events[0].event_id
    assert events[1].stop_reason is not None
    assert events[1].stop_reason.value == "graceful_shutdown"
    assert calls == 0


def test_audited_supervisor_when_handshake_recovers_records_retry_and_ready(tmp_path: Path) -> None:
    # Given
    controls = AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3")
    stores = AlpacaSipTradeStreamStores(
        controls,
        AlpacaSipTradeHistoryStore(tmp_path / "trades.sqlite3"),
    )
    audit = AlpacaSipSupervisorAuditStore(tmp_path / "audit.sqlite3")
    calls = 0
    sleeps: list[float] = []

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
    result = audit_module.run_audited_alpaca_sip_trade_stream_supervisor(
        operation,
        _CONFIG,
        controls,
        AlpacaSipReconnectPolicy(3, 1.0),
        run_id="b" * 64,
        audit_store=audit,
        clock=iter(_times(5)).__next__,
        sleeper=sleeps.append,
        shutdown_requested=lambda: False,
    )
    events = audit.events("b" * 64)

    # Then
    assert result.status.value == "ready"
    assert tuple(event.kind.value for event in events) == (
        "started",
        "connecting",
        "retry_scheduled",
        "connecting",
        "ready",
    )
    assert events[2].retry_delay_seconds == 1.0
    assert tuple(event.operation_count for event in events) == (0, 1, 1, 2, 2)
    assert sleeps == [1.0]


def test_audit_store_when_payload_is_tampered_fails_closed(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "audit.sqlite3"
    audit = AlpacaSipSupervisorAuditStore(path)
    _ = audit_module.run_audited_alpaca_sip_trade_stream_supervisor(
        lambda: (_ for _ in ()).throw(AssertionError),
        _CONFIG,
        AlpacaSipTradeStreamStore(tmp_path / "stream.sqlite3"),
        AlpacaSipReconnectPolicy(3, 1.0),
        run_id="c" * 64,
        audit_store=audit,
        clock=iter((_NOW, _NOW + dt.timedelta(milliseconds=1))).__next__,
        sleeper=lambda _seconds: None,
        shutdown_requested=lambda: True,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER supervisor_audit_events_no_update")
        connection.execute("UPDATE supervisor_audit_events SET payload=? WHERE sequence=1", (b"{}\n",))

    # When / Then
    with pytest.raises(AlpacaSipTradeStreamSupervisorError):
        _ = audit.events("c" * 64)


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
