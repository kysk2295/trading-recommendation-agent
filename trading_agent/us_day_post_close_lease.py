from __future__ import annotations

import fcntl
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID: Final = re.compile(r"^XNYS-[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class UsDayPostCloseLeaseBusyError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "post_close_busy"


class InvalidUsDayPostCloseLeaseError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "post_close_lease_invalid"


@dataclass(frozen=True, slots=True)
class UsDayPostCloseLeaseKey:
    tick_id: str
    session_id: str

    def __post_init__(self) -> None:
        if _SHA256.fullmatch(self.tick_id) is None or _SESSION_ID.fullmatch(self.session_id) is None:
            raise InvalidUsDayPostCloseLeaseError


@dataclass(frozen=True, slots=True)
class UsDayPostCloseLease:
    path: Path
    parent_descriptor: int
    lock_descriptor: int

    def require_bound(self) -> None:
        try:
            _require_binding(self.path, self.parent_descriptor, self.lock_descriptor)
        except (InvalidPrivateDirectoryIdentityError, OSError, ValueError):
            raise InvalidUsDayPostCloseLeaseError from None


@dataclass(frozen=True, slots=True)
class _LeaseResources:
    parent: int
    descriptor: int
    parent_locked: bool
    descriptor_locked: bool


@contextmanager
def us_day_post_close_lease(root: Path, key: UsDayPostCloseLeaseKey) -> Iterator[UsDayPostCloseLease]:
    lock_path = absolute_private_path(root / key.tick_id / f"{key.session_id}.lock")
    parent = descriptor = -1
    parent_locked = descriptor_locked = False
    try:
        parent = open_private_parent(lock_path.parent, create=True)
        require_private_directory(parent)
        descriptor = _open_lock(parent, lock_path.name)
        _require_binding(lock_path, parent, descriptor)
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent_locked = True
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptor_locked = True
        _require_binding(lock_path, parent, descriptor)
    except BlockingIOError:
        _close_lease(_LeaseResources(parent, descriptor, parent_locked, descriptor_locked))
        raise UsDayPostCloseLeaseBusyError from None
    except (InvalidPrivateDirectoryIdentityError, InvalidUsDayPostCloseLeaseError, OSError, ValueError):
        _close_lease(_LeaseResources(parent, descriptor, parent_locked, descriptor_locked))
        raise InvalidUsDayPostCloseLeaseError from None
    try:
        yield UsDayPostCloseLease(lock_path, parent, descriptor)
        try:
            _require_binding(lock_path, parent, descriptor)
        except (InvalidPrivateDirectoryIdentityError, OSError, ValueError):
            raise InvalidUsDayPostCloseLeaseError from None
    finally:
        _close_lease(_LeaseResources(parent, descriptor, parent_locked, descriptor_locked))


def _open_lock(parent: int, name: str) -> int:
    flags = os.O_CLOEXEC | os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        return descriptor
    except FileExistsError:
        return os.open(name, os.O_CLOEXEC | os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)


def _require_binding(path: Path, parent: int, descriptor: int) -> None:
    require_open_directory_path(path.parent, parent)
    named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (
        (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_nlink != 1
    ):
        raise InvalidUsDayPostCloseLeaseError


def _close_lease(resources: _LeaseResources) -> None:
    if resources.descriptor_locked:
        with suppress(OSError):
            fcntl.flock(resources.descriptor, fcntl.LOCK_UN)
    if resources.parent_locked:
        with suppress(OSError):
            fcntl.flock(resources.parent, fcntl.LOCK_UN)
    if resources.descriptor >= 0:
        with suppress(OSError):
            os.close(resources.descriptor)
    if resources.parent >= 0:
        with suppress(OSError):
            os.close(resources.parent)


__all__ = (
    "InvalidUsDayPostCloseLeaseError",
    "UsDayPostCloseLease",
    "UsDayPostCloseLeaseBusyError",
    "UsDayPostCloseLeaseKey",
    "us_day_post_close_lease",
)
