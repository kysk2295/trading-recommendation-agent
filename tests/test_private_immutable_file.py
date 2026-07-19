from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import trading_agent.private_immutable_file as private_file
from trading_agent.private_immutable_file import publish_private_immutable_text


def test_publication_repairs_interrupted_hard_link_cleanup(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "session.json"
    payload = '{"session":"fixture"}\n'
    assert publish_private_immutable_text(path, payload) is True
    staging = tmp_path / ".session.json.interrupted.staging"
    os.link(path, staging)
    assert path.stat().st_nlink == 2

    # When
    created = publish_private_immutable_text(path, payload)

    # Then
    assert created is False
    assert path.stat().st_nlink == 1
    assert not staging.exists()


def test_publication_rejects_staging_path_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    path = tmp_path / "session.json"
    original_link = private_file.os.link

    def replace_before_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int,
        dst_dir_fd: int,
        follow_symlinks: bool,
    ) -> None:
        os.unlink(source, dir_fd=src_dir_fd)
        descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=src_dir_fd)
        try:
            _ = os.write(descriptor, b'{"attacker":true}\n')
        finally:
            os.close(descriptor)
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(private_file.os, "link", replace_before_link)

    # When / Then
    with pytest.raises(private_file.InvalidPrivateImmutableFileError):
        _ = publish_private_immutable_text(path, '{"session":"fixture"}\n')


def test_publication_cleans_orphan_staging_before_retry(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "session.json"
    orphan = tmp_path / ".session.json.interrupted.staging"
    orphan.write_text("partial", encoding="utf-8")
    orphan.chmod(0o600)

    # When
    created = publish_private_immutable_text(path, '{"session":"fixture"}\n')

    # Then
    assert created is True
    assert not orphan.exists()


def test_publication_serializes_threads_in_one_process(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "session.json"
    payload = '{"session":"fixture"}\n'

    # When
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: publish_private_immutable_text(path, payload), range(2)))

    # Then
    assert sorted(results) == [False, True]
    assert path.stat().st_nlink == 1
