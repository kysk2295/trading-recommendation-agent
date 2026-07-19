from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event

from trading_agent.alpaca_sip_trade_stream import ALPACA_SIP_TRADE_STREAM_URL


class FakeConnection:
    __slots__ = ("_responses", "final_url", "sent")

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = responses
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        if not self._responses:
            raise TimeoutError
        return self._responses.pop(0)


class ConnectorQueue:
    __slots__ = ("_connections", "calls")

    def __init__(self, connections: list[FakeConnection]) -> None:
        self._connections = connections
        self.calls = 0

    @contextmanager
    def connect(self, url: str) -> Iterator[FakeConnection]:
        assert url == ALPACA_SIP_TRADE_STREAM_URL
        self.calls += 1
        yield self._connections.pop(0)


class FixtureClock:
    __slots__ = ("_current",)

    def __init__(self, current: dt.datetime) -> None:
        self._current = current

    def __call__(self) -> dt.datetime:
        current = self._current
        self._current += dt.timedelta(milliseconds=1)
        return current

    def advance(self, seconds: float) -> None:
        self._current += dt.timedelta(seconds=seconds)


class WaitRecorder:
    __slots__ = ("_clock", "delays", "interrupted")

    def __init__(self, clock: FixtureClock, *, interrupted: bool = False) -> None:
        self._clock = clock
        self.delays: list[float] = []
        self.interrupted = interrupted

    def __call__(self, stop_event: Event, seconds: float) -> bool:
        _ = stop_event
        self.delays.append(seconds)
        if not self.interrupted:
            self._clock.advance(seconds)
        return self.interrupted


def timeout_connection() -> FakeConnection:
    return FakeConnection([_connected(), _authenticated(), _ack()])


def success_connection() -> FakeConnection:
    return FakeConnection([_connected(), _authenticated(), _ack(), b'[{"T":"q","S":"BBB"}]'])


def invalid_ack_connection() -> FakeConnection:
    return FakeConnection([_connected(), _authenticated(), b"[]"])


def _connected() -> bytes:
    return b'[{"T":"success","msg":"connected"}]'


def _authenticated() -> bytes:
    return b'[{"T":"success","msg":"authenticated"}]'


def _ack() -> bytes:
    return (
        b'[{"T":"subscription","trades":["BBB","AAA"],"quotes":["BBB","AAA"],'
        b'"bars":[],"updatedBars":[],"dailyBars":[],"statuses":[],"lulds":[],'
        b'"corrections":["BBB","AAA"],"cancelErrors":["BBB","AAA"]}]'
    )
