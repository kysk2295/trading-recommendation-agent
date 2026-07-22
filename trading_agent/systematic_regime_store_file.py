from __future__ import annotations

import os
import secrets
import sqlite3
import stat
from contextlib import suppress
from typing import Final

_MAX_DATABASE_BYTES: Final = 64 * 1024 * 1024


class InvalidSystematicRegimeFileError(ValueError):
    pass


def open_private_file(parent: int, name: str, *, create: bool, write: bool) -> int:
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= os.O_RDWR if write else os.O_RDONLY
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        descriptor = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
    try:
        require_private_file(descriptor)
    except (OSError, ValueError):
        os.close(descriptor)
        raise
    return descriptor


def require_private_file(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidSystematicRegimeFileError


def load_sqlite_database(connection: sqlite3.Connection, descriptor: int) -> bytes:
    size = os.fstat(descriptor).st_size
    if size > _MAX_DATABASE_BYTES:
        raise InvalidSystematicRegimeFileError
    payload = os.pread(descriptor, size, 0)
    if len(payload) != size:
        raise InvalidSystematicRegimeFileError
    if payload:
        connection.deserialize(payload)
    return payload


def replace_sqlite_database(parent: int, name: str, payload: bytes) -> None:
    temporary = f".{name}.{secrets.token_hex(16)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_CLOEXEC | os.O_NOFOLLOW | os.O_RDWR | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=parent,
    )
    renamed = False
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        require_private_file(descriptor)
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        renamed = True
        os.fsync(parent)
        replacement = open_private_file(parent, name, create=False, write=False)
        os.close(replacement)
    finally:
        os.close(descriptor)
        if not renamed:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)


__all__ = (
    "InvalidSystematicRegimeFileError",
    "load_sqlite_database",
    "open_private_file",
    "replace_sqlite_database",
    "require_private_file",
)
