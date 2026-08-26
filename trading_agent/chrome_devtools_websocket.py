from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from trading_agent.chrome_devtools_types import CdpCommand, CdpMethod, InvalidChromeDevToolsError
from trading_agent.chrome_paused_request import InvalidPausedRequestIdentityError, parse_paused_request

_COMMAND_LIMIT: Final = 16 * 1024
_MESSAGE_LIMIT: Final = 1024 * 1024


class _Clock(Protocol):
    def monotonic(self) -> float: ...


class _WebSocketConnection(Protocol):
    close_timeout: float | None

    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None, decode: bool | None = None) -> bytes | str: ...


class _BoundaryModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True, hide_input_in_errors=True)


class _CdpEnvelope(_BoundaryModel):
    request_id: int | None = Field(default=None, alias="id", ge=1)
    method: str | None = Field(default=None, min_length=1, max_length=128)


@dataclass(slots=True)
class _InterceptionState:
    disable_safe: bool = True


class SerializedChromeWebSocket:
    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self._request_id = 0
        self._lock = threading.Lock()

    def command(self, websocket_url: str, command: CdpCommand, deadline: float) -> bytes:
        with self._lock:
            request_id = self._next_request_id()
            payload = _command_payload(request_id, command)
            return self._exchange(websocket_url, request_id, payload, deadline)

    def navigate_guarded(self, websocket_url: str, url: str, deadline: float) -> bytes:
        with self._lock:
            try:
                return self._guarded_exchange(websocket_url, url, deadline)
            except TimeoutError:
                raise InvalidChromeDevToolsError(reason="browser_cdp_timeout") from None
            except (InvalidPausedRequestIdentityError, OSError, ValidationError, WebSocketException):
                raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _exchange(self, websocket_url: str, request_id: int, payload: str, deadline: float) -> bytes:
        try:
            remaining = self._remaining(deadline)
            matched: bytes | None = None
            with connect(
                websocket_url,
                open_timeout=remaining,
                close_timeout=remaining,
                ping_interval=None,
                compression=None,
                proxy=None,
                max_size=_MESSAGE_LIMIT,
                max_queue=1,
            ) as connection:
                try:
                    connection.send(payload)
                    while True:
                        body = _message_bytes(connection.recv(timeout=self._remaining(deadline), decode=False))
                        _ = self._remaining(deadline)
                        envelope = _CdpEnvelope.model_validate_json(body)
                        if envelope.request_id is None:
                            continue
                        if envelope.request_id != request_id:
                            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
                        matched = body
                        break
                finally:
                    connection.close_timeout = max(0.0, deadline - self._clock.monotonic())
            _ = self._remaining(deadline)
            if matched is None:
                raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
            return matched
        except TimeoutError:
            raise InvalidChromeDevToolsError(reason="browser_cdp_timeout") from None
        except (OSError, ValidationError, WebSocketException):
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked") from None

    def _guarded_exchange(self, websocket_url: str, url: str, deadline: float) -> bytes:
        remaining = self._remaining(deadline)
        response: bytes | None = None
        with connect(
            websocket_url,
            open_timeout=remaining,
            close_timeout=remaining,
            ping_interval=None,
            compression=None,
            proxy=None,
            max_size=_MESSAGE_LIMIT,
            max_queue=1,
        ) as connection:
            try:
                navigate_id: int | None = None
                interception = _InterceptionState()
                enable_id = self._send(
                    connection,
                    CdpCommand(
                        CdpMethod.FETCH_ENABLE,
                        '{"patterns":[{"urlPattern":"*","resourceType":"Document","requestStage":"Request"}]}',
                    ),
                )
                _ = self._await_response(connection, enable_id, deadline)
                try:
                    navigate_id = self._send(
                        connection,
                        CdpCommand(CdpMethod.PAGE_NAVIGATE, json.dumps({"url": url}, separators=(",", ":"))),
                    )
                    response = self._await_guarded_navigation(connection, navigate_id, deadline, interception)
                finally:
                    if interception.disable_safe:
                        disable_id = self._send(connection, CdpCommand(CdpMethod.FETCH_DISABLE, "{}"))
                        _ = self._await_response(connection, disable_id, deadline, prior_request_id=navigate_id)
            finally:
                connection.close_timeout = max(0.0, deadline - self._clock.monotonic())
        _ = self._remaining(deadline)
        if response is None:
            raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
        return response

    def _await_guarded_navigation(
        self,
        connection: _WebSocketConnection,
        navigate_id: int,
        deadline: float,
        interception: _InterceptionState,
    ) -> bytes:
        pending_actions: set[int] = set()
        navigation_response: bytes | None = None
        blocked = False
        while True:
            try:
                body = self._receive(connection, deadline)
                envelope = _CdpEnvelope.model_validate_json(body)
            except TimeoutError:
                raise
            except (InvalidChromeDevToolsError, OSError, ValidationError, WebSocketException):
                interception.disable_safe = False
                raise
            if envelope.method == CdpMethod.FETCH_REQUEST_PAUSED:
                interception.disable_safe = False
                paused = parse_paused_request(body)
                if blocked or not paused.allowed:
                    blocked = True
                    action = CdpCommand(
                        CdpMethod.FETCH_FAIL_REQUEST,
                        json.dumps(
                            {"requestId": paused.request_id, "errorReason": "BlockedByClient"},
                            separators=(",", ":"),
                        ),
                    )
                else:
                    action = CdpCommand(
                        CdpMethod.FETCH_CONTINUE_REQUEST,
                        json.dumps({"requestId": paused.request_id}, separators=(",", ":")),
                    )
                pending_actions.add(self._send(connection, action))
                continue
            if envelope.request_id == navigate_id:
                navigation_response = body
            elif envelope.request_id in pending_actions:
                pending_actions.remove(envelope.request_id)
                interception.disable_safe = not pending_actions
            elif envelope.request_id is not None:
                raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
            if blocked and not pending_actions:
                raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
            if navigation_response is not None and not pending_actions:
                return navigation_response

    def _send(self, connection: _WebSocketConnection, command: CdpCommand) -> int:
        request_id = self._next_request_id()
        connection.send(_command_payload(request_id, command))
        return request_id

    def _await_response(
        self,
        connection: _WebSocketConnection,
        request_id: int,
        deadline: float,
        *,
        prior_request_id: int | None = None,
    ) -> bytes:
        while True:
            body = self._receive(connection, deadline)
            envelope = _CdpEnvelope.model_validate_json(body)
            if envelope.request_id is None:
                continue
            if envelope.request_id == prior_request_id:
                continue
            if envelope.request_id != request_id:
                raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
            return body

    def _receive(self, connection: _WebSocketConnection, deadline: float) -> bytes:
        body = _message_bytes(connection.recv(timeout=self._remaining(deadline), decode=False))
        _ = self._remaining(deadline)
        return body

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock.monotonic()
        if remaining <= 0:
            raise InvalidChromeDevToolsError(reason="browser_cdp_timeout")
        return remaining


def _command_payload(request_id: int, command: CdpCommand) -> str:
    payload = f'{{"id":{request_id},"method":{json.dumps(command.method.value)},"params":{command.params_json}}}'
    if len(payload.encode()) > _COMMAND_LIMIT:
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
    return payload


def _message_bytes(message: bytes | str) -> bytes:
    body = message if isinstance(message, bytes) else message.encode()
    if len(body) > _MESSAGE_LIMIT:
        raise InvalidChromeDevToolsError(reason="browser_navigation_blocked")
    return body


__all__ = ["SerializedChromeWebSocket"]
