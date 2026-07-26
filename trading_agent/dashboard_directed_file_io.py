from __future__ import annotations

import os
from pathlib import Path


class InvalidDirectedFileError(OSError):
    pass


def write_bytes_once(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_bytes(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_bounded_bytes(path: Path, max_bytes: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        payload = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(payload) > max_bytes:
        raise InvalidDirectedFileError("directed_file_oversized")
    return payload


__all__ = (
    "InvalidDirectedFileError",
    "append_bytes",
    "read_bounded_bytes",
    "write_bytes_once",
)
