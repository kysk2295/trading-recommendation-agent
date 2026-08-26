from __future__ import annotations

import ctypes
import os
import socket
from pathlib import Path
from typing import Protocol

from trading_agent.local_browser_gateway import InvalidLocalBrowserGatewayError
from trading_agent.local_browser_gateway_wire import (
    MAX_REQUEST_BYTES,
    BrowserRequest,
    InvalidLocalBrowserWireError,
    canonical_browser_request,
    parse_browser_response,
)
from trading_agent.local_browser_protocol import MAX_RESPONSE_BYTES, BrowserResponse
from trading_agent.local_browser_socket_fs import (
    InvalidPrivateBrowserSocketError,
    PrivateBrowserSocketBusyError,
    PrivateUnixSocketBinding,
    bind_private_unix_socket,
    require_private_socket_path,
)


class InvalidLocalBrowserSocketError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class LocalBrowserSocketBusyError(InvalidLocalBrowserSocketError):
    def __init__(self) -> None:
        super().__init__(reason="browser_socket_busy")


class BrowserGatewayHandler(Protocol):
    def handle_bytes(self, payload: bytes) -> bytes: ...


class BrowserPeerCredentials(Protocol):
    def require_current_user(self, connection: socket.socket) -> None: ...


class CurrentUserPeerCredentials:
    def require_current_user(self, connection: socket.socket) -> None:
        try:
            peer_uid = _peer_uid(connection.fileno())
        except (AttributeError, OSError, TypeError, ValueError):
            raise InvalidLocalBrowserSocketError(reason="browser_peer_credentials_unavailable") from None
        if os.getuid() != os.geteuid() or peer_uid != os.geteuid():
            raise InvalidLocalBrowserSocketError(reason="browser_peer_uid_rejected")


def _peer_uid(descriptor: int) -> int:
    peer_uid = ctypes.c_uint()
    peer_gid = ctypes.c_uint()
    getpeereid = ctypes.CDLL("libc.dylib", use_errno=True).getpeereid
    getpeereid.argtypes = (
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint),
    )
    getpeereid.restype = ctypes.c_int
    if getpeereid(descriptor, ctypes.byref(peer_uid), ctypes.byref(peer_gid)) != 0:
        raise OSError
    return peer_uid.value


class LocalBrowserSocketServer:
    def __init__(
        self,
        path: Path,
        handler: BrowserGatewayHandler,
        *,
        peer_credentials: BrowserPeerCredentials | None = None,
    ) -> None:
        self._path = path.absolute()
        self._handler = handler
        self._peer_credentials = peer_credentials or CurrentUserPeerCredentials()
        self._binding: PrivateUnixSocketBinding | None = None

    def __enter__(self) -> LocalBrowserSocketServer:
        try:
            self._binding = bind_private_unix_socket(self._path, os.geteuid())
        except PrivateBrowserSocketBusyError:
            raise LocalBrowserSocketBusyError from None
        except InvalidPrivateBrowserSocketError:
            raise InvalidLocalBrowserSocketError(reason="browser_socket_invalid") from None
        return self

    def __exit__(self, *_details: BaseException | None) -> None:
        binding, self._binding = self._binding, None
        if binding is not None:
            try:
                binding.close()
            except (InvalidPrivateBrowserSocketError, OSError):
                raise InvalidLocalBrowserSocketError(reason="browser_socket_cleanup_invalid") from None

    def serve_once(self) -> None:
        binding = self._binding
        if binding is None:
            raise InvalidLocalBrowserSocketError(reason="browser_socket_closed")
        try:
            connection, _address = binding.listener.accept()
            with connection:
                connection.settimeout(5.0)
                self._peer_credentials.require_current_user(connection)
                request = _read_line(connection, MAX_REQUEST_BYTES, "browser_request_too_large")
                response = self._handler.handle_bytes(request)
                if not response or len(response) > MAX_RESPONSE_BYTES or b"\n" in response:
                    raise InvalidLocalBrowserSocketError(reason="browser_response_too_large")
                connection.sendall(response + b"\n")
        except InvalidLocalBrowserSocketError:
            raise
        except InvalidLocalBrowserGatewayError as error:
            raise InvalidLocalBrowserSocketError(reason=error.reason) from None
        except (OSError, TimeoutError):
            raise InvalidLocalBrowserSocketError(reason="browser_socket_io_failed") from None


class LocalBrowserSocketClient:
    def __init__(self, path: Path, *, timeout_seconds: float) -> None:
        if not 0 < timeout_seconds <= 60:
            raise InvalidLocalBrowserSocketError(reason="browser_socket_timeout_invalid")
        self._path = path.absolute()
        self._timeout_seconds = timeout_seconds

    def request(self, request: BrowserRequest) -> BrowserResponse:
        payload = canonical_browser_request(request)
        try:
            _ = require_private_socket_path(self._path, os.geteuid())
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.connect(str(self._path))
                CurrentUserPeerCredentials().require_current_user(connection)
                connection.sendall(payload + b"\n")
                connection.shutdown(socket.SHUT_WR)
                response_payload = _read_line(connection, MAX_RESPONSE_BYTES, "browser_response_too_large")
            response = parse_browser_response(response_payload)
        except InvalidLocalBrowserSocketError:
            raise
        except (InvalidLocalBrowserWireError, InvalidPrivateBrowserSocketError, OSError, TimeoutError):
            raise InvalidLocalBrowserSocketError(reason="browser_socket_io_failed") from None
        if response.request_id != request.request_id:
            raise InvalidLocalBrowserSocketError(reason="browser_response_request_id_mismatch")
        return response


def _read_line(connection: socket.socket, maximum_bytes: int, oversize_reason: str) -> bytes:
    payload = bytearray()
    while True:
        chunk = connection.recv(min(4096, maximum_bytes + 2 - len(payload)))
        if not chunk:
            raise InvalidLocalBrowserSocketError(reason="browser_socket_frame_invalid")
        payload.extend(chunk)
        if len(payload) > maximum_bytes + 1:
            raise InvalidLocalBrowserSocketError(reason=oversize_reason)
        if b"\n" in chunk:
            break
    if payload.count(b"\n") != 1 or payload[-1:] != b"\n":
        raise InvalidLocalBrowserSocketError(reason="browser_socket_frame_invalid")
    return bytes(payload[:-1])
