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
from trading_agent.chrome_devtools_transport import (
    ChromeDebugPort,
    LoopbackChromeDevToolsTransport,
    LoopbackChromeHealthProbe,
)
from trading_agent.chrome_devtools_types import CdpCommand, CdpMethod, InvalidChromeDevToolsError


class _ChromeHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str]]] = []
    debug_port: ClassVar[int] = 0
    version_padding: ClassVar[str] = ""
    version_valid: ClassVar[bool] = True
    listed_target_valid: ClassVar[bool] = True
    new_target_valid: ClassVar[bool] = True

    def do_GET(self) -> None:
        self._reply()

    def do_PUT(self) -> None:
        self._reply()

    def _reply(self) -> None:
        type(self).requests.append((self.command, self.path))
        port = type(self).debug_port
        if self.path == "/json/version":
            payload = {
                "Browser": "Chrome/140" if type(self).version_valid else "",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser/token",
                "padding": type(self).version_padding,
            }
        elif self.path == "/json/list":
            payload = [
                {
                    "id": "page-1" if type(self).listed_target_valid else "",
                    "type": "page",
                    "title": "Page",
                    "url": "https://example.com",
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/page-1",
                }
            ]
        elif self.path == "/json/new":
            payload = {
                "id": "page-2" if type(self).new_target_valid else "",
                "type": "page",
                "title": "",
                "url": "about:blank",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/page-2",
            }
        else:
            self.send_error(404)
            return
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *_args: str) -> None:
        _ = format
        return


@pytest.fixture
def chrome_http() -> Iterator[tuple[ThreadingHTTPServer, int]]:
    _ChromeHandler.requests = []
    _ChromeHandler.version_padding = ""
    _ChromeHandler.version_valid = True
    _ChromeHandler.listed_target_valid = True
    _ChromeHandler.new_target_valid = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChromeHandler)
    _ChromeHandler.debug_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, server.server_port
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_loopback_http_uses_only_reviewed_chrome_paths(chrome_http: tuple[ThreadingHTTPServer, int]) -> None:
    # Given: a loopback endpoint exposing the reviewed discovery surface.
    _server, port = chrome_http
    transport = LoopbackChromeDevToolsTransport(ChromeDebugPort(port), timeout_seconds=1.0)
    # When: health, status, and target creation are requested.
    assert LoopbackChromeHealthProbe(timeout_seconds=1.0).probe(ChromeDebugPort(port), "/devtools/browser/token")
    status = transport.status()
    target = transport.create_target()
    # Then: the client never expands beyond the three exact endpoints.
    assert (status.ready, status.active_page_count, target.target_id) == (True, 1, "page-2")
    assert _ChromeHandler.requests == [
        ("GET", "/json/version"),
        ("GET", "/json/version"),
        ("GET", "/json/list"),
        ("PUT", "/json/new"),
    ]


def test_health_probe_rejects_browser_websocket_path_mismatch(
    chrome_http: tuple[ThreadingHTTPServer, int],
) -> None:
    # Given: a healthy loopback Chrome version endpoint.
    _server, port = chrome_http
    # When: the ownership file's browser path does not match Chrome's endpoint.
    healthy = LoopbackChromeHealthProbe(timeout_seconds=1.0).probe(ChromeDebugPort(port), "/devtools/browser/replaced")
    # Then: controller attachment fails closed without exposing a response body.
    assert healthy is False


def test_transport_rejects_invalid_port_before_http() -> None:
    # Given: a non-TCP debug port value.
    # When: the transport is constructed.
    with pytest.raises(InvalidChromeDevToolsError):
        _ = LoopbackChromeDevToolsTransport(ChromeDebugPort(0), timeout_seconds=1.0)
    # Then: no network request can be made.


def test_health_probe_rejects_oversized_http_body(chrome_http: tuple[ThreadingHTTPServer, int]) -> None:
    # Given: a loopback endpoint returning more than the bounded HTTP response size.
    _server, port = chrome_http
    _ChromeHandler.version_padding = "x" * (65 * 1024)
    # When: health is probed.
    healthy = LoopbackChromeHealthProbe(timeout_seconds=1.0).probe(ChromeDebugPort(port), "/devtools/browser/token")
    # Then: the body is rejected without surfacing its content.
    assert healthy is False


def test_new_target_converts_invalid_chrome_json_to_stable_reason(
    chrome_http: tuple[ThreadingHTTPServer, int],
) -> None:
    # Given: Chrome returns a structurally invalid target descriptor.
    _server, port = chrome_http
    _ChromeHandler.new_target_valid = False
    transport = LoopbackChromeDevToolsTransport(ChromeDebugPort(port), timeout_seconds=1.0)
    # When: target creation parses the untrusted response.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = transport.create_target()
    # Then: validation details are replaced by the stable public reason.
    assert raised.value.reason == "browser_navigation_blocked"


@pytest.mark.parametrize("boundary", ("version", "list"))
def test_status_converts_invalid_chrome_json_to_stable_reason(
    chrome_http: tuple[ThreadingHTTPServer, int], boundary: str
) -> None:
    # Given: one untrusted Chrome discovery response violates its schema.
    _server, port = chrome_http
    if boundary == "version":
        _ChromeHandler.version_valid = False
    else:
        _ChromeHandler.listed_target_valid = False
    transport = LoopbackChromeDevToolsTransport(ChromeDebugPort(port), timeout_seconds=1.0)
    # When: status parses Chrome discovery JSON.
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = transport.status()
    # Then: no Pydantic detail crosses the transport boundary.
    assert raised.value.reason == "browser_navigation_blocked"


@dataclass(slots=True)
class _FixtureWebSocket:
    """Mutable fixture records the bounded WebSocket contract."""

    responses: list[bytes]
    sent: list[str] = field(default_factory=list)

    def __enter__(self) -> _FixtureWebSocket:
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def recv(self, *, timeout: float, decode: bool) -> bytes:
        assert timeout > 0 and decode is False
        return self.responses.pop(0)


def test_websocket_skips_events_and_requires_monotonic_matching_id(
    chrome_http: tuple[ThreadingHTTPServer, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one CDP socket emits an event before response 1 and the next repeats stale response 1.
    _server, port = chrome_http
    sockets = [
        _FixtureWebSocket([b'{"method":"Page.loadEventFired"}', b'{"id":1,"result":{}}']),
        _FixtureWebSocket([b'{"id":1,"result":{}}']),
    ]
    options: list[dict[str, int | float | None]] = []

    def connector(_url: str, **kwargs: int | float | None) -> _FixtureWebSocket:
        options.append(kwargs)
        return sockets.pop(0)

    monkeypatch.setattr(cdp_transport, "connect", connector)
    transport = LoopbackChromeDevToolsTransport(ChromeDebugPort(port), timeout_seconds=1.0)
    command = CdpCommand(CdpMethod.RUNTIME_EVALUATE, '{"expression":"document.readyState"}')
    # When: two serialized commands receive response identifiers 1 and 1.
    assert transport.command("page-1", command) == b'{"id":1,"result":{}}'
    with pytest.raises(InvalidChromeDevToolsError) as raised:
        _ = transport.command("page-1", command)
    # Then: stale IDs fail closed and every socket disables proxies with a 1 MiB cap.
    assert raised.value.reason == "browser_navigation_blocked"
    assert [(item["proxy"], item["max_size"]) for item in options] == [
        (None, 1024 * 1024),
        (None, 1024 * 1024),
    ]
