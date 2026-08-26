from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

import trading_agent.local_browser_private_fs as private_fs


def payload() -> bytes:
    return b"9222\n/devtools/browser/token\n"


def observed_endpoint(directory: private_fs.PrivateBrowserDirectory, path: Path) -> private_fs.PrivateBrowserFile:
    path.write_bytes(payload())
    path.chmod(0o600)
    observed = private_fs.read_private_browser_file(directory, path.name, os.getuid(), 256)
    assert observed is not None
    return observed


def interpose_initial_move(
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
