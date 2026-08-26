from __future__ import annotations

import sqlite3
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
from trading_agent.local_browser_receipts import (
    BrowserReceipt,
    InvalidLocalBrowserReceiptError,
    LocalBrowserReceiptStore,
)
from trading_agent.local_chrome_controller import InvalidLocalChromeControllerError
from trading_agent.local_chrome_endpoint import ChromeDebugPort, LocalChromeEndpoint

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


class UnexpectedTestError(RuntimeError):
    pass


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
    def __init__(self, *, explode: bool = False) -> None:
        self.explode = explode

    def status(self) -> ChromeDevToolsStatus:
        if self.explode:
            raise UnexpectedTestError("secret client detail")
        return ChromeDevToolsStatus(ready=True, active_page_count=7)

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
    def __init__(self, client: FakeClient, *, explode: bool = False) -> None:
        self.client = client
        self.calls = 0
        self.explode = explode

    def create(self, endpoint: LocalChromeEndpoint) -> FakeClient:
        if self.explode:
            raise UnexpectedTestError("secret factory detail")
        assert endpoint.port == 9222
        self.calls += 1
        return self.client


def _dispatcher(controller: FakeController, tmp_path: Path, *, source: str | None = None) -> BrowserRequestDispatcher:
    clock_calls = 0

    def now() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        if source == "clock" and clock_calls == 1:
            raise UnexpectedTestError("secret clock detail")
        return NOW

    dependencies = BrowserDispatchDependencies(
        controller=controller,
        client_factory=FakeClientFactory(FakeClient(explode=source == "client"), explode=source == "factory"),
        now=now,
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
    assert first.status_payload is not None and first.status_payload.active_page_count == 7
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


def test_receipt_append_failure_remains_fail_closed(tmp_path: Path) -> None:
    class FailingAppendStore(LocalBrowserReceiptStore):
        def append(self, receipt: BrowserReceipt) -> None:
            _ = receipt
            raise InvalidLocalBrowserReceiptError(reason="browser_receipt_invalid")

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    controller = FakeController()
    with FailingAppendStore(state / "receipts.sqlite3") as store:
        gateway = LocalBrowserGateway(store, _dispatcher(controller, tmp_path))
        with pytest.raises(InvalidLocalBrowserGatewayError) as raised:
            gateway.handle(BrowserStatusRequest(request_id="d" * 64))
    assert raised.value.reason == "browser_receipt_invalid"
    assert controller.calls == 1


@pytest.mark.parametrize("source", ("controller", "factory", "client", "clock"))
def test_unexpected_dispatch_error_has_one_durable_replay(source: str, tmp_path: Path) -> None:
    class ExplodingController(FakeController):
        def ensure_ready(self) -> LocalChromeEndpoint:
            if source == "controller":
                self.calls += 1
                raise UnexpectedTestError("secret controller detail")
            return super().ensure_ready()

    state = tmp_path / source
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    request = BrowserStatusRequest(request_id="f" * 64)
    first_controller = ExplodingController()
    with LocalBrowserReceiptStore(path) as store:
        first = LocalBrowserGateway(store, _dispatcher(first_controller, tmp_path, source=source)).handle(request)
    restarted_controller = FakeController()
    with LocalBrowserReceiptStore(path) as restarted:
        replay = LocalBrowserGateway(restarted, _dispatcher(restarted_controller, tmp_path)).handle(request)
    with sqlite3.connect(path) as connection:
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("local_browser_requests", "local_browser_responses")
        )
    assert replay == first
    assert canonical_browser_response(first) == canonical_browser_response(replay)
    assert first.failure is not None and first.failure.reason.value == "browser_navigation_blocked"
    assert "secret" not in canonical_browser_response(first).decode()
    assert counts == (1, 1)
    assert restarted_controller.calls == 0


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
