from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path


def external_delivery_service_status(database: Path) -> str:
    try:
        descriptor = os.open(f"{database}.service.lock", os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return "stopped"
    locked = False
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            return "blocked_configuration"
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "external_running"
        locked = True
        return "stopped"
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


__all__ = ("external_delivery_service_status",)
