from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK: exceptions need writable traceback state
class InvalidChromeDevToolsError(RuntimeError):
    """Carry a typed CDP failure while permitting traceback attachment."""

    reason: str

    def __str__(self) -> str:
        return self.reason


class CdpMethod(StrEnum):
    FETCH_ENABLE = "Fetch.enable"
    FETCH_DISABLE = "Fetch.disable"
    FETCH_CONTINUE_REQUEST = "Fetch.continueRequest"
    FETCH_FAIL_REQUEST = "Fetch.failRequest"
    FETCH_REQUEST_PAUSED = "Fetch.requestPaused"
    PAGE_NAVIGATE = "Page.navigate"
    RUNTIME_EVALUATE = "Runtime.evaluate"
    PAGE_CAPTURE_SCREENSHOT = "Page.captureScreenshot"


@dataclass(frozen=True, slots=True)
class CdpCommand:
    method: CdpMethod
    params_json: str


@dataclass(frozen=True, slots=True)
class ChromeTarget:
    target_id: str
    url: str
    title: str
    websocket_url: str


@dataclass(frozen=True, slots=True)
class ChromeDevToolsStatus:
    ready: bool
    active_page_count: int


class ChromeDevToolsTransport(Protocol):
    def status(self) -> ChromeDevToolsStatus: ...

    def create_target(self) -> ChromeTarget: ...

    def command(
        self,
        target_id: str,
        command: CdpCommand,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes: ...

    def navigate_guarded(
        self,
        target_id: str,
        url: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes: ...
