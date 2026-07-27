from __future__ import annotations

import selectors
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final, cast

ALLOWED_PROVIDER_HOSTS: Final = frozenset(
    {
        "api.openai.com",
        "chatgpt.com",
        "openrouter.ai",
    }
)
MAX_REQUEST_BYTES: Final = 8 * 1024


class InvalidProviderProxyRequestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderProxy:
    url: str
    port: int


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = _read_request(self.request)
        try:
            host, port = connect_target(request)
        except InvalidProviderProxyRequestError:
            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            return
        try:
            upstream = socket.create_connection((host, port), timeout=15)
        except OSError:
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            return
        with upstream:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _relay(self.request, upstream)


@contextmanager
def restricted_provider_proxy() -> Iterator[ProviderProxy]:
    server = _Server(("127.0.0.1", 0), _Handler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ProviderProxy(url=f"http://127.0.0.1:{port}", port=port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def connect_target(request: bytes) -> tuple[str, int]:
    try:
        first_line = request.split(b"\r\n", 1)[0].decode("ascii")
        method, authority, version = first_line.split(" ")
        host, raw_port = authority.rsplit(":", 1)
        port = int(raw_port)
    except (UnicodeError, ValueError) as error:
        raise InvalidProviderProxyRequestError from error
    normalized = host.lower().rstrip(".")
    if (
        method != "CONNECT"
        or version not in {"HTTP/1.0", "HTTP/1.1"}
        or normalized not in ALLOWED_PROVIDER_HOSTS
        or port != 443
    ):
        raise InvalidProviderProxyRequestError
    return normalized, port


def _read_request(connection: socket.socket) -> bytes:
    payload = bytearray()
    while b"\r\n\r\n" not in payload and len(payload) <= MAX_REQUEST_BYTES:
        chunk = connection.recv(min(1024, MAX_REQUEST_BYTES + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
    return bytes(payload)


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    client.setblocking(False)
    upstream.setblocking(False)
    selector = selectors.DefaultSelector()
    selector.register(client, selectors.EVENT_READ, upstream)
    selector.register(upstream, selectors.EVENT_READ, client)
    try:
        while True:
            events = selector.select(timeout=30)
            if not events:
                return
            for key, _ in events:
                source = cast(socket.socket, key.fileobj)
                destination = cast(socket.socket, key.data)
                try:
                    payload = source.recv(64 * 1024)
                except OSError:
                    return
                if not payload:
                    return
                destination.sendall(payload)
    finally:
        selector.close()


__all__ = (
    "ALLOWED_PROVIDER_HOSTS",
    "InvalidProviderProxyRequestError",
    "ProviderProxy",
    "connect_target",
    "restricted_provider_proxy",
)
