from __future__ import annotations

from typer.testing import CliRunner

import run_dashboard_publisher


def test_dashboard_publisher_help() -> None:
    result = CliRunner().invoke(run_dashboard_publisher.app, ["--help"])

    assert result.exit_code == 0
    assert "redacted" in result.stdout
    assert "--once" in result.stdout


def test_dashboard_publisher_rejects_too_short_interval() -> None:
    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        ["--interval-seconds", "1"],
    )

    assert result.exit_code != 0
    assert "range 5<=x<=300" in result.output
