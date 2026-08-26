from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import pytest

import trading_agent.local_browser_receipt_sqlite as receipt_sqlite
from tests.test_local_browser_gateway import NOW, FakeClient, FakeClientFactory
from trading_agent.local_browser_gateway import (
    BrowserDispatchDependencies,
    BrowserRequestDispatcher,
    InvalidLocalBrowserGatewayError,
    LocalBrowserGateway,
    canonical_browser_response,
)
from trading_agent.local_browser_gateway_wire import BrowserRequest
from trading_agent.local_browser_protocol import BrowserOpenRequest, BrowserResponse, BrowserStatusRequest
from trading_agent.local_browser_receipts import InvalidLocalBrowserReceiptError, LocalBrowserReceiptStore
from trading_agent.local_chrome_endpoint import ChromeDebugPort, LocalChromeEndpoint


class RacingController:
    def __init__(self, rendezvous: threading.Barrier | None = None) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self._rendezvous = rendezvous

    def ensure_ready(self) -> LocalChromeEndpoint:
        with self._lock:
            self.calls += 1
        if self._rendezvous is not None:
            with suppress(threading.BrokenBarrierError):
                self._rendezvous.wait(timeout=0.5)
        return _endpoint()


class OrderedController(RacingController):
    def __init__(self) -> None:
        super().__init__()
        self.first_dispatched = threading.Event()
        self.contender_dispatched = threading.Event()
        self.release_first = threading.Event()

    def ensure_ready(self) -> LocalChromeEndpoint:
        with self._lock:
            self.calls += 1
            call = self.calls
        if call == 1:
            self.first_dispatched.set()
            assert self.release_first.wait(timeout=2.0)
        else:
            self.contender_dispatched.set()
        return _endpoint()


def _endpoint() -> LocalChromeEndpoint:
    return LocalChromeEndpoint(
        port=ChromeDebugPort(9222),
        browser_path="/devtools/browser/race",
        browser_websocket_url="ws://127.0.0.1:9222/devtools/browser/race",
        ownership="attached",
        process_id=None,
    )


def _dispatcher(controller: RacingController, root: Path) -> BrowserRequestDispatcher:
    dependencies = BrowserDispatchDependencies(controller, FakeClientFactory(FakeClient()), lambda: NOW)
    return BrowserRequestDispatcher(dependencies, root / "screens")


def _worker(
    path: Path,
    controller: RacingController,
    request: BrowserRequest,
    accepted: dict[str, BrowserResponse],
    rejected: dict[str, str],
    label: str,
) -> Callable[[], None]:
    def run() -> None:
        try:
            with LocalBrowserReceiptStore(path) as store:
                accepted[label] = LocalBrowserGateway(store, _dispatcher(controller, path.parent)).handle(request)
        except InvalidLocalBrowserGatewayError as error:
            rejected[label] = error.reason

    return run


def _receipt_counts(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as connection:
        requests = connection.execute("SELECT COUNT(*) FROM local_browser_requests").fetchone()
        responses = connection.execute("SELECT COUNT(*) FROM local_browser_responses").fetchone()
    assert requests is not None and responses is not None
    return int(requests[0]), int(responses[0])


def test_two_stores_serialize_same_request_before_dispatch(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    with LocalBrowserReceiptStore(path):
        pass
    controller = RacingController(threading.Barrier(2))
    request = BrowserStatusRequest(request_id="a" * 64)
    accepted: dict[str, BrowserResponse] = {}
    rejected: dict[str, str] = {}
    workers = tuple(
        threading.Thread(target=_worker(path, controller, request, accepted, rejected, str(index)))
        for index in range(2)
    )
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3.0)
    assert not any(worker.is_alive() for worker in workers)
    assert rejected == {}
    assert len(accepted) == 2
    assert len({canonical_browser_response(response) for response in accepted.values()}) == 1
    assert controller.calls == 1
    assert _receipt_counts(path) == (1, 1)


def test_two_stores_initialize_fresh_database_before_serialized_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    prepare_gate = threading.Barrier(2)
    start_gate = threading.Barrier(2)
    original_prepare = receipt_sqlite._prepare_connection

    def prepare_together(connection: sqlite3.Connection) -> None:
        with suppress(threading.BrokenBarrierError):
            prepare_gate.wait(timeout=0.5)
        original_prepare(connection)

    monkeypatch.setattr(receipt_sqlite, "_prepare_connection", prepare_together)
    controller = RacingController()
    request = BrowserStatusRequest(request_id="e" * 64)
    accepted: dict[str, BrowserResponse] = {}
    rejected: dict[str, str] = {}

    def run(label: str) -> None:
        start_gate.wait(timeout=2.0)
        try:
            with LocalBrowserReceiptStore(path) as store:
                accepted[label] = LocalBrowserGateway(store, _dispatcher(controller, state)).handle(request)
        except (InvalidLocalBrowserGatewayError, InvalidLocalBrowserReceiptError) as error:
            rejected[label] = error.reason

    workers = tuple(threading.Thread(target=run, args=(str(index),)) for index in range(2))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3.0)
    assert not any(worker.is_alive() for worker in workers)
    assert rejected == {}
    assert len(accepted) == 2
    assert len({canonical_browser_response(response) for response in accepted.values()}) == 1
    assert controller.calls == 1
    assert _receipt_counts(path) == (1, 1)


def test_changed_payload_contender_conflicts_before_dispatch(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "receipts.sqlite3"
    with LocalBrowserReceiptStore(path):
        pass
    controller = OrderedController()
    accepted: dict[str, BrowserResponse] = {}
    rejected: dict[str, str] = {}
    first = BrowserOpenRequest(request_id="b" * 64, url="https://example.com/first")
    changed = BrowserOpenRequest(request_id="b" * 64, url="https://example.org/changed")
    primary = threading.Thread(target=_worker(path, controller, first, accepted, rejected, "primary"))
    contender = threading.Thread(target=_worker(path, controller, changed, accepted, rejected, "changed"))
    primary.start()
    assert controller.first_dispatched.wait(timeout=2.0)
    contender.start()
    _ = controller.contender_dispatched.wait(timeout=0.2)
    controller.release_first.set()
    primary.join(timeout=3.0)
    contender.join(timeout=3.0)
    assert not primary.is_alive() and not contender.is_alive()
    assert set(accepted) == {"primary"}
    assert rejected == {"changed": "browser_request_id_conflict"}
    assert controller.calls == 1
    assert _receipt_counts(path) == (1, 1)
