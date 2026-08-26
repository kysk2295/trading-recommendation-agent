from __future__ import annotations

import os
import socket
import stat
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import trading_agent.local_browser_socket_fs as socket_fs
from trading_agent.local_browser_gateway import canonical_browser_response, parse_browser_request
from trading_agent.local_browser_protocol import (
    BrowserAction,
    BrowserResponse,
    BrowserStatusPayload,
    BrowserStatusRequest,
)
from trading_agent.local_browser_socket import (
    InvalidLocalBrowserSocketError,
    LocalBrowserSocketClient,
    LocalBrowserSocketServer,
)


@dataclass(slots=True)
class EchoGateway:
    calls: int = 0

    def handle_bytes(self, payload: bytes) -> bytes:
        self.calls += 1
        request = parse_browser_request(payload)
        assert isinstance(request, BrowserStatusRequest)
        return canonical_browser_response(
            BrowserResponse(
                request_id=request.request_id,
                action=BrowserAction.STATUS,
                status_payload=BrowserStatusPayload(ready=True),
            )
        )


class RejectPeer:
    def require_current_user(self, connection: socket.socket) -> None:
        _ = connection
        raise InvalidLocalBrowserSocketError(reason="browser_peer_uid_rejected")


@pytest.fixture
def short_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="browser-", dir="/private/tmp") as value:
        root = Path(value)
        root.chmod(0o700)
        yield root


def _serve_once(
    server: LocalBrowserSocketServer,
) -> tuple[threading.Thread, list[InvalidLocalBrowserSocketError]]:
    errors: list[InvalidLocalBrowserSocketError] = []

    def serve() -> None:
        try:
            server.serve_once()
        except InvalidLocalBrowserSocketError as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    return thread, errors


def _finish_server(thread: threading.Thread, errors: list[InvalidLocalBrowserSocketError]) -> None:
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert not errors


def test_real_socket_client_round_trip_is_private_and_peer_authenticated(short_root: Path) -> None:
    state = short_root / "state"
    socket_path = state / "gateway.sock"
    gateway = EchoGateway()
    request = BrowserStatusRequest(request_id="a" * 64)
    with LocalBrowserSocketServer(socket_path, gateway) as server:
        assert stat.S_IMODE(os.lstat(socket_path).st_mode) == 0o600
        thread, errors = _serve_once(server)
        response = LocalBrowserSocketClient(socket_path, timeout_seconds=1.0).request(request)
        _finish_server(thread, errors)
    assert response.status_payload is not None and response.status_payload.ready
    assert gateway.calls == 1
    assert not socket_path.exists()


def test_peer_is_rejected_before_request_read_or_dispatch(short_root: Path) -> None:
    socket_path = short_root / "state" / "gateway.sock"
    gateway = EchoGateway()
    with LocalBrowserSocketServer(socket_path, gateway, peer_credentials=RejectPeer()) as server:
        thread, errors = _serve_once(server)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(b"not-json\n")
        thread.join(timeout=2.0)
    assert gateway.calls == 0
    assert [error.reason for error in errors] == ["browser_peer_uid_rejected"]


def test_client_rejects_oversized_or_mismatched_response(short_root: Path) -> None:
    class OversizedGateway(EchoGateway):
        def handle_bytes(self, payload: bytes) -> bytes:
            _ = payload
            return b"x" * (16 * 1024 + 1)

    path = short_root / "state" / "gateway.sock"
    with LocalBrowserSocketServer(path, OversizedGateway()) as server:
        thread, errors = _serve_once(server)
        with pytest.raises(InvalidLocalBrowserSocketError):
            _ = LocalBrowserSocketClient(path, timeout_seconds=1.0).request(BrowserStatusRequest(request_id="b" * 64))
        thread.join(timeout=2.0)
    assert errors[0].reason == "browser_response_too_large"


def test_client_rejects_response_for_another_request(short_root: Path) -> None:
    class MismatchedGateway(EchoGateway):
        def handle_bytes(self, payload: bytes) -> bytes:
            _ = payload
            return canonical_browser_response(
                BrowserResponse(
                    request_id="c" * 64,
                    action=BrowserAction.STATUS,
                    status_payload=BrowserStatusPayload(ready=True),
                )
            )

    path = short_root / "state" / "gateway.sock"
    with LocalBrowserSocketServer(path, MismatchedGateway()) as server:
        thread, errors = _serve_once(server)
        with pytest.raises(InvalidLocalBrowserSocketError) as raised:
            _ = LocalBrowserSocketClient(path, timeout_seconds=1.0).request(BrowserStatusRequest(request_id="b" * 64))
        _finish_server(thread, errors)
    assert raised.value.reason == "browser_response_request_id_mismatch"


def test_oversized_request_is_rejected_before_dispatch(short_root: Path) -> None:
    path = short_root / "state" / "gateway.sock"
    gateway = EchoGateway()
    with LocalBrowserSocketServer(path, gateway) as server:
        thread, errors = _serve_once(server)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(path))
            client.sendall(b"x" * (16 * 1024 + 1) + b"\n")
        thread.join(timeout=2.0)
    assert gateway.calls == 0
    assert [error.reason for error in errors] == ["browser_request_too_large"]


def test_close_never_unlinks_a_replacement_socket(short_root: Path) -> None:
    path = short_root / "state" / "gateway.sock"
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with LocalBrowserSocketServer(path, EchoGateway()):
            path.unlink()
            replacement.bind(str(path))
            path.chmod(0o600)
        assert path.exists()
        assert stat.S_ISSOCK(os.lstat(path).st_mode)
    finally:
        replacement.close()
        if path.exists():
            path.unlink()


def test_existing_symlink_or_hardlinked_socket_fails_closed(short_root: Path) -> None:
    state = short_root / "state"
    state.mkdir(mode=0o700)
    target = state / "target"
    target.touch(mode=0o600)
    path = state / "gateway.sock"
    path.symlink_to(target)
    with pytest.raises(InvalidLocalBrowserSocketError), LocalBrowserSocketServer(path, EchoGateway()):
        pass
    assert path.is_symlink()


def test_existing_hardlinked_socket_fails_closed(short_root: Path) -> None:
    state = short_root / "state"
    state.mkdir(mode=0o700)
    target = state / "target.sock"
    path = state / "gateway.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
        existing.bind(str(target))
        target.chmod(0o600)
        os.link(target, path)
        with pytest.raises(InvalidLocalBrowserSocketError), LocalBrowserSocketServer(path, EchoGateway()):
            pass
        assert os.lstat(path).st_nlink == 2
    path.unlink()
    target.unlink()


def test_service_lease_prevents_a_second_server_without_removing_the_first(short_root: Path) -> None:
    path = short_root / "state" / "gateway.sock"
    with LocalBrowserSocketServer(path, EchoGateway()):
        original = os.lstat(path)
        with pytest.raises(InvalidLocalBrowserSocketError), LocalBrowserSocketServer(path, EchoGateway()):
            pass
        current = os.lstat(path)
        assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)


def test_service_lease_replacement_during_lock_fails_closed(short_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = short_root / "state" / "gateway.sock"
    lease = path.parent / ".local-browser-gateway.lease"
    original_flock = socket_fs.fcntl.flock
    replacement_inode: list[int] = []

    def replacing_flock(descriptor: int, operation: int) -> None:
        if operation & socket_fs.fcntl.LOCK_EX and not replacement_inode:
            lease.unlink()
            lease.touch(mode=0o600)
            replacement_inode.append(os.lstat(lease).st_ino)
        original_flock(descriptor, operation)

    monkeypatch.setattr(socket_fs.fcntl, "flock", replacing_flock)
    with pytest.raises(InvalidLocalBrowserSocketError), LocalBrowserSocketServer(path, EchoGateway()):
        pass
    assert os.lstat(lease).st_ino == replacement_inode[0]
    assert not path.exists()
