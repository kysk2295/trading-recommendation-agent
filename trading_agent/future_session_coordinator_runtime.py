from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from pathlib import Path

from trading_agent.future_session_coordinator_inspectors import (
    CoordinatorInspectionError,
)
from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorBlockReason,
)


def acquire_coordinator_claim(path: Path) -> int:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        os.close(descriptor)
        raise OSError("invalid coordinator lock")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise CoordinatorInspectionError(FutureSessionCoordinatorBlockReason.CONCURRENT_COORDINATOR) from None
    return descriptor


def release_coordinator_claim(path: Path, descriptor: int) -> None:
    del path
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def launchctl_label_is_loaded(label: str) -> bool:
    completed = subprocess.run(
        ("/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"),
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


__all__ = (
    "acquire_coordinator_claim",
    "launchctl_label_is_loaded",
    "release_coordinator_claim",
)
