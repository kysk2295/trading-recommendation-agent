from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from trading_agent.local_browser_atomic_rename import (
    AtomicRenameConflictError,
    AtomicRenameUnavailableError,
    rename_entry_exclusively,
)
from trading_agent.private_directory_identity import (
    InvalidPrivateDirectoryIdentityError,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
)

_QUARANTINE_PREFIX = ".browser-cleanup-"


@dataclass(slots=True)
class InvalidLocalBrowserPrivateFsError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class PrivateBrowserDirectory:
    path: Path
    descriptor: int


@dataclass(frozen=True, slots=True)
class PrivateBrowserFile:
    payload: bytes
    device: int
    inode: int


@contextmanager
def open_private_browser_directory(path: Path, owner_id: int) -> Iterator[PrivateBrowserDirectory]:
    descriptor: int | None = None
    try:
        descriptor = open_private_parent(path, create=True)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_id or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_directory_invalid")
        require_private_directory(descriptor)
        require_open_directory_path(path, descriptor)
    except InvalidLocalBrowserPrivateFsError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except (InvalidPrivateDirectoryIdentityError, OSError, TypeError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_directory_invalid") from None
    try:
        yield PrivateBrowserDirectory(path, descriptor)
    finally:
        os.close(descriptor)


def read_private_browser_file(
    directory: PrivateBrowserDirectory, name: str, owner_id: int, maximum_bytes: int
) -> PrivateBrowserFile | None:
    _require_directory_identity(directory)
    try:
        metadata = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
    if not _private_regular_file(metadata, owner_id) or metadata.st_size > maximum_bytes:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid")
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory.descriptor)
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
    try:
        checked = os.fstat(descriptor)
        if not _private_regular_file(checked, owner_id) or checked.st_size > maximum_bytes:
            raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid")
        payload = os.read(descriptor, maximum_bytes + 1)
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
    finally:
        os.close(descriptor)
    if len(payload) > maximum_bytes:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid")
    _require_directory_identity(directory)
    return PrivateBrowserFile(payload, checked.st_dev, checked.st_ino)


def unlink_private_browser_file(
    directory: PrivateBrowserDirectory, name: str, expected: PrivateBrowserFile, owner_id: int
) -> None:
    _require_directory_identity(directory)
    try:
        metadata = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_replaced") from None
    if not _matches_expected_file(metadata, expected, owner_id):
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_replaced")
    quarantine_name, quarantine_descriptor = _open_quarantine(directory)
    moved, emptied = False, False
    try:
        os.rename(name, name, src_dir_fd=directory.descriptor, dst_dir_fd=quarantine_descriptor)
        moved = True
        quarantined = _quarantined_metadata(quarantine_descriptor, name)
        if not _matches_expected_file(quarantined, expected, owner_id):
            _restore_quarantined_file(directory, quarantine_descriptor, name)
            emptied = True
            raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_replaced")
        os.unlink(name, dir_fd=quarantine_descriptor)
        emptied = True
    except InvalidLocalBrowserPrivateFsError:
        raise
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_replaced") from None
    finally:
        os.close(quarantine_descriptor)
        if not moved or emptied:
            _remove_quarantine(directory, quarantine_name)


def _require_directory_identity(directory: PrivateBrowserDirectory) -> None:
    try:
        require_open_directory_path(directory.path, directory.descriptor)
    except (InvalidPrivateDirectoryIdentityError, OSError, TypeError, ValueError):
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_directory_invalid") from None


def _private_regular_file(metadata: os.stat_result, owner_id: int) -> bool:
    return (
        metadata.st_uid == owner_id
        and stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _matches_expected_file(metadata: os.stat_result, expected: PrivateBrowserFile, owner_id: int) -> bool:
    return _private_regular_file(metadata, owner_id) and (metadata.st_dev, metadata.st_ino) == (
        expected.device,
        expected.inode,
    )


def _open_quarantine(directory: PrivateBrowserDirectory) -> tuple[str, int]:
    name = f"{_QUARANTINE_PREFIX}{secrets.token_hex(16)}"
    try:
        os.mkdir(name, 0o700, dir_fd=directory.descriptor)
    except FileExistsError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory.descriptor)
    except OSError:
        _remove_quarantine(directory, name)
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
    try:
        os.fchmod(descriptor, 0o700)
    except OSError:
        os.close(descriptor)
        _remove_quarantine(directory, name)
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
    return name, descriptor


def _quarantined_metadata(directory_descriptor: int, name: str) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_replaced") from None
    if not stat.S_ISREG(metadata.st_mode):
        return metadata
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_descriptor)
    except OSError:
        try:
            return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except OSError:
            raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_replaced") from None
    try:
        return os.fstat(descriptor)
    finally:
        os.close(descriptor)


def _restore_quarantined_file(directory: PrivateBrowserDirectory, quarantine_descriptor: int, name: str) -> None:
    try:
        rename_entry_exclusively(quarantine_descriptor, name, directory.descriptor, name)
    except AtomicRenameConflictError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_replaced") from None
    except AtomicRenameUnavailableError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None


def _remove_quarantine(directory: PrivateBrowserDirectory, name: str) -> None:
    try:
        os.rmdir(name, dir_fd=directory.descriptor)
    except OSError:
        raise InvalidLocalBrowserPrivateFsError(reason="local_browser_private_file_invalid") from None
