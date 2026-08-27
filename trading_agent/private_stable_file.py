from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Final, override

from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)

_FILE_MODE: Final = 0o600
_READ_CHUNK: Final = 64 * 1024


class InvalidPrivateStableFileError(ValueError):
    @override
    def __str__(self) -> str:
        return "private stable file identity is invalid"


def read_private_stable_bytes(path: Path, *, max_bytes: int) -> bytes:
    try:
        target = absolute_private_path(path)
        if not target.name or type(max_bytes) is not int or max_bytes <= 0:
            raise InvalidPrivateStableFileError
        parent = open_private_parent(target.parent, create=False)
        try:
            require_private_directory_query_only(parent)
            descriptor = os.open(
                target.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            try:
                opened_before = os.fstat(descriptor)
                named_before = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                _require_private_identity(opened_before, named_before, max_bytes)
                payload = _read_bounded(descriptor, max_bytes)
                opened_after = os.fstat(descriptor)
                named_after = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
                _require_stable_identity(opened_before, opened_after, named_before, named_after, len(payload))
                require_open_directory_path(target.parent, parent)
                return payload
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except (OSError, TypeError, ValueError):
        raise InvalidPrivateStableFileError from None


def _require_private_identity(
    opened: os.stat_result,
    named: os.stat_result,
    max_bytes: int,
) -> None:
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) != _FILE_MODE
        or opened.st_nlink != 1
        or opened.st_size < 0
        or opened.st_size > max_bytes
        or _identity(opened) != _identity(named)
    ):
        raise InvalidPrivateStableFileError


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    content = bytearray()
    while chunk := os.read(descriptor, min(_READ_CHUNK, max_bytes + 1 - len(content))):
        content.extend(chunk)
        if len(content) > max_bytes:
            raise InvalidPrivateStableFileError
    return bytes(content)


def _require_stable_identity(
    opened_before: os.stat_result,
    opened_after: os.stat_result,
    named_before: os.stat_result,
    named_after: os.stat_result,
    payload_size: int,
) -> None:
    stable = (
        opened_before.st_size,
        opened_before.st_mtime_ns,
        opened_before.st_ctime_ns,
    )
    if (
        stable
        != (
            opened_after.st_size,
            opened_after.st_mtime_ns,
            opened_after.st_ctime_ns,
        )
        or payload_size != opened_before.st_size
        or _identity(opened_before) != _identity(opened_after)
        or _identity(opened_before) != _identity(named_before)
        or _identity(opened_after) != _identity(named_after)
    ):
        raise InvalidPrivateStableFileError


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino
