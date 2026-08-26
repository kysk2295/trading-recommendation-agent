from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from trading_agent.local_browser_private_fs import (
    InvalidLocalBrowserPrivateFsError,
    PrivateBrowserDirectory,
    open_private_browser_directory,
)
from trading_agent.local_browser_receipt_sqlite import (
    InvalidPrivateBrowserReceiptDatabaseError,
    PrivateBrowserReceiptDatabase,
)
from trading_agent.private_directory_identity import InvalidPrivateDirectoryIdentityError, require_open_directory_path

LOCAL_BROWSER_RECEIPT_LEASE_NAME = ".local-browser-receipts.execution.lease"


class InvalidLocalBrowserReceiptLeaseError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, *, reason: str = "local_browser_receipt_lease_invalid") -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class LocalBrowserReceiptLease:
    __slots__ = ("descriptor", "device", "inode", "owner_id")

    def __init__(self, descriptor: int, device: int, inode: int, owner_id: int) -> None:
        self.descriptor = descriptor
        self.device = device
        self.inode = inode
        self.owner_id = owner_id

    def require_current(self, directory: PrivateBrowserDirectory) -> None:
        _require_current_entry(directory, self.descriptor, self.device, self.inode, self.owner_id)

    def release(self) -> None:
        descriptor, self.descriptor = self.descriptor, -1
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextmanager
def hold_local_browser_receipt_initialization_lease(path: Path, owner_id: int) -> Iterator[None]:
    try:
        with open_private_browser_directory(path.parent, owner_id) as directory:
            lease = acquire_local_browser_receipt_lease(directory, owner_id)
            try:
                yield
            finally:
                try:
                    lease.require_current(directory)
                finally:
                    lease.release()
    except InvalidLocalBrowserPrivateFsError:
        raise InvalidLocalBrowserReceiptLeaseError() from None


@contextmanager
def hold_local_browser_receipt_execution_lease(
    database: PrivateBrowserReceiptDatabase,
) -> Iterator[None]:
    try:
        with open_private_browser_directory(database.path.parent, database.owner_id) as directory:
            lease = acquire_local_browser_receipt_lease(directory, database.owner_id)
            try:
                database.require_current()
                yield
            finally:
                try:
                    try:
                        database.require_current()
                    finally:
                        lease.require_current(directory)
                finally:
                    lease.release()
    except (InvalidLocalBrowserPrivateFsError, InvalidPrivateBrowserReceiptDatabaseError):
        raise InvalidLocalBrowserReceiptLeaseError() from None


def acquire_local_browser_receipt_lease(directory: PrivateBrowserDirectory, owner_id: int) -> LocalBrowserReceiptLease:
    descriptor: int | None = None
    lease: LocalBrowserReceiptLease | None = None
    try:
        _require_private_directory(directory, owner_id)
        descriptor = _open_lease(directory)
        metadata = os.fstat(descriptor)
        if not _is_private_lease(metadata, owner_id):
            raise InvalidLocalBrowserReceiptLeaseError()
        _require_current_entry(directory, descriptor, metadata.st_dev, metadata.st_ino, owner_id)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _require_current_entry(directory, descriptor, metadata.st_dev, metadata.st_ino, owner_id)
        lease = LocalBrowserReceiptLease(descriptor, metadata.st_dev, metadata.st_ino, owner_id)
        return lease
    except (InvalidLocalBrowserReceiptLeaseError, OSError, TypeError, ValueError):
        raise InvalidLocalBrowserReceiptLeaseError() from None
    finally:
        if lease is None:
            _close_descriptor(descriptor)


def _open_lease(directory: PrivateBrowserDirectory) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        return os.open(LOCAL_BROWSER_RECEIPT_LEASE_NAME, flags, 0o600, dir_fd=directory.descriptor)
    except FileExistsError:
        return os.open(LOCAL_BROWSER_RECEIPT_LEASE_NAME, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory.descriptor)


def _require_private_directory(directory: PrivateBrowserDirectory, owner_id: int) -> None:
    try:
        metadata = os.fstat(directory.descriptor)
        require_open_directory_path(directory.path, directory.descriptor)
    except (InvalidPrivateDirectoryIdentityError, OSError, TypeError, ValueError):
        raise InvalidLocalBrowserReceiptLeaseError() from None
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_id or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise InvalidLocalBrowserReceiptLeaseError()


def _require_current_entry(
    directory: PrivateBrowserDirectory, descriptor: int, device: int, inode: int, owner_id: int
) -> None:
    _require_private_directory(directory, owner_id)
    try:
        checked = os.fstat(descriptor)
        current = os.stat(
            LOCAL_BROWSER_RECEIPT_LEASE_NAME,
            dir_fd=directory.descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise InvalidLocalBrowserReceiptLeaseError() from None
    if not _is_private_lease(checked, owner_id) or not _is_private_lease(current, owner_id):
        raise InvalidLocalBrowserReceiptLeaseError()
    if (current.st_dev, current.st_ino) != (device, inode):
        raise InvalidLocalBrowserReceiptLeaseError()


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
