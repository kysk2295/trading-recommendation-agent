from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)


class UsDayChampionBootstrapLeaseError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "champion_bootstrap_lease_invalid"


@dataclass(frozen=True, slots=True)
class UsDayChampionBootstrapLease:
    path: Path
    parent_descriptor: int
    lock_descriptor: int

    def require_bound(self) -> None:
        try:
            _require_binding(self.path, self.parent_descriptor, self.lock_descriptor)
        except (InvalidPrivateDirectoryIdentityError, OSError, ValueError):
            raise UsDayChampionBootstrapLeaseError from None


@dataclass(frozen=True, slots=True)
class _LeaseResources:
    parent: int
    descriptor: int
    descriptor_locked: bool


@contextmanager
def us_day_champion_bootstrap_lease(version_store: Path) -> Iterator[UsDayChampionBootstrapLease]:
    lock_path = absolute_private_path(
        version_store.parent / f".{version_store.name}.champion-bootstrap.lock"
    )
    parent = descriptor = -1
    descriptor_locked = False
    try:
        parent = _open_parent(lock_path.parent)
        require_private_directory_query_only(parent)
        descriptor = _open_lock(parent, lock_path.name)
        _require_binding(lock_path, parent, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptor_locked = True
        _require_binding(lock_path, parent, descriptor)
    except (BlockingIOError, InvalidPrivateDirectoryIdentityError, OSError, ValueError):
        _close(_LeaseResources(parent, descriptor, descriptor_locked))
        raise UsDayChampionBootstrapLeaseError from None
    try:
        lease = UsDayChampionBootstrapLease(lock_path, parent, descriptor)
        yield lease
    finally:
        _close(_LeaseResources(parent, descriptor, descriptor_locked))


def _open_lock(parent: int, name: str) -> int:
    flags = os.O_CLOEXEC | os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        return descriptor
    except FileExistsError:
        return os.open(name, os.O_CLOEXEC | os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)


def _open_parent(path: Path) -> int:
    try:
        return open_private_parent(path, create=False)
    except FileNotFoundError:
        return open_private_parent(path, create=True)


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
        raise UsDayChampionBootstrapLeaseError


def _close(resources: _LeaseResources) -> None:
    if resources.descriptor_locked:
        with suppress(OSError):
            fcntl.flock(resources.descriptor, fcntl.LOCK_UN)
    for descriptor in (resources.descriptor, resources.parent):
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)


__all__ = (
    "UsDayChampionBootstrapLease",
    "UsDayChampionBootstrapLeaseError",
    "us_day_champion_bootstrap_lease",
)
