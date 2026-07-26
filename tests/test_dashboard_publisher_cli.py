from __future__ import annotations

import ast
from pathlib import Path

from typer.testing import CliRunner

import run_dashboard_publisher


def test_dashboard_publisher_help() -> None:
    result = CliRunner().invoke(run_dashboard_publisher.app, ["--help"])

    assert result.exit_code == 0
    assert "redacted" in result.stdout
    assert "--once" in result.stdout
    assert "--interval-seconds" not in result.stdout


def test_dashboard_publisher_rejects_non_https_remote_url(tmp_path: Path) -> None:
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=http://railway.example\n"
        "DASHBOARD_INGEST_TOKEN=token-with-adequate-length-123\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)

    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        ["--credentials", str(credentials), "--once"],
    )

    assert result.exit_code != 0
    assert "invalid_settings" in result.output


def test_publisher_uses_websocket_events_without_periodic_http_or_sleep() -> None:
    source = Path(run_dashboard_publisher.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        root
        for node in ast.walk(tree)
        for root in (
            [name.name.partition(".")[0] for name in node.names]
            if isinstance(node, ast.Import)
            else [node.module.partition(".")[0]]
            if isinstance(node, ast.ImportFrom) and node.module is not None
            else []
        )
    }
    called_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "httpx2" not in imported_roots
    assert "post" not in called_names
    assert "websockets" in imported_roots
    assert "watchfiles" in imported_roots


def test_publisher_converts_dashboard_urls_to_publish_websockets() -> None:
    assert (
        run_dashboard_publisher._publisher_url("https://observatory.example")
        == "wss://observatory.example/api/realtime/publish"
    )
    assert (
        run_dashboard_publisher._publisher_url("http://localhost:3100")
        == "ws://localhost:3100/api/realtime/publish"
    )


def test_publisher_watches_account_ledger_without_periodic_broker_reads(
    tmp_path: Path,
) -> None:
    for name in ("live_sessions", "experiment_control", "lane_control"):
        (tmp_path / name).mkdir()

    assert run_dashboard_publisher._watch_roots(tmp_path) == (
        tmp_path / "live_sessions",
        tmp_path / "experiment_control",
        tmp_path / "lane_control",
    )


def test_publisher_bounds_reconnect_backoff() -> None:
    assert [run_dashboard_publisher._reconnect_delay_seconds(index) for index in range(7)] == [
        5,
        10,
        20,
        40,
        60,
        60,
        60,
    ]
