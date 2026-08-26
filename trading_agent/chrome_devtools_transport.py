from __future__ import annotations

import json
import re
import socket
import threading
import time
from typing import Final, Literal
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from trading_agent.chrome_devtools_types import (
    CdpCommand,
    ChromeDevToolsStatus,
    ChromeTarget,
    InvalidChromeDevToolsError,
)
from trading_agent.local_chrome_endpoint import ChromeDebugPort

_HTTP_BODY_LIMIT: Final = 64 * 1024
_WS_MESSAGE_LIMIT: Final = 1024 * 1024
_TARGET_LIMIT: Final = 100
_PATH_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{1,256}")
_HttpMethod = Literal["GET", "PUT"]
_HttpPath = Literal["/json/version", "/json/list", "/json/new"]


class _BoundaryModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True, hide_input_in_errors=True)


class _VersionPayload(_BoundaryModel):
    browser: str = Field(alias="Browser", min_length=1, max_length=256)
    websocket_url: str = Field(alias="webSocketDebuggerUrl", min_length=1, max_length=2_048)


class _TargetPayload(_BoundaryModel):
    target_id: str = Field(alias="id", min_length=1, max_length=256)
    kind: str = Field(alias="type", min_length=1, max_length=64)
    title: str = Field(default="", max_length=500)
    url: str = Field(max_length=2_048)
    websocket_url: str = Field(alias="webSocketDebuggerUrl", min_length=1, max_length=2_048)


class _CdpEnvelope(_BoundaryModel):
    request_id: int | None = Field(default=None, alias="id", ge=1)


_TARGETS = TypeAdapter(tuple[_TargetPayload, ...])


class LoopbackChromeDevToolsTransport:
    """Mutable serialized CDP transport owns monotonic command identifiers."""

    def __init__(self, port: ChromeDebugPort, *, timeout_seconds: float) -> None:
        if not 1 <= int(port) <= 65_535 or not 0 < timeout_seconds <= 60:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        self._port = port
        self._timeout = timeout_seconds
        self._request_id = 0
        self._lock = threading.Lock()

    def status(self) -> ChromeDevToolsStatus:
        _ = self._version()
        targets = self._targets()
        return ChromeDevToolsStatus(True, len(targets))

    def create_target(self) -> ChromeTarget:
        try:
            payload = _TargetPayload.model_validate_json(self._request("PUT", "/json/new"))
        except ValidationError:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None
        if payload.kind != "page":
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        return self._target(payload)

    def command(self, target_id: str, command: CdpCommand) -> bytes:
        target = next((candidate for candidate in self._targets() if candidate.target_id == target_id), None)
        if target is None:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            payload = (
                f'{{"id":{request_id},"method":{json.dumps(command.method.value)},'
                f'"params":{command.params_json}}}'
            )
            if len(payload.encode()) > 16 * 1024:
                raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
            return self._exchange(target.websocket_url, request_id, payload)

    def _version(self) -> _VersionPayload:
        try:
            payload = _VersionPayload.model_validate_json(self._request("GET", "/json/version"))
        except ValidationError:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None
        expected = "/devtools/browser/"
        _ = _loopback_websocket(payload.websocket_url, self._port, expected)
        return payload

    def _targets(self) -> tuple[ChromeTarget, ...]:
        try:
            payloads = _TARGETS.validate_json(self._request("GET", "/json/list"))
        except ValidationError:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None
        pages = tuple(self._target(payload) for payload in payloads if payload.kind == "page")
        if len(payloads) > _TARGET_LIMIT or len(pages) > _TARGET_LIMIT:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        return pages

    def _target(self, payload: _TargetPayload) -> ChromeTarget:
        websocket_url = _loopback_websocket(payload.websocket_url, self._port, "/devtools/page/")
        if not payload.target_id or _PATH_TOKEN.fullmatch(payload.target_id) is None:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        if urlsplit(websocket_url).path != f"/devtools/page/{payload.target_id}":
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        return ChromeTarget(payload.target_id, payload.url, payload.title, websocket_url)

    def _request(self, method: _HttpMethod, path: _HttpPath) -> bytes:
        timeout = httpx.Timeout(self._timeout, connect=self._timeout)
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=5.0)
        transport = httpx.HTTPTransport(
            trust_env=False,
            retries=0,
            limits=limits,
            socket_options=((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),),
        )
        try:
            with (
                httpx.Client(
                    base_url=f"http://127.0.0.1:{int(self._port)}",
                    transport=transport,
                    timeout=timeout,
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream(method, path) as response,
            ):
                response.raise_for_status()
                if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                    raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _HTTP_BODY_LIMIT:
                        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
                return bytes(body)
        except httpx.TimeoutException:
            raise InvalidChromeDevToolsError(reason="browser_cdp_timeout") from None
        except httpx.HTTPError:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None

    def _exchange(self, websocket_url: str, request_id: int, payload: str) -> bytes:
        deadline = time.monotonic() + self._timeout
        try:
            with connect(
                websocket_url,
                open_timeout=self._timeout,
                close_timeout=self._timeout,
                ping_interval=None,
                compression=None,
                proxy=None,
                max_size=_WS_MESSAGE_LIMIT,
                max_queue=1,
            ) as connection:
                connection.send(payload)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise InvalidChromeDevToolsError(reason="browser_cdp_timeout")
                    message = connection.recv(timeout=remaining, decode=False)
                    match message:
                        case bytes() as body:
                            pass
                        case str() as text:
                            body = text.encode()
                    if len(body) > _WS_MESSAGE_LIMIT:
                        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
                    envelope = _CdpEnvelope.model_validate_json(body)
                    if envelope.request_id is None:
                        continue
                    if envelope.request_id != request_id:
                        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
                    return body
        except TimeoutError:
            raise InvalidChromeDevToolsError(reason="browser_cdp_timeout") from None
        except (OSError, ValidationError, WebSocketException):
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None


class LoopbackChromeHealthProbe:
    def __init__(self, *, timeout_seconds: float) -> None:
        if not 0 < timeout_seconds <= 60:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        self._timeout = timeout_seconds

    def probe(self, port: ChromeDebugPort, path: str) -> bool:
        try:
            transport = LoopbackChromeDevToolsTransport(port, timeout_seconds=self._timeout)
            payload = transport._version()
            return urlsplit(payload.websocket_url).path == path
        except (InvalidChromeDevToolsError, ValidationError):
            return False


def _loopback_websocket(url: str, port: ChromeDebugPort, prefix: str) -> str:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "ws"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == int(port)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path.startswith(prefix)
            and _PATH_TOKEN.fullmatch(parsed.path.removeprefix(prefix)) is not None
        )
    except ValueError:
        valid = False
    if not valid:
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
    return url


__all__ = ["ChromeDebugPort", "LoopbackChromeDevToolsTransport", "LoopbackChromeHealthProbe"]
