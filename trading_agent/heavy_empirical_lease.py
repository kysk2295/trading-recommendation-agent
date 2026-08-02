from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import override

from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)


class HeavyEmpiricalLeaseError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "heavy empirical process lease unavailable"


@contextmanager
def heavy_empirical_lease(ledger_path: Path) -> Iterator[None]:
    lock_path = absolute_private_path(Path(f"{ledger_path}.m6-heavy.lock"))
    parent = descriptor = -1
    parent_locked = descriptor_locked = False
    try:
        parent = open_private_parent(lock_path.parent, create=True)
        require_private_directory(parent)
        descriptor = os.open(
            lock_path.name,
            os.O_CLOEXEC | os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        _require_binding(lock_path, parent, descriptor)
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent_locked = True
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptor_locked = True
        _require_binding(lock_path, parent, descriptor)
    except (BlockingIOError, InvalidPrivateDirectoryIdentityError, OSError, ValueError):
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        if parent >= 0:
            with suppress(OSError):
                os.close(parent)
        raise HeavyEmpiricalLeaseError from None
    try:
        yield
        _require_binding(lock_path, parent, descriptor)
    except (InvalidPrivateDirectoryIdentityError, OSError, ValueError):
        raise HeavyEmpiricalLeaseError from None
    finally:
        if descriptor_locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if parent_locked:
            fcntl.flock(parent, fcntl.LOCK_UN)
        os.close(descriptor)
        os.close(parent)


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
        raise HeavyEmpiricalLeaseError
