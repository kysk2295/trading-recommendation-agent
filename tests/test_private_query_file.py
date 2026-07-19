from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)


def test_query_read_does_not_chmod_private_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    source = parent / "source.txt"
    source.write_text("payload\n", encoding="utf-8")
    source.chmod(0o600)

    def reject_fchmod(_descriptor: int, _mode: int) -> None:
        raise AssertionError("query-only read must not chmod")

    monkeypatch.setattr(os, "fchmod", reject_fchmod)

    # When
    payload = read_private_text_query_only(source)

    # Then
    assert payload == "payload\n"
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


def test_query_read_rejects_non_private_parent_without_mutating_it(tmp_path: Path) -> None:
    # Given
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o750)
    source = parent / "source.txt"
    source.write_text("payload\n", encoding="utf-8")
    source.chmod(0o600)

    # When / Then
    with pytest.raises(InvalidPrivateQueryFileError):
        _ = read_private_text_query_only(source)
    assert stat.S_IMODE(parent.stat().st_mode) == 0o750
