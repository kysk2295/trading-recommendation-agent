from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent.local_browser_profile_lease as lease_fs
from trading_agent.local_browser_private_fs import open_private_browser_directory


def test_profile_lease_excludes_second_real_flock_and_releases_descriptor(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    with open_private_browser_directory(root, os.getuid()) as directory:
        first = lease_fs.acquire_local_browser_profile_lease(directory, os.getuid())
        with pytest.raises(lease_fs.LocalBrowserProfileLeaseBusyError):
            _ = lease_fs.acquire_local_browser_profile_lease(directory, os.getuid())
        descriptor = first.descriptor
        first.release()
        with pytest.raises(OSError):
            os.fstat(descriptor)
        second = lease_fs.acquire_local_browser_profile_lease(directory, os.getuid())
        second.release()


@pytest.mark.parametrize("kind", ("mode", "hardlink", "symlink"))
def test_profile_lease_rejects_unsafe_existing_entry(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "profile"
    with open_private_browser_directory(root, os.getuid()) as directory:
        path = root / lease_fs.LOCAL_BROWSER_PROFILE_LEASE_NAME
        if kind == "symlink":
            target = root / "target"
            target.write_bytes(b"target")
            path.symlink_to(target)
        else:
            path.write_bytes(b"")
            path.chmod(0o644 if kind == "mode" else 0o600)
            if kind == "hardlink":
                os.link(path, root / "second-link")
        with pytest.raises(lease_fs.InvalidLocalBrowserProfileLeaseError) as raised:
            _ = lease_fs.acquire_local_browser_profile_lease(directory, os.getuid())
        assert raised.value.reason == "local_browser_profile_lease_invalid"


def test_profile_lease_rejects_name_replacement_after_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "profile"
    with open_private_browser_directory(root, os.getuid()) as directory:
        path = root / lease_fs.LOCAL_BROWSER_PROFILE_LEASE_NAME
        original = lease_fs.fcntl.flock

        def replace(descriptor: int, operation: int) -> None:
            original(descriptor, operation)
            if operation & lease_fs.fcntl.LOCK_EX:
                path.unlink()
                path.write_bytes(b"")
                path.chmod(0o600)

        monkeypatch.setattr(lease_fs.fcntl, "flock", replace)
        with pytest.raises(lease_fs.InvalidLocalBrowserProfileLeaseError) as raised:
            _ = lease_fs.acquire_local_browser_profile_lease(directory, os.getuid())
        assert raised.value.reason == "local_browser_profile_lease_invalid"


def test_profile_lease_rechecks_private_metadata_before_launch(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    with open_private_browser_directory(root, os.getuid()) as directory:
        lease = lease_fs.acquire_local_browser_profile_lease(directory, os.getuid())
        (root / lease_fs.LOCAL_BROWSER_PROFILE_LEASE_NAME).chmod(0o644)
        with pytest.raises(lease_fs.InvalidLocalBrowserProfileLeaseError):
            lease.require_current(directory)
        lease.release()
