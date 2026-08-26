from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.chrome_devtools_types import ChromeDevToolsStatus
from trading_agent.local_browser_gateway import (
    BrowserDispatchDependencies,
    BrowserRequestDispatcher,
    InvalidLocalBrowserGatewayError,
    LocalBrowserGateway,
    canonical_browser_request,
    canonical_browser_response,
    parse_browser_request,
)
from trading_agent.local_browser_gateway_wire import InvalidLocalBrowserWireError
from trading_agent.local_browser_protocol import (
    BrowserAction,
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserScreenshotReceipt,
    BrowserStatusRequest,
)
from trading_agent.local_browser_receipts import LocalBrowserReceiptStore
from trading_agent.local_chrome_controller import InvalidLocalChromeControllerError
from trading_agent.local_chrome_endpoint import ChromeDebugPort, LocalChromeEndpoint

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


class FakeController:
    def __init__(self) -> None:
        self.calls = 0

    def ensure_ready(self) -> LocalChromeEndpoint:
        self.calls += 1
        return LocalChromeEndpoint(
            port=ChromeDebugPort(9222),
            browser_path="/devtools/browser/test",
            browser_websocket_url="ws://127.0.0.1:9222/devtools/browser/test",
            ownership="attached",
            process_id=None,
        )


class FakeClient:
    def status(self) -> ChromeDevToolsStatus:
        return ChromeDevToolsStatus(ready=True, active_page_count=1)

    def open(self, url: str, *, captured_at: datetime) -> BrowserPageObservation:
        return BrowserPageObservation(target_id="target-1", url=url, title="Story", captured_at=captured_at)

    def search(self, query: str, *, captured_at: datetime) -> BrowserPageObservation:
        return self.open(f"https://example.com/search/{query}", captured_at=captured_at)

    def read(self, target_id: str, *, captured_at: datetime) -> BrowserPageObservation:
        return self.open(f"https://example.com/{target_id}", captured_at=captured_at)

    def follow(self, target_id: str, link_index: int, *, captured_at: datetime) -> BrowserPageObservation:
        return self.read(f"{target_id}-{link_index}", captured_at=captured_at)

    def capture(self, target_id: str, root: Path, *, captured_at: datetime) -> BrowserScreenshotReceipt:
        return BrowserScreenshotReceipt(
            path=str(root / f"{target_id}.png"),
            sha256="f" * 64,
            width=1,
            height=1,
            captured_at=captured_at,
        )


class FakeClientFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls = 0

    def create(self, endpoint: LocalChromeEndpoint) -> FakeClient:
        assert endpoint.port == 9222
        self.calls += 1
        return self.client


def _dispatcher(controller: FakeController, tmp_path: Path) -> BrowserRequestDispatcher:
    dependencies = BrowserDispatchDependencies(
        controller=controller, client_factory=FakeClientFactory(FakeClient()), now=lambda: NOW
    )
    return BrowserRequestDispatcher(dependencies, tmp_path / "screens")


def test_request_wire_is_canonical_and_round_trips() -> None:
    request = BrowserOpenRequest(request_id="a" * 64, url="https://EXAMPLE.com/story#fragment")
    payload = canonical_browser_request(request)
    assert payload == (
        b'{"action":"open","request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"url":"https://example.com/story"}'
    )
    assert parse_browser_request(payload) == request


def test_exact_replay_after_restart_does_not_touch_chrome(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    request = BrowserStatusRequest(request_id="b" * 64)
    first_controller = FakeController()
    with LocalBrowserReceiptStore(path) as store:
        gateway = LocalBrowserGateway(
            store,
            _dispatcher(first_controller, tmp_path),
        )
        first = gateway.handle(request)
    restarted_controller = FakeController()
    with LocalBrowserReceiptStore(path) as restarted:
        gateway = LocalBrowserGateway(
            restarted,
            _dispatcher(restarted_controller, tmp_path),
        )
        replay = gateway.handle(request)
    assert replay == first
    assert first.action is BrowserAction.STATUS
    assert (first_controller.calls, restarted_controller.calls) == (1, 0)


def test_changed_payload_conflicts_before_dispatch(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    controller = FakeController()
    first = BrowserOpenRequest(request_id="c" * 64, url="https://example.com/first")
    changed = BrowserOpenRequest(request_id="c" * 64, url="https://example.org/changed")
    with LocalBrowserReceiptStore(state / "receipts.sqlite3") as store:
        gateway = LocalBrowserGateway(store, _dispatcher(controller, tmp_path))
        _ = gateway.handle(first)
        with pytest.raises(InvalidLocalBrowserGatewayError) as raised:
            _ = gateway.handle(changed)
    assert raised.value.reason == "browser_request_id_conflict"
    assert controller.calls == 1


def test_dispatch_error_is_redacted_receipted_and_replayed(tmp_path: Path) -> None:
    class FailingController(FakeController):
        def ensure_ready(self) -> LocalChromeEndpoint:
            self.calls += 1
            raise InvalidLocalChromeControllerError(reason="secret process detail")

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    controller = FailingController()
    request = BrowserStatusRequest(request_id="e" * 64)
    with LocalBrowserReceiptStore(state / "receipts.sqlite3") as store:
        gateway = LocalBrowserGateway(store, _dispatcher(controller, tmp_path))
        first = gateway.handle(request)
        replay = gateway.handle(request)
    assert first == replay
    assert first.failure is not None
    assert first.failure.reason.value == "browser_navigation_blocked"
    assert "secret" not in canonical_browser_response(first).decode()
    assert controller.calls == 1


@pytest.mark.parametrize(
    "payload",
    (
        b"x" * (16 * 1024 + 1),
        b' {"action":"status","request_id":"' + b"d" * 64 + b'"}',
    ),
)
def test_request_wire_rejects_oversized_or_noncanonical_json(payload: bytes) -> None:
    with pytest.raises(InvalidLocalBrowserWireError):
        _ = parse_browser_request(payload)
