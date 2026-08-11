from __future__ import annotations

from pathlib import Path

from trading_agent.dashboard_publisher_events import watch_roots


def test_dashboard_publisher_watches_hermes_terminal_database_root(tmp_path: Path) -> None:
    # Given
    hermes = tmp_path / "hermes"
    hermes.mkdir()

    # When
    roots = watch_roots(tmp_path)

    # Then
    assert roots == (hermes,)
