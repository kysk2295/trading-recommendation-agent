from __future__ import annotations

import os
import stat
from pathlib import Path

from trading_agent.future_session_us_materializer_errors import (
    FutureSessionMaterializationError,
)


def read_private_canonical_file(path: Path) -> bytes:
    if not path.is_absolute():
        raise FutureSessionMaterializationError("absolute_input_required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise FutureSessionMaterializationError("invalid_input_file")
        payload = bytearray()
        while chunk := os.read(descriptor, 64 * 1024):
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ):
            raise FutureSessionMaterializationError("input_changed")
        return bytes(payload)
    finally:
        os.close(descriptor)


__all__ = ("read_private_canonical_file",)
