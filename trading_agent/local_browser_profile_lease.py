from __future__ import annotations

import errno
import fcntl
import os
import stat
from dataclasses import dataclass

from trading_agent.local_browser_private_fs import PrivateBrowserDirectory
from trading_agent.private_directory_identity import InvalidPrivateDirectoryIdentityError, require_open_directory_path

LOCAL_BROWSER_PROFILE_LEASE_NAME = ".local-browser-profile.lease"


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK: exceptions need writable traceback state
class InvalidLocalBrowserProfileLeaseError(RuntimeError):
    """Carry a lease failure while permitting traceback attachment."""

    reason: str = "local_browser_profile_lease_invalid"

    def __str__(self) -> str:
        return self.reason


class LocalBrowserProfileLeaseBusyError(RuntimeError):
    pass


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK: release invalidates the descriptor
class LocalBrowserProfileLease:
    """A cooperative exclusive launch claim for the dedicated Chrome profile."""

    descriptor: int
    device: int
    inode: int
    owner_id: int

    def require_current(self, directory: PrivateBrowserDirectory) -> None:
        _require_current_entry(directory, self.descriptor, self.device, self.inode, self.owner_id)

    def release(self) -> None:
        descriptor, self.descriptor = self.descriptor, -1
        if descriptor >= 0:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def acquire_local_browser_profile_lease(directory: PrivateBrowserDirectory, owner_id: int) -> LocalBrowserProfileLease:
    _require_private_directory(directory, owner_id)
    descriptor: int | None = None
    try:
        descriptor = _open_lease(directory)
        metadata = os.fstat(descriptor)
        if not _is_private_lease(metadata, owner_id):
            raise InvalidLocalBrowserProfileLeaseError()
        _require_current_entry(directory, descriptor, metadata.st_dev, metadata.st_ino, owner_id)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _require_current_entry(directory, descriptor, metadata.st_dev, metadata.st_ino, owner_id)
        return LocalBrowserProfileLease(descriptor, metadata.st_dev, metadata.st_ino, owner_id)
    except BlockingIOError as error:
        _close_descriptor(descriptor)
        if error.errno in (errno.EACCES, errno.EAGAIN):
            raise LocalBrowserProfileLeaseBusyError() from None
        raise InvalidLocalBrowserProfileLeaseError() from None
    except (InvalidLocalBrowserProfileLeaseError, OSError, TypeError, ValueError):
        _close_descriptor(descriptor)
        raise InvalidLocalBrowserProfileLeaseError() from None


def _open_lease(directory: PrivateBrowserDirectory) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        return os.open(LOCAL_BROWSER_PROFILE_LEASE_NAME, flags, 0o600, dir_fd=directory.descriptor)
    except FileExistsError:
        return os.open(LOCAL_BROWSER_PROFILE_LEASE_NAME, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory.descriptor)


def _require_private_directory(directory: PrivateBrowserDirectory, owner_id: int) -> None:
    try:
        metadata = os.fstat(directory.descriptor)
        require_open_directory_path(directory.path, directory.descriptor)
    except (InvalidPrivateDirectoryIdentityError, OSError, TypeError, ValueError):
        raise InvalidLocalBrowserProfileLeaseError() from None
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_id or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise InvalidLocalBrowserProfileLeaseError()


def _require_current_entry(
    directory: PrivateBrowserDirectory, descriptor: int, device: int, inode: int, owner_id: int
) -> None:
    _require_private_directory(directory, owner_id)
    try:
        checked = os.fstat(descriptor)
        metadata = os.stat(LOCAL_BROWSER_PROFILE_LEASE_NAME, dir_fd=directory.descriptor, follow_symlinks=False)
    except OSError:
        raise InvalidLocalBrowserProfileLeaseError() from None
    if not _is_private_lease(checked, owner_id) or not _is_private_lease(metadata, owner_id):
        raise InvalidLocalBrowserProfileLeaseError()
    if (metadata.st_dev, metadata.st_ino) != (device, inode):
        raise InvalidLocalBrowserProfileLeaseError()


def _is_private_lease(metadata: os.stat_result, owner_id: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == owner_id
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )


def _close_descriptor(descriptor: int | None) -> None:
    if descriptor is not None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
