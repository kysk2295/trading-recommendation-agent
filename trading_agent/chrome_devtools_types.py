from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(slots=True)
class InvalidChromeDevToolsError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class CdpMethod(StrEnum):
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

    def command(self, target_id: str, command: CdpCommand) -> bytes: ...
