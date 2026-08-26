from __future__ import annotations

import errno
import fcntl
import os
import secrets
import socket
import stat
from dataclasses import dataclass
from pathlib import Path

from trading_agent.local_browser_atomic_rename import (
    AtomicRenameConflictError,
    AtomicRenameUnavailableError,
    rename_entry_exclusively,
)
from trading_agent.private_directory_identity import (
    open_private_parent,
    require_open_directory_path,
)

_LEASE_NAME = ".local-browser-gateway.lease"
_QUARANTINE_PREFIX = ".browser-socket-cleanup-"
_STAGING_PREFIX = ".browser-socket-"


class InvalidPrivateBrowserSocketError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PrivateSocketIdentity:
    device: int
    inode: int
    owner_id: int


@dataclass(frozen=True, slots=True)
class PrivateSocketResources:
    listener: socket.socket
    parent_descriptor: int
    lease_descriptor: int
    identity: PrivateSocketIdentity


class PrivateUnixSocketBinding:
    def __init__(self, path: Path, resources: PrivateSocketResources) -> None:
        self.path = path
        self.listener = resources.listener
        self._resources = resources
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.listener.close()
        try:
            _unlink_socket_if_matches(
                self._resources.parent_descriptor,
                self.path.name,
                self._resources.identity,
            )
        finally:
            fcntl.flock(self._resources.lease_descriptor, fcntl.LOCK_UN)
            os.close(self._resources.lease_descriptor)
            os.close(self._resources.parent_descriptor)


def bind_private_unix_socket(path: Path, owner_id: int) -> PrivateUnixSocketBinding:
    absolute = path.absolute()
    parent = open_private_parent(absolute.parent, create=True)
    lease: int | None = None
    listener: socket.socket | None = None
    published_name: str | None = None
    identity: PrivateSocketIdentity | None = None
    try:
        _require_private_parent(absolute.parent, parent, owner_id)
        lease = _acquire_lease(parent, owner_id)
        existing = _socket_identity(parent, absolute.name, owner_id)
        if existing is not None:
            _unlink_socket_if_matches(parent, absolute.name, existing)
        _require_private_parent(absolute.parent, parent, owner_id)
        staging_name = f"{_STAGING_PREFIX}{secrets.token_hex(8)}"
        staging_path = absolute.parent / staging_name
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(staging_path))
        os.chmod(staging_name, 0o600, dir_fd=parent, follow_symlinks=False)
        identity = _required_socket_identity(parent, staging_name, owner_id)
        published_name = staging_name
        rename_entry_exclusively(parent, staging_name, parent, absolute.name)
        published_name = absolute.name
        if _required_socket_identity(parent, absolute.name, owner_id) != identity:
            raise OSError
        _require_private_parent(absolute.parent, parent, owner_id)
        listener.listen(8)
        resources = PrivateSocketResources(listener, parent, lease, identity)
        return PrivateUnixSocketBinding(absolute, resources)
    except (AtomicRenameConflictError, AtomicRenameUnavailableError, OSError, TypeError, ValueError):
        if listener is not None:
            listener.close()
        cleanup_error: (
            AtomicRenameConflictError | AtomicRenameUnavailableError | OSError | TypeError | ValueError | None
        ) = None
        try:
            if identity is not None and published_name is not None:
                _unlink_socket_if_matches(parent, published_name, identity)
        except (
            AtomicRenameConflictError,
            AtomicRenameUnavailableError,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            cleanup_error = error
        finally:
            if lease is not None:
                fcntl.flock(lease, fcntl.LOCK_UN)
                os.close(lease)
            os.close(parent)
        raise InvalidPrivateBrowserSocketError from cleanup_error


def require_private_socket_path(path: Path, owner_id: int) -> PrivateSocketIdentity:
    parent = open_private_parent(path.absolute().parent, create=False)
    try:
        _require_private_parent(path.absolute().parent, parent, owner_id)
        return _required_socket_identity(parent, path.name, owner_id)
    except (OSError, TypeError, ValueError):
        raise InvalidPrivateBrowserSocketError from None
    finally:
        os.close(parent)


def _acquire_lease(parent: int, owner_id: int) -> int:
    try:
        descriptor = os.open(
            _LEASE_NAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
    except FileExistsError:
        descriptor = os.open(_LEASE_NAME, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
    metadata = os.fstat(descriptor)
    if not _private_regular(metadata, owner_id):
        os.close(descriptor)
        raise OSError
    locked = False
    try:
        _require_lease_current(parent, descriptor, owner_id)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        _require_lease_current(parent, descriptor, owner_id)
    except BlockingIOError as error:
        os.close(descriptor)
        if error.errno in (errno.EACCES, errno.EAGAIN):
            raise OSError from None
        raise
    except OSError:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise
    return descriptor


def _require_lease_current(parent: int, descriptor: int, owner_id: int) -> None:
    checked = os.fstat(descriptor)
    current = os.stat(_LEASE_NAME, dir_fd=parent, follow_symlinks=False)
    if (
        not _private_regular(checked, owner_id)
        or not _private_regular(current, owner_id)
        or (checked.st_dev, checked.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise OSError


def _require_private_parent(path: Path, descriptor: int, owner_id: int) -> None:
    metadata = os.fstat(descriptor)
    require_open_directory_path(path, descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_id or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise OSError


def _socket_identity(parent: int, name: str, owner_id: int) -> PrivateSocketIdentity | None:
    try:
        metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not _private_socket(metadata, owner_id):
        raise OSError
    return PrivateSocketIdentity(metadata.st_dev, metadata.st_ino, owner_id)


def _required_socket_identity(parent: int, name: str, owner_id: int) -> PrivateSocketIdentity:
    identity = _socket_identity(parent, name, owner_id)
    if identity is None:
        raise OSError
    return identity


def _private_socket(metadata: os.stat_result, owner_id: int) -> bool:
    return (
        stat.S_ISSOCK(metadata.st_mode)
        and metadata.st_uid == owner_id
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _private_regular(metadata: os.stat_result, owner_id: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == owner_id
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _unlink_socket_if_matches(parent: int, name: str, expected: PrivateSocketIdentity) -> None:
    current = _socket_identity(parent, name, expected.owner_id)
    if current is None or current != expected:
        return
    quarantine_name = f"{_QUARANTINE_PREFIX}{secrets.token_hex(16)}"
    os.mkdir(quarantine_name, 0o700, dir_fd=parent)
    quarantine = os.open(quarantine_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    emptied = False
    try:
        rename_entry_exclusively(parent, name, quarantine, name)
        moved = _required_socket_identity(quarantine, name, expected.owner_id)
        if moved != expected:
            rename_entry_exclusively(quarantine, name, parent, name)
            emptied = True
            return
        os.unlink(name, dir_fd=quarantine)
        emptied = True
    finally:
        os.close(quarantine)
        if emptied:
            os.rmdir(quarantine_name, dir_fd=parent)
