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
    require_same_file,
)

_FILE_MODE: Final = 0o600
_MAX_TEXT_BYTES: Final = 64 * 1024 * 1024


class InvalidPrivateQueryFileError(ValueError):
    @override
    def __str__(self) -> str:
        return "private query file is invalid"


def read_private_text_query_only(path: Path) -> str:
    try:
        target = absolute_private_path(path)
        if not target.name:
            raise InvalidPrivateQueryFileError
        parent_descriptor = open_private_parent(target.parent, create=False)
        try:
            require_private_directory_query_only(parent_descriptor)
            descriptor = _open_final(parent_descriptor, target.name)
            try:
                payload = _read_stable_text(descriptor)
                confirmation = _open_final(parent_descriptor, target.name)
                try:
                    require_same_file(descriptor, confirmation)
                finally:
                    os.close(confirmation)
                require_open_directory_path(target.parent, parent_descriptor)
                return payload
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_descriptor)
    except (OSError, TypeError, UnicodeError, ValueError):
        raise InvalidPrivateQueryFileError from None


def _open_final(parent_descriptor: int, name: str) -> int:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=parent_descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
            or metadata.st_nlink != 1
        ):
            raise InvalidPrivateQueryFileError
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _read_stable_text(descriptor: int) -> str:
    before = os.fstat(descriptor)
    if before.st_size < 0 or before.st_size > _MAX_TEXT_BYTES:
        raise InvalidPrivateQueryFileError
    content = bytearray()
    while chunk := os.read(descriptor, min(64 * 1024, _MAX_TEXT_BYTES + 1 - len(content))):
        content.extend(chunk)
        if len(content) > _MAX_TEXT_BYTES:
            raise InvalidPrivateQueryFileError
    after = os.fstat(descriptor)
    if (
        len(content) != before.st_size
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise InvalidPrivateQueryFileError
    return bytes(content).decode("utf-8")
