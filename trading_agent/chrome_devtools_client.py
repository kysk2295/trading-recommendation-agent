from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trading_agent.chrome_devtools_types import (
    CdpCommand,
    CdpMethod,
    ChromeDevToolsStatus,
    ChromeDevToolsTransport,
    ChromeTarget,
    InvalidChromeDevToolsError,
)
from trading_agent.local_browser_protocol import (
    BrowserPageObservation,
    BrowserScreenshotReceipt,
    BrowserVisibleLink,
    InvalidLocalBrowserProtocolError,
    require_public_https_url,
)
from trading_agent.local_browser_screenshot import (
    InvalidLocalBrowserScreenshotError,
    publish_private_screenshot,
)

_DOM_EXPRESSION: Final = """(() => JSON.stringify((() => {
const body = document.body;
const visible = Array.from(document.querySelectorAll('a[href]')).filter((anchor) => {
  const style = getComputedStyle(anchor); const rect = anchor.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
}).slice(0, 100).map((anchor) => ({label: (anchor.innerText || '').trim().slice(0, 200),
url: String(anchor.href || '').slice(0, 2048)}));
return {title: String(document.title || '').slice(0, 500), url: String(location.href || '').slice(0, 2048),
text: String(body ? body.innerText : '').slice(0, 12000), links: visible};
})()))()"""
_READY_EXPRESSION: Final = "document.readyState"
_METADATA_EXPRESSION: Final = (
    "JSON.stringify({title:String(document.title||'').slice(0,500),"
    "url:String(location.href||'').slice(0,2048)})"
)
_SCREENSHOT_LIMIT: Final = 8 * 1024 * 1024


class _Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class _BoundaryModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True, hide_input_in_errors=True)


class _RuntimeValue(_BoundaryModel):
    kind: str = Field(alias="type")
    value: str = Field(max_length=1024 * 1024)


class _RuntimeResult(_BoundaryModel):
    result: _RuntimeValue


class _RuntimeResponse(_BoundaryModel):
    result: _RuntimeResult


class _NavigationResult(_BoundaryModel):
    error_text: str | None = Field(default=None, alias="errorText", max_length=500)


class _NavigationResponse(_BoundaryModel):
    result: _NavigationResult


class _ScreenshotResult(_BoundaryModel):
    data: str = Field(max_length=12 * 1024 * 1024)


class _ScreenshotResponse(_BoundaryModel):
    result: _ScreenshotResult


class _PageLink(_BoundaryModel):
    label: str = Field(max_length=500)
    url: str = Field(max_length=4_096)


class _PagePayload(_BoundaryModel):
    title: str = Field(max_length=1_000)
    url: str = Field(max_length=4_096)
    text: str = Field(max_length=1024 * 1024)
    links: tuple[_PageLink, ...] = Field(max_length=100)


class _MetadataPayload(_BoundaryModel):
    title: str = Field(max_length=1_000)
    url: str = Field(max_length=4_096)


class ChromeDevToolsClient:
    def __init__(
        self,
        transport: ChromeDevToolsTransport,
        *,
        command_timeout_seconds: float = 20.0,
        clock: _Clock | None = None,
        owner_id: int | None = None,
    ) -> None:
        if not 0 < command_timeout_seconds <= 60:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        self._transport = transport
        self._timeout = command_timeout_seconds
        self._clock = clock or time
        self._owner_id = os.getuid() if owner_id is None else owner_id

    def status(self) -> ChromeDevToolsStatus:
        return self._transport.status()

    def search(self, query: str, *, captured_at: datetime) -> BrowserPageObservation:
        normalized = query.strip()
        if not normalized or len(normalized) > 500:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        return self.open(f"https://www.google.com/search?{urlencode({'q': normalized})}", captured_at=captured_at)

    def open(self, url: str, *, captured_at: datetime) -> BrowserPageObservation:
        normalized = require_public_https_url(url)
        target = self._transport.create_target()
        return self._navigate(target.target_id, normalized, captured_at)

    def read(self, target_id: str, *, captured_at: datetime) -> BrowserPageObservation:
        _require_target_id(target_id)
        observation = self._page(target_id, captured_at)
        if not observation.visible_text.strip():
            raise InvalidChromeDevToolsError(reason="browser_visible_text_unavailable")
        return observation

    def follow(self, target_id: str, link_index: int, *, captured_at: datetime) -> BrowserPageObservation:
        _require_target_id(target_id)
        observation = self._page(target_id, captured_at)
        if not 0 <= link_index < len(observation.links):
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        return self._navigate(target_id, observation.links[link_index].url, captured_at)

    def capture(self, target_id: str, root: Path, *, captured_at: datetime) -> BrowserScreenshotReceipt:
        _require_target_id(target_id)
        response = self._command(
            target_id,
            CdpCommand(CdpMethod.PAGE_CAPTURE_SCREENSHOT, '{"format":"png","fromSurface":true}'),
        )
        try:
            encoded = _ScreenshotResponse.model_validate_json(response).result.data
            payload = base64.b64decode(encoded, validate=True)
            width, height = _png_dimensions(payload)
            digest = hashlib.sha256(payload).hexdigest()
            path = publish_private_screenshot(root, payload, digest, self._owner_id)
            return BrowserScreenshotReceipt(
                path=str(path), sha256=digest, width=width, height=height, captured_at=captured_at
            )
        except (binascii.Error, InvalidLocalBrowserScreenshotError, ValidationError, ValueError):
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None

    def _navigate(self, target_id: str, url: str, captured_at: datetime) -> BrowserPageObservation:
        _require_target_id(target_id)
        command = CdpCommand(CdpMethod.PAGE_NAVIGATE, json.dumps({"url": url}, separators=(",", ":")))
        response = self._command(target_id, command)
        try:
            result = _NavigationResponse.model_validate_json(response).result
        except ValidationError:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None
        if result.error_text:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        self._wait_ready(target_id)
        return self._metadata(target_id, captured_at)

    def _wait_ready(self, target_id: str) -> None:
        deadline = self._clock.monotonic() + self._timeout
        while True:
            state = self._runtime_value(target_id, _READY_EXPRESSION)
            if state in {"interactive", "complete"}:
                return
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                raise InvalidChromeDevToolsError(reason="browser_cdp_timeout")
            self._clock.sleep(min(0.05, remaining))

    def _page(self, target_id: str, captured_at: datetime) -> BrowserPageObservation:
        try:
            payload = _PagePayload.model_validate_json(self._runtime_value(target_id, _DOM_EXPRESSION))
            url = require_public_https_url(payload.url)
            links: list[BrowserVisibleLink] = []
            for candidate in payload.links:
                if not 8 <= len(candidate.url) <= 2_048:
                    continue
                try:
                    normalized = require_public_https_url(candidate.url)
                except InvalidLocalBrowserProtocolError:
                    continue
                links.append(BrowserVisibleLink(label=candidate.label.strip()[:200], url=normalized))
                if len(links) == 40:
                    break
            return BrowserPageObservation(
                target_id=target_id,
                url=url,
                title=payload.title[:500],
                visible_text=payload.text[:12_000],
                links=tuple(links),
                captured_at=captured_at,
            )
        except (InvalidLocalBrowserProtocolError, ValidationError):
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None

    def _metadata(self, target_id: str, captured_at: datetime) -> BrowserPageObservation:
        try:
            payload = _MetadataPayload.model_validate_json(self._runtime_value(target_id, _METADATA_EXPRESSION))
            return BrowserPageObservation(
                target_id=target_id,
                url=require_public_https_url(payload.url),
                title=payload.title[:500],
                captured_at=captured_at,
            )
        except (InvalidLocalBrowserProtocolError, ValidationError):
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None

    def _runtime_value(self, target_id: str, expression: str) -> str:
        params = json.dumps(
            {"expression": expression, "returnByValue": True, "awaitPromise": False},
            separators=(",", ":"),
        )
        response = self._command(target_id, CdpCommand(CdpMethod.RUNTIME_EVALUATE, params))
        try:
            value = _RuntimeResponse.model_validate_json(response).result.result
        except ValidationError:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None
        if value.kind != "string":
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        return value.value

    def _command(self, target_id: str, command: CdpCommand) -> bytes:
        return self._transport.command(target_id, command)


def _require_target_id(target_id: str) -> None:
    if not 1 <= len(target_id) <= 256 or any(ord(character) < 33 for character in target_id):
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 24 or len(payload) > _SCREENSHOT_LIMIT or payload[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
    width, height = struct.unpack(">II", payload[16:24])
    if not 1 <= width <= 20_000 or not 1 <= height <= 20_000:
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
    return width, height


__all__ = [
    "CdpCommand",
    "CdpMethod",
    "ChromeDevToolsClient",
    "ChromeDevToolsStatus",
    "ChromeTarget",
    "InvalidChromeDevToolsError",
]
