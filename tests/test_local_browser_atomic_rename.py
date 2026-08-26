from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import trading_agent.local_browser_atomic_rename as atomic_rename


@pytest.mark.skipif(sys.platform != "darwin", reason="renameatx_np is a Darwin primitive")
def test_rename_entry_exclusively_moves_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "source").mkdir()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        atomic_rename.rename_entry_exclusively(descriptor, "source", descriptor, "destination")
    finally:
        os.close(descriptor)
    assert not (root / "source").exists() and (root / "destination").is_dir()


@pytest.mark.skipif(sys.platform != "darwin", reason="renameatx_np is a Darwin primitive")
def test_rename_entry_exclusively_rejects_existing_destination(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "source").mkdir()
    (root / "destination").mkdir()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(atomic_rename.AtomicRenameConflictError):
            atomic_rename.rename_entry_exclusively(descriptor, "source", descriptor, "destination")
    finally:
        os.close(descriptor)
    assert (root / "source").is_dir() and (root / "destination").is_dir()


def test_rename_entry_exclusively_fails_closed_off_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atomic_rename.sys, "platform", "linux")
    with pytest.raises(atomic_rename.AtomicRenameUnavailableError):
        atomic_rename.rename_entry_exclusively(0, "source", 0, "destination")
