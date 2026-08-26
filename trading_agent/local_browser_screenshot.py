from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from trading_agent.local_browser_private_fs import (
    InvalidLocalBrowserPrivateFsError,
    open_private_browser_directory,
)
from trading_agent.private_directory_identity import require_open_directory_path, require_same_file


@dataclass(slots=True)
class InvalidLocalBrowserScreenshotError(RuntimeError):
    reason: str = "browser_navigation_blocked"

    def __str__(self) -> str:
        return self.reason


def publish_private_screenshot(root: Path, payload: bytes, digest: str, owner_id: int) -> Path:
    if not payload or len(payload) > 8 * 1024 * 1024:
        raise InvalidLocalBrowserScreenshotError()
    name = f"{digest}-{secrets.token_hex(8)}.png"
    try:
        with open_private_browser_directory(root, owner_id) as directory:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory.descriptor,
            )
            try:
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                written = 0
                while written < len(view):
                    written += os.write(descriptor, view[written:])
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
                if not _private_file(metadata, owner_id, len(payload)):
                    raise InvalidLocalBrowserScreenshotError()
                published = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory.descriptor)
                try:
                    require_same_file(descriptor, published)
                finally:
                    os.close(published)
                require_open_directory_path(root, directory.descriptor)
            finally:
                os.close(descriptor)
    except InvalidLocalBrowserScreenshotError:
        raise
    except (InvalidLocalBrowserPrivateFsError, OSError, TypeError, ValueError):
        raise InvalidLocalBrowserScreenshotError() from None
    return root / name


def _private_file(metadata: os.stat_result, owner_id: int, size: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == owner_id
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and metadata.st_size == size
    )
