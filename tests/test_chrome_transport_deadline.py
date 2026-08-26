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
