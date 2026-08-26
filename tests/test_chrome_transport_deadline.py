from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import ClassVar

import pytest

import trading_agent.chrome_devtools_transport as cdp_transport
from trading_agent.chrome_devtools_transport import ChromeDebugPort, LoopbackChromeDevToolsTransport
from trading_agent.chrome_devtools_types import CdpCommand, CdpMethod, InvalidChromeDevToolsError


@dataclass(slots=True)
class _Clock:
    now: float = 20.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _ListHandler(BaseHTTPRequestHandler):
    clock: ClassVar[_Clock]
    debug_port: ClassVar[int]

    def do_GET(self) -> None:
        if self.path != "/json/list":
            self.send_error(404)
            return
        type(self).clock.advance(0.6)
        port = type(self).debug_port
        body = json.dumps(
            [
                {
                    "id": "page-1",
                    "type": "page",
                    "title": "Page",
                    "url": "https://example.com",
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/page-1",
                }
            ],
            separators=(",", ":"),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *_args: str) -> None:
        _ = format
        return


@pytest.fixture
def staged_http() -> Iterator[tuple[int, _Clock]]:
    clock = _Clock()
    _ListHandler.clock = clock
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ListHandler)
    _ListHandler.debug_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, clock
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@dataclass(slots=True)
class _WebSocket:
    clock: _Clock
    responses: list[bytes] = field(default_factory=lambda: [b'{"id":1,"result":{}}'])
    close_timeout: float | None = None

    def __enter__(self) -> _WebSocket:
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return

    def send(self, _payload: str) -> None:
        return

    def recv(self, *, timeout: float, decode: bool) -> bytes:
        assert timeout == pytest.approx(0.4) and decode is False
        self.clock.advance(0.5)
        return self.responses.pop(0)


@dataclass(slots=True)
class _ClosingWebSocket:
    clock: _Clock
    receive_seconds: float
    close_seconds: float
    close_timeout: float | None = None
    closed: bool = False

    def __enter__(self) -> _ClosingWebSocket:
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def send(self, _payload: str) -> None:
        return

    def recv(self, *, timeout: float, decode: bool) -> bytes:
        assert timeout > 0 and decode is False
        self.clock.advance(self.receive_seconds)
        return b'{"id":1,"result":{}}'

    def close(self) -> None:
        self.closed = True
        self.clock.advance(self.close_seconds)


def test_command_shares_one_deadline_across_http_and_websocket(
    staged_http: tuple[int, _Clock], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: target discovery consumes 0.6s and the matching WebSocket response consumes another 0.5s.
    port, clock = staged_http
    socket = _WebSocket(clock)
    options: dict[str, int | float | None] = {}

    def connector(_url: str, **kwargs: int | float | None) -> _WebSocket:
        options.update(kwargs)
        return socket

    monkeypatch.setattr(cdp_transport, "connect", connector)
    transport = LoopbackChromeDevToolsTransport(ChromeDebugPort(port), timeout_seconds=2.0, clock=clock)
    command = CdpCommand(CdpMethod.RUNTIME_EVALUATE, '{"expression":"document.readyState"}')
    # When: the two stages exceed a one-second command override despite a matching response.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = transport.command("page-1", command, timeout_seconds=1.0)
    # Then: no stage resets the deadline and the socket receives only the post-HTTP remainder.
    assert raised.value.reason == "browser_cdp_timeout"
    assert options["open_timeout"] == pytest.approx(0.4)


def test_exchange_rejects_match_when_close_finishes_at_elapsed_1_05(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: receive consumes 0.95s and mandatory socket close consumes 0.10s of a 1.0s budget.
    clock = _Clock()
    socket = _ClosingWebSocket(clock, receive_seconds=0.95, close_seconds=0.10)

    def connector(_url: str, **kwargs: int | float | None) -> _ClosingWebSocket:
        socket.close_timeout = kwargs["close_timeout"]
        return socket

    monkeypatch.setattr(cdp_transport, "connect", connector)
    transport = LoopbackChromeDevToolsTransport(ChromeDebugPort(9222), timeout_seconds=2.0, clock=clock)
    # When: a matching response arrives before the deadline but close ends at elapsed 1.05s.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = transport._exchange("ws://127.0.0.1:9222/devtools/page/page-1", 1, "{}", clock.now + 1.0)
    # Then: cleanup completed, consumed the remaining budget, and success is rejected stably.
    assert raised.value.reason == "browser_cdp_timeout"
    assert socket.closed is True and clock.now == pytest.approx(21.05)
    assert socket.close_timeout == pytest.approx(0.05)


def test_exchange_returns_match_after_close_within_shared_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: receive consumes 0.75s and close consumes 0.10s of a 1.0s budget.
    clock = _Clock()
    socket = _ClosingWebSocket(clock, receive_seconds=0.75, close_seconds=0.10)

    def connector(_url: str, **kwargs: int | float | None) -> _ClosingWebSocket:
        socket.close_timeout = kwargs["close_timeout"]
        return socket

    monkeypatch.setattr(cdp_transport, "connect", connector)
    transport = LoopbackChromeDevToolsTransport(ChromeDebugPort(9222), timeout_seconds=2.0, clock=clock)
    # When: a matching response and mandatory close both finish before the deadline.
    response = transport._exchange("ws://127.0.0.1:9222/devtools/page/page-1", 1, "{}", clock.now + 1.0)
    # Then: success is returned only after close and close receives the remaining 0.25s.
    assert response == b'{"id":1,"result":{}}'
    assert socket.closed is True and clock.now == pytest.approx(20.85)
    assert socket.close_timeout == pytest.approx(0.25)
