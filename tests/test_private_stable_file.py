from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent.private_stable_file as stable_file


def test_stable_reader_returns_exact_private_regular_file_bytes(tmp_path: Path) -> None:
    # Given: one private, single-link, current-user regular file.
    path = tmp_path / "authority.bin"
    path.write_bytes(b"trusted-bytes")
    path.chmod(0o600)

    # When/Then: the descriptor-stable reader returns its exact bytes.
    assert stable_file.read_private_stable_bytes(path, max_bytes=64) == b"trusted-bytes"


@pytest.mark.parametrize("kind", ("public", "symlink", "hardlink"))
def test_stable_reader_rejects_unsafe_file_identity(tmp_path: Path, kind: str) -> None:
    # Given: a named authority with unsafe mode, symlink identity, or link count.
    path = tmp_path / "authority.bin"
    target = tmp_path / "target.bin"
    target.write_bytes(b"trusted-bytes")
    target.chmod(0o600)
    if kind == "public":
        target.rename(path)
        path.chmod(0o640)
    elif kind == "symlink":
        path.symlink_to(target)
    else:
        os.link(target, path)

    # When/Then: no bytes cross the trust boundary.
    with pytest.raises(stable_file.InvalidPrivateStableFileError):
        _ = stable_file.read_private_stable_bytes(path, max_bytes=64)


def test_stable_reader_rejects_named_path_swap_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an attacker swaps the named file after its descriptor is opened.
    path = tmp_path / "authority.bin"
    held = tmp_path / "held.bin"
    replacement = tmp_path / "replacement.bin"
    path.write_bytes(b"trusted-bytes")
    replacement.write_bytes(b"hostile-bytes")
    path.chmod(0o600)
    replacement.chmod(0o600)
    real_read = stable_file.os.read
    swapped = False

    def swap_after_open(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        chunk = real_read(descriptor, count)
        if not swapped:
            swapped = True
            path.rename(held)
            replacement.rename(path)
        return chunk

    monkeypatch.setattr(stable_file.os, "read", swap_after_open)

    # When/Then: the opened-versus-named inode mismatch fails closed.
    with pytest.raises(stable_file.InvalidPrivateStableFileError):
        _ = stable_file.read_private_stable_bytes(path, max_bytes=64)


def test_stable_reader_rejects_open_file_content_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: file content changes after the opened descriptor yields its first bytes.
    path = tmp_path / "authority.bin"
    path.write_bytes(b"trusted-bytes")
    path.chmod(0o600)
    real_read = stable_file.os.read
    mutated = False

    def mutate_after_read(descriptor: int, count: int) -> bytes:
        nonlocal mutated
        chunk = real_read(descriptor, count)
        if not mutated:
            mutated = True
            with path.open("r+b") as stream:
                _ = stream.write(b"hostile-bytes")
                stream.flush()
                os.fsync(stream.fileno())
        return chunk

    monkeypatch.setattr(stable_file.os, "read", mutate_after_read)

    # When/Then: unstable size or timestamps invalidate the read.
    with pytest.raises(stable_file.InvalidPrivateStableFileError):
        _ = stable_file.read_private_stable_bytes(path, max_bytes=64)
