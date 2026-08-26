from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.chrome_devtools_types import ChromeDevToolsStatus, InvalidChromeDevToolsError
from trading_agent.local_browser_gateway import (
    BrowserDispatchDependencies,
    BrowserRequestDispatcher,
)
from trading_agent.local_browser_gateway_wire import BrowserRequest, canonical_browser_response
from trading_agent.local_browser_protocol import (
    BrowserAction,
    BrowserCaptureRequest,
    BrowserFollowRequest,
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserReadRequest,
    BrowserScreenshotReceipt,
    BrowserSearchRequest,
    BrowserStatusRequest,
    BrowserVisibleLink,
)
from trading_agent.local_chrome_endpoint import ChromeDebugPort, LocalChromeEndpoint

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


class UnexpectedTestError(RuntimeError):
    pass


class FakeController:
    def __init__(self, *, explode: bool = False) -> None:
        self.explode = explode

    def ensure_ready(self) -> LocalChromeEndpoint:
        if self.explode:
            raise UnexpectedTestError("secret controller detail")
        return LocalChromeEndpoint(
            port=ChromeDebugPort(9222),
            browser_path="/devtools/browser/test",
            browser_websocket_url="ws://127.0.0.1:9222/devtools/browser/test",
            ownership="attached",
            process_id=None,
        )


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class FakeClient:
    """Record calls while injecting test-only failures."""

    calls: list[tuple[str, str]] = field(default_factory=list)
    explode: bool = False

    def status(self) -> ChromeDevToolsStatus:
        if self.explode:
            raise UnexpectedTestError("secret client detail")
        self.calls.append(("status", ""))
        return ChromeDevToolsStatus(True, 7)

    def search(self, query: str, *, captured_at: datetime) -> BrowserPageObservation:
        self.calls.append(("search", query))
        return self._observation("search-target", "https://example.com/search", captured_at)

    def open(self, url: str, *, captured_at: datetime) -> BrowserPageObservation:
        self.calls.append(("open", url))
        return self._observation("open-target", url, captured_at)

    def read(self, target_id: str, *, captured_at: datetime) -> BrowserPageObservation:
        self.calls.append(("read", target_id))
        return self._observation(target_id, "https://example.com/read", captured_at, links=True)

    def follow(self, target_id: str, link_index: int, *, captured_at: datetime) -> BrowserPageObservation:
        self.calls.append(("follow", f"{target_id}:{link_index}"))
        return self._observation(target_id, "https://example.org/followed", captured_at)

    def capture(self, target_id: str, root: Path, *, captured_at: datetime) -> BrowserScreenshotReceipt:
        self.calls.append(("capture", f"{target_id}:{root.name}"))
        return BrowserScreenshotReceipt(
            path=str(root / "capture.png"),
            sha256="f" * 64,
            width=2,
            height=3,
            captured_at=captured_at,
        )

    @staticmethod
    def _observation(target_id: str, url: str, captured_at: datetime, *, links: bool = False) -> BrowserPageObservation:
        visible_links = (BrowserVisibleLink(label="Result", url="https://example.org/result"),) if links else ()
        return BrowserPageObservation(
            target_id=target_id,
            url=url,
            title="Page",
            visible_text="text",
            links=visible_links,
            captured_at=captured_at,
        )


class FakeFactory:
    def __init__(self, client: FakeClient, *, explode: bool = False) -> None:
        self._client = client
        self._explode = explode

    def create(self, endpoint: LocalChromeEndpoint) -> FakeClient:
        if self._explode:
            raise UnexpectedTestError("secret factory detail")
        assert endpoint.port == 9222
        return self._client


def _dispatcher(client: FakeClient, root: Path) -> BrowserRequestDispatcher:
    dependencies = BrowserDispatchDependencies(FakeController(), FakeFactory(client), lambda: NOW)
    return BrowserRequestDispatcher(dependencies, root)


def _exploding_dispatcher(source: str, root: Path) -> BrowserRequestDispatcher:
    controller = FakeController(explode=source == "controller")
    factory = FakeFactory(FakeClient(explode=source == "client"), explode=source == "factory")
    clock_calls = 0

    def now() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        if source == "clock" and clock_calls == 1:
            raise UnexpectedTestError("secret clock detail")
        return NOW

    return BrowserRequestDispatcher(BrowserDispatchDependencies(controller, factory, now), root)


def test_dispatches_exactly_the_six_protocol_actions(tmp_path: Path) -> None:
    client = FakeClient()
    dispatcher = _dispatcher(client, tmp_path / "screens")
    requests = (
        BrowserStatusRequest(request_id="1" * 64),
        BrowserSearchRequest(request_id="2" * 64, query="AI chips"),
        BrowserOpenRequest(request_id="3" * 64, url="https://example.com/open"),
        BrowserReadRequest(request_id="4" * 64, target_id="read-target"),
        BrowserFollowRequest(request_id="5" * 64, target_id="follow-target", link_index=1),
        BrowserCaptureRequest(request_id="6" * 64, target_id="capture-target"),
    )
    responses = tuple(dispatcher.dispatch(request) for request in requests)
    assert tuple(response.action for response in responses) == tuple(BrowserAction)
    assert all(response.status == "ok" for response in responses)
    assert responses[0].status_payload is not None
    assert responses[0].status_payload.active_page_count == 7
    assert responses[1].search_results[0].url == "https://example.org/result"
    assert responses[2].observation is not None
    assert responses[5].screenshot is not None
    assert client.calls == [
        ("status", ""),
        ("search", "AI chips"),
        ("read", "search-target"),
        ("open", "https://example.com/open"),
        ("read", "read-target"),
        ("follow", "follow-target:1"),
        ("capture", "capture-target:screens"),
    ]


def test_dispatch_redacts_unreviewed_chrome_error_reason(tmp_path: Path) -> None:
    class FailingClient(FakeClient):
        def open(self, url: str, *, captured_at: datetime) -> BrowserPageObservation:
            raise InvalidChromeDevToolsError(reason=f"secret-token:{url}:{captured_at}")

    response = _dispatcher(FailingClient(), tmp_path).dispatch(
        BrowserOpenRequest(request_id="a" * 64, url="https://example.com")
    )
    assert response.status == "error"
    assert response.failure is not None
    assert response.failure.reason.value == "browser_navigation_blocked"


@pytest.mark.parametrize("source", ("controller", "factory", "client", "clock"))
def test_unexpected_dispatch_errors_are_stable_and_redacted(source: str, tmp_path: Path) -> None:
    request: BrowserRequest = BrowserStatusRequest(request_id="b" * 64)
    response = _exploding_dispatcher(source, tmp_path).dispatch(request)
    assert canonical_browser_response(response) == (
        b'{"action":"status","failure":{"reason":"browser_navigation_blocked"},'
        b'"request_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
        b'"status":"error"}'
    )


@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit))
def test_dispatch_does_not_catch_process_interruptions(interruption: type[BaseException], tmp_path: Path) -> None:
    class InterruptingController(FakeController):
        def ensure_ready(self) -> LocalChromeEndpoint:
            raise interruption()

    dependencies = BrowserDispatchDependencies(InterruptingController(), FakeFactory(FakeClient()), lambda: NOW)
    dispatcher = BrowserRequestDispatcher(dependencies, tmp_path)
    with pytest.raises(interruption):
        dispatcher.dispatch(BrowserStatusRequest(request_id="c" * 64))
