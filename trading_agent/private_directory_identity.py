from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Final, override

_DIRECTORY_MODE: Final = 0o700


class InvalidPrivateDirectoryIdentityError(ValueError):
    @override
    def __str__(self) -> str:
        return "private directory identity is invalid"


def absolute_private_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def open_private_parent(path: Path, *, create: bool) -> int:
    absolute = absolute_private_path(path)
    descriptor = os.open(absolute.anchor, _directory_open_flags())
    try:
        for component in absolute.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(component, _DIRECTORY_MODE, dir_fd=descriptor)
                    os.fsync(descriptor)
            next_descriptor = os.open(component, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except (OSError, TypeError, ValueError):
        os.close(descriptor)
        raise


def require_open_directory_path(path: Path, expected_descriptor: int) -> None:
    current = open_private_parent(path, create=False)
    try:
        require_same_file(expected_descriptor, current)
    finally:
        os.close(current)


def require_same_file(left_descriptor: int, right_descriptor: int) -> None:
    left = os.fstat(left_descriptor)
    right = os.fstat(right_descriptor)
    if (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino):
        raise InvalidPrivateDirectoryIdentityError


def require_private_directory(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise InvalidPrivateDirectoryIdentityError
    os.fchmod(descriptor, _DIRECTORY_MODE)


def require_private_directory_query_only(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE
    ):
        raise InvalidPrivateDirectoryIdentityError


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
