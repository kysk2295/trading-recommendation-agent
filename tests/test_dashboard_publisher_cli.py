from __future__ import annotations

import ast
import datetime as dt
import json
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from typer.testing import CliRunner
from watchfiles import Change
from websockets.exceptions import WebSocketException

import run_dashboard_publisher
from trading_agent.dashboard_commands import InteractionPayload
from trading_agent.dashboard_relay import is_reconnectable_group, pairing_url, run_interaction


class _SendSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


def test_dashboard_publisher_help() -> None:
    result = CliRunner().invoke(run_dashboard_publisher.app, ["--help"])

    assert result.exit_code == 0
    assert "redacted" in result.stdout
    assert "--once" in result.stdout
    assert "--pair-browser" in result.stdout
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


def test_dashboard_publisher_dry_run_emits_canonical_v2_json(tmp_path: Path) -> None:
    # Given a mode-600 publisher boundary and accepted redacted source receipt
    outputs = tmp_path / "outputs"
    source_root = outputs / "experiment_control"
    source_root.mkdir(parents=True)
    receipt = source_root / "dashboard-receipts.v2.jsonl"
    observed_at = dt.datetime.now(dt.UTC)
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "snapshot_epoch": "cli-fixture",
                "workspace": "research",
                "item_id": "research.cli",
                "kind": "research",
                "label": "CLI fixture",
                "value": "accepted",
                "observed_at": observed_at.isoformat(),
                "safe_ref": "c" * 64,
                "terminal_kind": "reviewer_decision",
                "state": "populated",
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    credentials = tmp_path / "dashboard.env"
    credentials.write_text(
        "DASHBOARD_URL=https://example.test\n"
        "DASHBOARD_INGEST_TOKEN=fixture-value-with-adequate-length\n",
        encoding="utf-8",
    )
    credentials.chmod(0o600)

    # When the real CLI dry-run boundary executes
    result = CliRunner().invoke(
        run_dashboard_publisher.app,
        [
            "--outputs",
            str(outputs),
            "--credentials",
            str(credentials),
            "--dry-run",
        ],
    )

    # Then it emits a strict canonical v2 payload without sending externally
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 2
    assert payload["workspaces"]["research"]["state"] == "unavailable"


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
    assert (
        pairing_url(
            "https://observatory.example",
            "/operator/pair/single-use-ticket",
        )
        == "https://observatory.example/operator/pair/single-use-ticket"
    )


def test_publisher_watches_account_ledger_without_periodic_broker_reads(
    tmp_path: Path,
) -> None:
    for name in (
        "live_sessions",
        "source_evidence",
        "experiment_control",
        "lane_control",
        "derivatives",
        "paper",
        "system",
    ):
        (tmp_path / name).mkdir()

    assert run_dashboard_publisher._watch_roots(tmp_path) == (
        tmp_path / "live_sessions",
        tmp_path / "source_evidence",
        tmp_path / "experiment_control",
        tmp_path / "lane_control",
        tmp_path / "derivatives",
        tmp_path / "paper",
        tmp_path / "system",
    )


@pytest.mark.anyio
async def test_publisher_watch_roots_coalesce_one_mutation_each(
    tmp_path: Path,
) -> None:
    # Given every stable root and an injectable event source
    for name in (
        "live_sessions",
        "source_evidence",
        "experiment_control",
        "lane_control",
        "derivatives",
        "paper",
        "system",
    ):
        (tmp_path / name).mkdir()
    roots = run_dashboard_publisher._watch_roots(tmp_path)
    socket = _SendSocket()

    observed_paths: tuple[Path, ...] = ()

    # When real files in every declared root change in one coalesced burst
    async def one_batch(
        *paths: Path,
        **_settings: int,
    ) -> AsyncIterator[set[tuple[Change, str]]]:
        nonlocal observed_paths
        observed_paths = paths
        changed: set[tuple[Change, str]] = set()
        for root in paths:
            mutation = root / "mutation.receipt"
            mutation.write_text(root.name, encoding="utf-8")
            changed.add((Change.added, str(mutation)))
        yield changed

    await run_dashboard_publisher._watch_output_events(
        socket,
        tmp_path,
        anyio.Lock(),
        one_batch,
    )

    # Then the publisher rebuilds and sends exactly one coalesced snapshot event
    assert observed_paths == roots
    assert all((root / "mutation.receipt").read_text(encoding="utf-8") == root.name for root in roots)
    assert len(socket.messages) == 1
    assert json.loads(socket.messages[0])["snapshot"]["schema_version"] == 2


@pytest.mark.anyio
async def test_publisher_idle_watch_does_no_projection_or_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an idle event subscription with counters on observable work
    socket = _SendSocket()
    projections = 0

    async def idle_watch(
        *_paths: Path,
        **_settings: int,
    ) -> AsyncIterator[set[tuple[Change, str]]]:
        await anyio.Event().wait()
        yield set()

    def counted_projection(_outputs: Path):
        nonlocal projections
        projections += 1
        return run_dashboard_publisher.collect_dashboard_snapshot_v2(_outputs)

    monkeypatch.setattr(run_dashboard_publisher, "awatch", idle_watch)
    monkeypatch.setattr(run_dashboard_publisher, "collect_dashboard_snapshot_v2", counted_projection)

    # When idleness is observed for a bounded interval
    with anyio.move_on_after(0.05):
        await run_dashboard_publisher._watch_output_events(socket, tmp_path, anyio.Lock())

    # Then no snapshot/database projection, send, HTTP poll, or model call occurs
    assert projections == 0
    assert socket.messages == []


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


def test_publisher_classifies_nested_websocket_failures_for_reconnect() -> None:
    failure = ExceptionGroup(
        "publisher connection",
        [ExceptionGroup("receive", [WebSocketException("publisher replaced")])],
    )

    assert is_reconnectable_group(failure)


@pytest.mark.anyio
async def test_publisher_reports_running_then_terminal_command_state(tmp_path: Path) -> None:
    socket = _SendSocket()
    interaction = InteractionPayload.model_validate(
        {
            "id": "019c0014-f0f5-7000-8000-000000000001",
            "agent_id": "research",
            "command": "결손을 요약해줘",
            "state": "queued",
            "response": None,
            "created_at": "2026-07-26T04:00:00Z",
            "updated_at": "2026-07-26T04:00:00Z",
        }
    )

    await run_interaction(
        socket,
        interaction,
        anyio.Lock(),
        anyio.CapacityLimiter(1),
        Path("/bin/echo"),
        tmp_path,
    )

    states = [json.loads(message)["state"] for message in socket.messages]
    assert states == ["running", "completed"]
