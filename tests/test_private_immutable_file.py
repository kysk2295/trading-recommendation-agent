from __future__ import annotations

import os
from pathlib import Path

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
