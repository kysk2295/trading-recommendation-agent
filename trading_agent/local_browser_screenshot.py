from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from trading_agent.local_browser_atomic_rename import (
    AtomicRenameConflictError,
    AtomicRenameUnavailableError,
    rename_entry_exclusively,
)
from trading_agent.local_browser_private_fs import (
    InvalidLocalBrowserPrivateFsError,
    PrivateBrowserDirectory,
    PrivateBrowserFile,
    open_private_browser_directory,
    unlink_private_browser_file,
)
from trading_agent.private_directory_identity import require_open_directory_path, require_same_file


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK: exceptions need writable traceback state
class InvalidLocalBrowserScreenshotError(RuntimeError):
    """Carry a screenshot failure while permitting traceback attachment."""

    reason: str = "browser_navigation_blocked"

    def __str__(self) -> str:
        return self.reason


def publish_private_screenshot(root: Path, payload: bytes, digest: str, owner_id: int) -> Path:
    if (
        not payload
        or len(payload) > 8 * 1024 * 1024
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or hashlib.sha256(payload).hexdigest() != digest
    ):
        raise InvalidLocalBrowserScreenshotError()
    final_name = f"{digest}-{secrets.token_hex(8)}.png"
    staging_name = f".screenshot-{secrets.token_hex(16)}.tmp"
    try:
        with open_private_browser_directory(root, owner_id) as directory:
            descriptor = os.open(
                staging_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory.descriptor,
            )
            expected: PrivateBrowserFile | None = None
            published = False
            try:
                initial = os.fstat(descriptor)
                expected = PrivateBrowserFile(b"", initial.st_dev, initial.st_ino)
                if not _private_file(initial, owner_id, 0):
                    raise InvalidLocalBrowserScreenshotError()
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                written = 0
                while written < len(view):
                    count = os.write(descriptor, view[written:])
                    if count <= 0:
                        raise InvalidLocalBrowserScreenshotError()
                    written += count
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
                if not _private_file(metadata, owner_id, len(payload)):
                    raise InvalidLocalBrowserScreenshotError()
                _require_name_identity(directory, staging_name, descriptor)
                require_open_directory_path(root, directory.descriptor)
                rename_entry_exclusively(
                    directory.descriptor,
                    staging_name,
                    directory.descriptor,
                    final_name,
                )
                published = True
                os.fsync(directory.descriptor)
                final_descriptor = os.open(
                    final_name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory.descriptor,
                )
                try:
                    require_same_file(descriptor, final_descriptor)
                    if not _private_file(os.fstat(final_descriptor), owner_id, len(payload)):
                        raise InvalidLocalBrowserScreenshotError()
                finally:
                    os.close(final_descriptor)
                require_open_directory_path(root, directory.descriptor)
            except (
                AtomicRenameConflictError,
                AtomicRenameUnavailableError,
                InvalidLocalBrowserPrivateFsError,
                InvalidLocalBrowserScreenshotError,
                OSError,
                TypeError,
                ValueError,
            ):
                if expected is not None:
                    _cleanup_exact(
                        directory,
                        final_name if published else staging_name,
                        expected,
                        owner_id,
                    )
                raise
            finally:
                os.close(descriptor)
    except InvalidLocalBrowserScreenshotError:
        raise
    except (
        AtomicRenameConflictError,
        AtomicRenameUnavailableError,
        InvalidLocalBrowserPrivateFsError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise InvalidLocalBrowserScreenshotError() from None
    return root / final_name


def _require_name_identity(directory: PrivateBrowserDirectory, name: str, descriptor: int) -> None:
    named = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory.descriptor)
    try:
        require_same_file(descriptor, named)
    finally:
        os.close(named)


def _cleanup_exact(
    directory: PrivateBrowserDirectory,
    name: str,
    expected: PrivateBrowserFile,
    owner_id: int,
) -> None:
    try:
        unlink_private_browser_file(directory, name, expected, owner_id)
    except InvalidLocalBrowserPrivateFsError:
        return


def _private_file(metadata: os.stat_result, owner_id: int, size: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == owner_id
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and metadata.st_size == size
    )
