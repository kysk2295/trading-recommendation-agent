from __future__ import annotations

import socket
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.chrome_devtools_types import ChromeDevToolsStatus, InvalidChromeDevToolsError
from trading_agent.local_browser_dispatch import BrowserClient
from trading_agent.local_browser_gateway import (
    BrowserDispatchDependencies,
    BrowserRequestDispatcher,
    LocalBrowserGateway,
)
from trading_agent.local_browser_protocol import (
    BrowserPageObservation,
    BrowserScreenshotReceipt,
    BrowserStatusRequest,
)
from trading_agent.local_browser_receipts import LocalBrowserReceiptStore
from trading_agent.local_browser_socket import (
    InvalidLocalBrowserSocketError,
    LocalBrowserSocketClient,
    LocalBrowserSocketServer,
)
from trading_agent.local_chrome_endpoint import ChromeDebugPort, LocalChromeEndpoint

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def short_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="browser-e2e-", dir="/private/tmp") as value:
        root = Path(value)
        root.chmod(0o700)
        yield root


class CountingController:
    def __init__(self) -> None:
        self.calls = 0

    def ensure_ready(self) -> LocalChromeEndpoint:
        self.calls += 1
        return LocalChromeEndpoint(
            port=ChromeDebugPort(9222),
            browser_path="/devtools/browser/e2e",
            browser_websocket_url="ws://127.0.0.1:9222/devtools/browser/e2e",
            ownership="attached",
            process_id=None,
        )


class StatusOnlyClient:
    def status(self) -> ChromeDevToolsStatus:
        return ChromeDevToolsStatus(True, 7)

    def search(self, query: str, *, captured_at: datetime) -> BrowserPageObservation:
        raise InvalidChromeDevToolsError(reason=f"unsupported:{query}:{captured_at}")

    def open(self, url: str, *, captured_at: datetime) -> BrowserPageObservation:
        raise InvalidChromeDevToolsError(reason=f"unsupported:{url}:{captured_at}")

    def read(self, target_id: str, *, captured_at: datetime) -> BrowserPageObservation:
        raise InvalidChromeDevToolsError(reason=f"unsupported:{target_id}:{captured_at}")

    def follow(self, target_id: str, link_index: int, *, captured_at: datetime) -> BrowserPageObservation:
        raise InvalidChromeDevToolsError(reason=f"unsupported:{target_id}:{link_index}:{captured_at}")

    def capture(self, target_id: str, root: Path, *, captured_at: datetime) -> BrowserScreenshotReceipt:
        raise InvalidChromeDevToolsError(reason=f"unsupported:{target_id}:{root}:{captured_at}")


class StatusClientFactory:
    def create(self, endpoint: LocalChromeEndpoint) -> BrowserClient:
        assert endpoint.port == 9222
        return StatusOnlyClient()


def _gateway(store: LocalBrowserReceiptStore, controller: CountingController, root: Path) -> LocalBrowserGateway:
    dependencies = BrowserDispatchDependencies(controller, StatusClientFactory(), lambda: NOW)
    return LocalBrowserGateway(store, BrowserRequestDispatcher(dependencies, root / "screens"))


def _round_trip(root: Path, controller: CountingController, request: BrowserStatusRequest) -> None:
    errors: list[InvalidLocalBrowserSocketError] = []
    ready = threading.Event()

    def serve() -> None:
        try:
            with (
                LocalBrowserReceiptStore(root / "receipts.sqlite3") as store,
                LocalBrowserSocketServer(root / "gateway.sock", _gateway(store, controller, root)) as server,
            ):
                ready.set()
                server.serve_once()
        except InvalidLocalBrowserSocketError as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(timeout=2.0)
    response = LocalBrowserSocketClient(root / "gateway.sock", timeout_seconds=1.0).request(request)
    thread.join(timeout=2.0)
    assert response.status_payload is not None and response.status_payload.ready
    assert response.status_payload.active_page_count == 7
    assert not thread.is_alive()
    assert errors == []


def test_actual_socket_replays_receipt_after_gateway_restart_without_chrome(short_root: Path) -> None:
    state = short_root / "state"
    request = BrowserStatusRequest(request_id="a" * 64)
    first_controller = CountingController()
    _round_trip(state, first_controller, request)
    restarted_controller = CountingController()
    _round_trip(state, restarted_controller, request)
    assert (first_controller.calls, restarted_controller.calls) == (1, 0)


@pytest.mark.parametrize(
    "hostile_url",
    (
        b"https://example.com/?api%5Fkey=withheld",
        b"https://example.com/anything/auth-token%3Dwithheld",
    ),
)
def test_raw_hostile_open_is_redacted_and_gateway_keeps_serving_without_chrome(
    short_root: Path, hostile_url: bytes
) -> None:
    # Given: the real socket gateway is ready to serve a hostile frame followed by a valid request.
    state = short_root / "state"
    socket_path = state / "gateway.sock"
    controller = CountingController()
    hostile_handled = threading.Event()
    ready = threading.Event()
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            with (
                LocalBrowserReceiptStore(state / "receipts.sqlite3") as store,
                LocalBrowserSocketServer(socket_path, _gateway(store, controller, state)) as server,
            ):
                ready.set()
                try:
                    server.serve_once()
                except InvalidLocalBrowserSocketError as error:
                    assert error.reason == "browser_request_invalid"
                    hostile_handled.set()
                server.serve_once()
        except BaseException as error:  # noqa: RUF100  # noqa: BROAD_EXCEPT_OK: capture boundary crash
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(timeout=2.0)

    # When: an untyped client submits a canonical open frame with sensitive URL metadata.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1.0)
        client.connect(str(socket_path))
        client.sendall(b'{"action":"open","request_id":"' + b"a" * 64 + b'","url":"' + hostile_url + b'"}\n')
        client.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b""

    # Then: the frame is rejected before dispatch or a receipt write, and the gateway serves again.
    assert hostile_handled.wait(timeout=2.0)
    assert controller.calls == 0
    response = LocalBrowserSocketClient(socket_path, timeout_seconds=1.0).request(
        BrowserStatusRequest(request_id="b" * 64)
    )
    thread.join(timeout=2.0)
    assert response.status_payload is not None and response.status_payload.ready
    assert not thread.is_alive()
    assert errors == []
    assert controller.calls == 1
    with sqlite3.connect(state / "receipts.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM local_browser_requests").fetchone() == (1,)
