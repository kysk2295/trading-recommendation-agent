from __future__ import annotations

import os
import stat
from pathlib import Path


def runtime_supervisor_store_is_private(path: Path) -> bool:
    metadata = path.lstat()
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
    )


__all__ = ("runtime_supervisor_store_is_private",)
