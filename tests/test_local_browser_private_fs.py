from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import trading_agent.local_browser_private_fs as private_fs


def _payload() -> bytes:
    return b"9222\n/devtools/browser/token\n"


@pytest.mark.parametrize("mode,owner", ((0o755, None), (0o700, -1)))
def test_private_directory_rejects_nonprivate_identity(tmp_path: Path, mode: int, owner: int | None) -> None:
    # Given: an existing root with a weak mode or mismatched owner identity.
    root = tmp_path / "profile"
    root.mkdir(mode=mode)
    expected_owner = os.getuid() if owner is None else owner
    # When: descriptor-pinned setup validates the root.
    with (
        pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised,
        private_fs.open_private_browser_directory(root, expected_owner),
    ):
        pass
    # Then: it fails closed before a consumer can access it.
    assert raised.value.reason == "local_browser_private_directory_invalid"


def test_private_file_rejects_post_open_growth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a small private file whose fstat grows after the no-follow open.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        (root / "DevToolsActivePort").write_bytes(_payload())
        (root / "DevToolsActivePort").chmod(0o600)
        original = os.fstat

        def oversized(descriptor: int) -> os.stat_result:
            metadata = original(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                return metadata
            return os.stat_result(
                (metadata.st_mode, metadata.st_ino, metadata.st_dev, metadata.st_nlink, metadata.st_uid,
                 metadata.st_gid, 257, metadata.st_atime, metadata.st_mtime, metadata.st_ctime)
            )

        monkeypatch.setattr(private_fs.os, "fstat", oversized)
        # When: the descriptor-relative read crosses its post-open boundary.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            _ = private_fs.read_private_browser_file(directory, "DevToolsActivePort", os.getuid(), 256)
        # Then: the grown descriptor is rejected.
        assert raised.value.reason == "local_browser_private_file_invalid"


def test_private_file_unlink_preserves_replacement(tmp_path: Path) -> None:
    # Given: a pinned private directory and an observed endpoint inode.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        path.write_bytes(_payload())
        path.chmod(0o600)
        observed = private_fs.read_private_browser_file(directory, path.name, os.getuid(), 256)
        assert observed is not None
        replacement = b"9223\n/devtools/browser/replacement\n"
        path.unlink()
        path.write_bytes(replacement)
        path.chmod(0o600)
        # When: cleanup attempts to remove the observed inode by name.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: replacement detection leaves the new entry intact.
        assert raised.value.reason == "local_browser_private_file_replaced" and path.read_bytes() == replacement


def test_private_file_restores_replacement_interposed_before_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a replacement that arrives after validation but before the atomic move.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        path.write_bytes(_payload())
        path.chmod(0o600)
        observed = private_fs.read_private_browser_file(directory, path.name, os.getuid(), 256)
        assert observed is not None
        replacement = b"9223\n/devtools/browser/interposed\n"
        original = private_fs.os.rename

        def interpose(source: str, destination: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
            path.unlink()
            path.write_bytes(replacement)
            path.chmod(0o600)
            original(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        monkeypatch.setattr(private_fs.os, "rename", interpose)
        # When: guarded cleanup encounters the interposed replacement.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: it restores the replacement without deleting either inode.
        assert raised.value.reason == "local_browser_private_file_replaced" and path.read_bytes() == replacement


def test_private_file_preserves_new_entry_after_quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a new endpoint that appears only after the expected inode is quarantined.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        path.write_bytes(_payload())
        path.chmod(0o600)
        observed = private_fs.read_private_browser_file(directory, path.name, os.getuid(), 256)
        assert observed is not None
        replacement = b"9223\n/devtools/browser/after-quarantine\n"
        original = private_fs.os.rename

        def replace_after(source: str, destination: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
            original(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            path.write_bytes(replacement)
            path.chmod(0o600)

        monkeypatch.setattr(private_fs.os, "rename", replace_after)
        # When: cleanup removes its quarantined inode.
        private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: the post-quarantine entry remains at the public endpoint name.
        assert path.read_bytes() == replacement


def test_private_file_rejects_preexisting_quarantine_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an attacker-visible collision with the freshly generated quarantine name.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        path.write_bytes(_payload())
        path.chmod(0o600)
        observed = private_fs.read_private_browser_file(directory, path.name, os.getuid(), 256)
        assert observed is not None
        token = "collision"
        (root / f"{private_fs._QUARANTINE_PREFIX}{token}").mkdir(mode=0o700)
        monkeypatch.setattr(private_fs.secrets, "token_hex", lambda _size: token)
        # When: guarded cleanup reserves its quarantine directory.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: it fails closed and leaves the observed endpoint unchanged.
        assert raised.value.reason == "local_browser_private_file_invalid" and path.read_bytes() == _payload()


def test_private_directory_closes_descriptor_for_its_own_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a descriptor-pinned open whose private-directory validator raises this module's error.
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    original_open, original_close = os.open, os.close
    opened: list[int] = []
    closed: list[int] = []

    def open_parent(_path: Path, *, create: bool) -> int:
        descriptor = original_open(root, os.O_RDONLY | os.O_DIRECTORY)
        opened.append(descriptor)
        return descriptor

    def close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    def reject(_descriptor: int) -> None:
        raise private_fs.InvalidLocalBrowserPrivateFsError(reason="local_browser_private_directory_invalid")

    monkeypatch.setattr(private_fs, "open_private_parent", open_parent)
    monkeypatch.setattr(private_fs, "require_private_directory", reject)
    monkeypatch.setattr(private_fs.os, "close", close)
    # When: context entry fails during its own validation.
    with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError), private_fs.open_private_browser_directory(
        root, os.getuid()
    ):
        pass
    # Then: the opened descriptor was closed despite the module-owned exception.
    assert closed == opened


def test_private_file_rejects_symlink(tmp_path: Path) -> None:
    # Given: a private directory whose endpoint name is a symlink.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        target = root / "target"
        target.write_bytes(_payload())
        target.chmod(0o600)
        (root / "DevToolsActivePort").symlink_to(target)
        # When: the descriptor-relative reader opens the final name without following it.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            _ = private_fs.read_private_browser_file(directory, "DevToolsActivePort", os.getuid(), 256)
        # Then: the symlinked endpoint is rejected.
        assert raised.value.reason == "local_browser_private_file_invalid"
