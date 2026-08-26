from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent.local_browser_receipt_lease as lease_fs
from trading_agent.local_browser_private_fs import open_private_browser_directory


def test_receipt_lease_releases_descriptor_and_can_be_reacquired(tmp_path: Path) -> None:
    root = tmp_path / "state"
    with open_private_browser_directory(root, os.getuid()) as directory:
        first = lease_fs.acquire_local_browser_receipt_lease(directory, os.getuid())
        descriptor = first.descriptor
        first.release()
        with pytest.raises(OSError):
            os.fstat(descriptor)
        second = lease_fs.acquire_local_browser_receipt_lease(directory, os.getuid())
        second.release()


@pytest.mark.parametrize("kind", ("mode", "hardlink", "symlink"))
def test_receipt_lease_rejects_unsafe_existing_entry(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "state"
    with open_private_browser_directory(root, os.getuid()) as directory:
        path = root / lease_fs.LOCAL_BROWSER_RECEIPT_LEASE_NAME
        if kind == "symlink":
            target = root / "target"
            target.write_bytes(b"target")
            path.symlink_to(target)
        else:
            path.write_bytes(b"")
            path.chmod(0o644 if kind == "mode" else 0o600)
            if kind == "hardlink":
                os.link(path, root / "second-link")
        with pytest.raises(lease_fs.InvalidLocalBrowserReceiptLeaseError):
            _ = lease_fs.acquire_local_browser_receipt_lease(directory, os.getuid())


def test_receipt_lease_rejects_name_replacement_after_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "state"
    with open_private_browser_directory(root, os.getuid()) as directory:
        path = root / lease_fs.LOCAL_BROWSER_RECEIPT_LEASE_NAME
        original = lease_fs.fcntl.flock
        locked_descriptors: list[int] = []

        def replace(descriptor: int, operation: int) -> None:
            original(descriptor, operation)
            if operation & lease_fs.fcntl.LOCK_EX:
                locked_descriptors.append(descriptor)
                path.unlink()
                path.write_bytes(b"")
                path.chmod(0o600)

        monkeypatch.setattr(lease_fs.fcntl, "flock", replace)
        with pytest.raises(lease_fs.InvalidLocalBrowserReceiptLeaseError):
            _ = lease_fs.acquire_local_browser_receipt_lease(directory, os.getuid())
        assert len(locked_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(locked_descriptors[0])


def test_receipt_lease_rechecks_name_and_closes_descriptor_before_release(tmp_path: Path) -> None:
    root = tmp_path / "state"
    with open_private_browser_directory(root, os.getuid()) as directory:
        lease = lease_fs.acquire_local_browser_receipt_lease(directory, os.getuid())
        descriptor = lease.descriptor
        path = root / lease_fs.LOCAL_BROWSER_RECEIPT_LEASE_NAME
        path.unlink()
        path.write_bytes(b"")
        path.chmod(0o600)
        with pytest.raises(lease_fs.InvalidLocalBrowserReceiptLeaseError):
            lease.require_current(directory)
        lease.release()
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_receipt_lease_closes_descriptor_when_lock_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "state"
    with open_private_browser_directory(root, os.getuid()) as directory:
        original = lease_fs.fcntl.flock
        interrupted_descriptors: list[int] = []

        def interrupt(descriptor: int, operation: int) -> None:
            if operation & lease_fs.fcntl.LOCK_EX:
                interrupted_descriptors.append(descriptor)
                raise KeyboardInterrupt
            original(descriptor, operation)

        monkeypatch.setattr(lease_fs.fcntl, "flock", interrupt)
        with pytest.raises(KeyboardInterrupt):
            _ = lease_fs.acquire_local_browser_receipt_lease(directory, os.getuid())
        assert len(interrupted_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(interrupted_descriptors[0])


@pytest.mark.parametrize("kind", ("hardlink", "symlink"))
def test_receipt_initialization_lease_rejects_unsafe_existing_entry(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    lease_path = root / lease_fs.LOCAL_BROWSER_RECEIPT_LEASE_NAME
    target = root / "target"
    target.write_bytes(b"")
    target.chmod(0o600)
    if kind == "hardlink":
        os.link(target, lease_path)
    else:
        lease_path.symlink_to(target)
    with (
        pytest.raises(lease_fs.InvalidLocalBrowserReceiptLeaseError),
        lease_fs.hold_local_browser_receipt_initialization_lease(root / "receipts.sqlite3", os.getuid()),
    ):
        pass


@pytest.mark.parametrize("interruption", (RuntimeError, KeyboardInterrupt, SystemExit))
def test_receipt_initialization_lease_closes_descriptor_on_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException]
) -> None:
    root = tmp_path / "state"
    path = root / "receipts.sqlite3"
    original = lease_fs.acquire_local_browser_receipt_lease
    descriptors: list[int] = []

    def capture(directory: lease_fs.PrivateBrowserDirectory, owner_id: int) -> lease_fs.LocalBrowserReceiptLease:
        lease = original(directory, owner_id)
        descriptors.append(lease.descriptor)
        return lease

    monkeypatch.setattr(lease_fs, "acquire_local_browser_receipt_lease", capture)
    with (
        pytest.raises(interruption),
        lease_fs.hold_local_browser_receipt_initialization_lease(path, os.getuid()),
    ):
        raise interruption()
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize("replacement", ("name", "parent"))
def test_receipt_initialization_lease_detects_replacement_and_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    root = tmp_path / "state"
    path = root / "receipts.sqlite3"
    moved = tmp_path / "moved"
    original = lease_fs.acquire_local_browser_receipt_lease
    descriptors: list[int] = []

    def capture(directory: lease_fs.PrivateBrowserDirectory, owner_id: int) -> lease_fs.LocalBrowserReceiptLease:
        lease = original(directory, owner_id)
        descriptors.append(lease.descriptor)
        return lease

    monkeypatch.setattr(lease_fs, "acquire_local_browser_receipt_lease", capture)
    with (
        pytest.raises(lease_fs.InvalidLocalBrowserReceiptLeaseError),
        lease_fs.hold_local_browser_receipt_initialization_lease(path, os.getuid()),
    ):
        lease_path = root / lease_fs.LOCAL_BROWSER_RECEIPT_LEASE_NAME
        if replacement == "name":
            lease_path.unlink()
            lease_path.write_bytes(b"")
            lease_path.chmod(0o600)
        else:
            root.rename(moved)
            root.mkdir(mode=0o700)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
