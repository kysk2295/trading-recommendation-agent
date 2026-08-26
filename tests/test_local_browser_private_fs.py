from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

import trading_agent.local_browser_private_fs as private_fs


def _payload() -> bytes:
    return b"9222\n/devtools/browser/token\n"


def _observed_endpoint(directory: private_fs.PrivateBrowserDirectory, path: Path) -> private_fs.PrivateBrowserFile:
    path.write_bytes(_payload())
    path.chmod(0o600)
    observed = private_fs.read_private_browser_file(directory, path.name, os.getuid(), 256)
    assert observed is not None
    return observed


def _interpose_initial_move(
    monkeypatch: pytest.MonkeyPatch, before: Callable[[], None] | None = None, after: Callable[[], None] | None = None
) -> None:
    original = private_fs.rename_entry_exclusively
    first = True

    def move(source_directory: int, source: str, destination_directory: int, destination: str) -> None:
        nonlocal first
        if first:
            first = False
            if before is not None:
                before()
            original(source_directory, source, destination_directory, destination)
            if after is not None:
                after()
            return
        original(source_directory, source, destination_directory, destination)

    monkeypatch.setattr(private_fs, "rename_entry_exclusively", move)


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
                (
                    metadata.st_mode,
                    metadata.st_ino,
                    metadata.st_dev,
                    metadata.st_nlink,
                    metadata.st_uid,
                    metadata.st_gid,
                    257,
                    metadata.st_atime,
                    metadata.st_mtime,
                    metadata.st_ctime,
                )
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
        observed = _observed_endpoint(directory, path)
        replacement = b"9223\n/devtools/browser/interposed\n"

        def replace() -> None:
            path.unlink()
            path.write_bytes(replacement)
            path.chmod(0o600)

        _interpose_initial_move(monkeypatch, replace)
        # When: guarded cleanup encounters the interposed replacement.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: it restores the replacement without deleting either inode.
        assert raised.value.reason == "local_browser_private_file_replaced" and path.read_bytes() == replacement


def test_private_file_restores_directory_interposed_before_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a directory that replaces the validated regular endpoint before quarantine.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        observed = _observed_endpoint(directory, path)

        def replace() -> None:
            path.unlink()
            path.mkdir(mode=0o700)

        _interpose_initial_move(monkeypatch, replace)
        # When: cleanup finds the directory in its private quarantine.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: the directory is atomically restored to the public endpoint name.
        assert raised.value.reason == "local_browser_private_file_replaced" and path.is_dir()


def test_private_file_restores_symlink_interposed_before_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a symlink that replaces the validated regular endpoint before quarantine.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path, target = root / "DevToolsActivePort", root / "target"
        observed = _observed_endpoint(directory, path)
        target.write_bytes(b"target")

        def replace() -> None:
            path.unlink()
            path.symlink_to(target)

        _interpose_initial_move(monkeypatch, replace)
        # When: cleanup finds the symlink in its private quarantine.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: the symlink itself, rather than its target, is atomically restored.
        assert raised.value.reason == "local_browser_private_file_replaced" and path.is_symlink()


def test_private_file_preserves_new_public_entry_before_directory_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a directory enters quarantine and a new public entry arrives before restoration.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        observed = _observed_endpoint(directory, path)
        replacement = b"9223\n/devtools/browser/new-public\n"

        def replace() -> None:
            path.unlink()
            path.mkdir(mode=0o700)

        def publish() -> None:
            path.write_bytes(replacement)
            path.chmod(0o600)

        _interpose_initial_move(monkeypatch, replace, publish)
        # When: no-replace restoration finds a new public entry.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: it preserves the public replacement and leaves the quarantined directory recoverable.
        quarantine = next(root.glob(f"{private_fs._QUARANTINE_PREFIX}*"))
        assert raised.value.reason == "local_browser_private_file_replaced"
        assert path.read_bytes() == replacement and (quarantine / path.name).is_dir()


def test_private_file_preserves_quarantine_when_exclusive_restore_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a non-regular replacement in quarantine and a new public endpoint.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        observed = _observed_endpoint(directory, path)
        replacement = b"9223\n/devtools/browser/unavailable\n"
        original = private_fs.rename_entry_exclusively
        first = True

        def unavailable(source_directory: int, source: str, destination_directory: int, destination: str) -> None:
            nonlocal first
            if first:
                first = False
                path.unlink()
                path.mkdir(mode=0o700)
                original(source_directory, source, destination_directory, destination)
                path.write_bytes(replacement)
                path.chmod(0o600)
                return
            raise private_fs.AtomicRenameUnavailableError()

        monkeypatch.setattr(private_fs, "rename_entry_exclusively", unavailable)
        # When: restoration cannot access the Darwin no-replace primitive.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: it fails closed without overwriting either retained entry.
        quarantine = next(root.glob(f"{private_fs._QUARANTINE_PREFIX}*"))
        assert raised.value.reason == "local_browser_private_file_invalid"
        assert path.read_bytes() == replacement and (quarantine / path.name).is_dir()


def test_private_file_preserves_new_entry_after_quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a new endpoint that appears only after the expected inode is quarantined.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        observed = _observed_endpoint(directory, path)
        replacement = b"9223\n/devtools/browser/after-quarantine\n"

        def publish() -> None:
            path.write_bytes(replacement)
            path.chmod(0o600)

        _interpose_initial_move(monkeypatch, after=publish)
        # When: cleanup removes its quarantined inode.
        private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: the post-quarantine entry remains at the public endpoint name.
        assert path.read_bytes() == replacement


def test_private_file_rejects_preexisting_quarantine_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an attacker-visible collision with the freshly generated quarantine name.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        observed = _observed_endpoint(directory, path)
        token = "collision"
        (root / f"{private_fs._QUARANTINE_PREFIX}{token}").mkdir(mode=0o700)
        monkeypatch.setattr(private_fs.secrets, "token_hex", lambda _size: token)
        # When: guarded cleanup reserves its quarantine directory.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: it fails closed and leaves the observed endpoint unchanged.
        assert raised.value.reason == "local_browser_private_file_invalid" and path.read_bytes() == _payload()


def test_private_file_preserves_source_when_quarantine_destination_is_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an injected endpoint appears in the fresh quarantine before the initial move.
    root = tmp_path / "profile"
    with private_fs.open_private_browser_directory(root, os.getuid()) as directory:
        path = root / "DevToolsActivePort"
        observed = _observed_endpoint(directory, path)
        injected = b"9223\n/devtools/browser/quarantine-injected\n"
        original = private_fs.rename_entry_exclusively

        def inject(source_directory: int, source: str, destination_directory: int, destination: str) -> None:
            quarantine = next(root.glob(f"{private_fs._QUARANTINE_PREFIX}*"))
            injected_path = quarantine / destination
            injected_path.write_bytes(injected)
            injected_path.chmod(0o600)
            original(source_directory, source, destination_directory, destination)

        monkeypatch.setattr(private_fs, "rename_entry_exclusively", inject)
        # When: the initial source-to-quarantine move sees the injected destination.
        with pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError) as raised:
            private_fs.unlink_private_browser_file(directory, path.name, observed, os.getuid())
        # Then: exclusive rename preserves both the public source and injected quarantine entry.
        quarantine = next(root.glob(f"{private_fs._QUARANTINE_PREFIX}*"))
        assert raised.value.reason == "local_browser_private_file_replaced"
        assert path.read_bytes() == _payload() and (quarantine / path.name).read_bytes() == injected


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
    with (
        pytest.raises(private_fs.InvalidLocalBrowserPrivateFsError),
        private_fs.open_private_browser_directory(root, os.getuid()),
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
