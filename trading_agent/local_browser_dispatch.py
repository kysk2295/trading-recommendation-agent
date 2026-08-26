from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, assert_never

from pydantic import ValidationError

from trading_agent.chrome_devtools_client import ChromeDevToolsClient
from trading_agent.chrome_devtools_transport import LoopbackChromeDevToolsTransport
from trading_agent.chrome_devtools_types import (
    ChromeDevToolsStatus,
    InvalidChromeDevToolsError,
)
from trading_agent.local_browser_gateway_wire import BrowserRequest, request_action
from trading_agent.local_browser_protocol import (
    BrowserCaptureRequest,
    BrowserFailure,
    BrowserFailureReason,
    BrowserFollowRequest,
    BrowserOpenRequest,
    BrowserPageObservation,
    BrowserReadRequest,
    BrowserResponse,
    BrowserScreenshotReceipt,
    BrowserSearchRequest,
    BrowserSearchResult,
    BrowserStatusPayload,
    BrowserStatusRequest,
    InvalidLocalBrowserProtocolError,
)
from trading_agent.local_chrome_controller import InvalidLocalChromeControllerError
from trading_agent.local_chrome_endpoint import LocalChromeEndpoint


class ChromeController(Protocol):
    def ensure_ready(self) -> LocalChromeEndpoint: ...


class BrowserClient(Protocol):
    def status(self) -> ChromeDevToolsStatus: ...
    def search(self, query: str, *, captured_at: datetime) -> BrowserPageObservation: ...
    def open(self, url: str, *, captured_at: datetime) -> BrowserPageObservation: ...
    def read(self, target_id: str, *, captured_at: datetime) -> BrowserPageObservation: ...
    def follow(self, target_id: str, link_index: int, *, captured_at: datetime) -> BrowserPageObservation: ...
    def capture(self, target_id: str, root: Path, *, captured_at: datetime) -> BrowserScreenshotReceipt: ...


class BrowserClientFactory(Protocol):
    def create(self, endpoint: LocalChromeEndpoint) -> BrowserClient: ...


class LoopbackBrowserClientFactory:
    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    def create(self, endpoint: LocalChromeEndpoint) -> BrowserClient:
        transport = LoopbackChromeDevToolsTransport(endpoint.port, timeout_seconds=self._timeout_seconds)
        return ChromeDevToolsClient(transport, command_timeout_seconds=self._timeout_seconds)


@dataclass(frozen=True, slots=True)
class BrowserDispatchDependencies:
    controller: ChromeController
    client_factory: BrowserClientFactory
    now: Callable[[], datetime]


class BrowserRequestDispatcher:
    def __init__(self, dependencies: BrowserDispatchDependencies, screenshot_root: Path) -> None:
        self._dependencies = dependencies
        self._screenshot_root = screenshot_root

    def dispatch(self, request: BrowserRequest) -> BrowserResponse:
        action = request_action(request)
        try:
            endpoint = self._dependencies.controller.ensure_ready()
            client = self._dependencies.client_factory.create(endpoint)
            captured_at = self._dependencies.now()
            match request:
                case BrowserStatusRequest():
                    status = client.status()
                    return BrowserResponse(
                        request_id=request.request_id,
                        action=action,
                        status_payload=BrowserStatusPayload(ready=status.ready),
                    )
                case BrowserSearchRequest(query=query):
                    opened = client.search(query, captured_at=captured_at)
                    page = client.read(opened.target_id, captured_at=captured_at)
                    results = tuple(BrowserSearchResult(title=link.label, url=link.url) for link in page.links)
                    return BrowserResponse(request_id=request.request_id, action=action, search_results=results)
                case BrowserOpenRequest(url=url):
                    observation = client.open(url, captured_at=captured_at)
                    return BrowserResponse(request_id=request.request_id, action=action, observation=observation)
                case BrowserReadRequest(target_id=target_id):
                    observation = client.read(target_id, captured_at=captured_at)
                    return BrowserResponse(request_id=request.request_id, action=action, observation=observation)
                case BrowserFollowRequest(target_id=target_id, link_index=link_index):
                    observation = client.follow(target_id, link_index, captured_at=captured_at)
                    return BrowserResponse(request_id=request.request_id, action=action, observation=observation)
                case BrowserCaptureRequest(target_id=target_id):
                    screenshot = client.capture(target_id, self._screenshot_root, captured_at=captured_at)
                    return BrowserResponse(request_id=request.request_id, action=action, screenshot=screenshot)
                case unreachable:
                    assert_never(unreachable)
        except InvalidChromeDevToolsError as error:
            return _failure(request, _chrome_reason(error.reason))
        except InvalidLocalBrowserProtocolError as error:
            return _failure(request, _chrome_reason(error.reason))
        except (InvalidLocalChromeControllerError, OSError):
            return _failure(request, BrowserFailureReason.NAVIGATION_BLOCKED)
        except ValidationError:
            return _failure(request, BrowserFailureReason.RESPONSE_TOO_LARGE)

    def now(self) -> datetime:
        return self._dependencies.now()


def _failure(request: BrowserRequest, reason: BrowserFailureReason) -> BrowserResponse:
    return BrowserResponse(
        request_id=request.request_id,
        action=request_action(request),
        status="error",
        failure=BrowserFailure(reason=reason),
    )


def _chrome_reason(reason: str) -> BrowserFailureReason:
    try:
        return BrowserFailureReason(reason)
    except ValueError:
        return BrowserFailureReason.NAVIGATION_BLOCKED
