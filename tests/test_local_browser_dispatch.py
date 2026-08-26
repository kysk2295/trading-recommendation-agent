from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.chrome_devtools_types import ChromeDevToolsStatus, InvalidChromeDevToolsError
from trading_agent.local_browser_gateway import (
    BrowserDispatchDependencies,
    BrowserRequestDispatcher,
)
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


class FakeController:
    def ensure_ready(self) -> LocalChromeEndpoint:
        return LocalChromeEndpoint(
            port=ChromeDebugPort(9222),
            browser_path="/devtools/browser/test",
            browser_websocket_url="ws://127.0.0.1:9222/devtools/browser/test",
            ownership="attached",
            process_id=None,
        )


@dataclass(slots=True)
class FakeClient:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def status(self) -> ChromeDevToolsStatus:
        self.calls.append(("status", ""))
        return ChromeDevToolsStatus(True, 1)

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
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def create(self, endpoint: LocalChromeEndpoint) -> FakeClient:
        assert endpoint.port == 9222
        return self._client


def _dispatcher(client: FakeClient, root: Path) -> BrowserRequestDispatcher:
    dependencies = BrowserDispatchDependencies(FakeController(), FakeFactory(client), lambda: NOW)
    return BrowserRequestDispatcher(dependencies, root)


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
