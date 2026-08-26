from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import trading_agent.local_browser_screenshot as screenshot

_PAYLOAD = b"private screenshot payload"
_DIGEST = hashlib.sha256(_PAYLOAD).hexdigest()


def test_partial_write_failure_exactly_cleans_staging_inode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the first write is short and the next write fails.
    root = tmp_path / "screenshots"
    original = screenshot.os.write
    calls = 0

    def short_then_fail(descriptor: int, payload: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(descriptor, payload[:3])
        raise OSError("injected write failure")

    monkeypatch.setattr(screenshot.os, "write", short_then_fail)
    # When: publication cannot complete the staging inode.
    with pytest.raises(screenshot.InvalidLocalBrowserScreenshotError):
        _ = screenshot.publish_private_screenshot(root, _PAYLOAD, _DIGEST, os.getuid())
    # Then: no partial PNG or staging entry remains.
    assert root.is_dir() and tuple(root.iterdir()) == ()


def test_destination_collision_preserves_existing_png_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the digest destination already names another private file.
    root = tmp_path / "screenshots"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(screenshot.secrets, "token_hex", lambda size: "a" * (size * 2))
    destination = root / f"{_DIGEST}-{'a' * 16}.png"
    destination.write_bytes(b"existing")
    destination.chmod(0o600)
    # When: exclusive publication encounters the existing destination.
    with pytest.raises(screenshot.InvalidLocalBrowserScreenshotError):
        _ = screenshot.publish_private_screenshot(root, _PAYLOAD, _DIGEST, os.getuid())
    # Then: the existing inode is preserved and the staging inode is gone.
    assert destination.read_bytes() == b"existing"
    assert tuple(root.iterdir()) == (destination,)


def test_temp_name_replacement_is_detected_without_deleting_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a competing private inode replaces the staging name immediately before rename.
    root = tmp_path / "screenshots"
    replacement = b"replacement"
    monkeypatch.setattr(screenshot.secrets, "token_hex", lambda size: "b" * (size * 2))
    original = screenshot.rename_entry_exclusively

    def replace_then_rename(
        source_directory: int,
        source: str,
        destination_directory: int,
        destination: str,
    ) -> None:
        os.unlink(source, dir_fd=source_directory)
        descriptor = os.open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=source_directory,
        )
        try:
            _ = os.write(descriptor, replacement)
        finally:
            os.close(descriptor)
        original(source_directory, source, destination_directory, destination)

    monkeypatch.setattr(screenshot, "rename_entry_exclusively", replace_then_rename)
    # When: publication verifies the post-rename final inode.
    with pytest.raises(screenshot.InvalidLocalBrowserScreenshotError):
        _ = screenshot.publish_private_screenshot(root, _PAYLOAD, _DIGEST, os.getuid())
    # Then: it does not report success or delete the competing replacement.
    destination = root / f"{_DIGEST}-{'b' * 16}.png"
    assert destination.read_bytes() == replacement
    assert tuple(root.iterdir()) == (destination,)
