#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "anyio>=4.9",
#   "pydantic>=2.11",
#   "rich>=13.9",
#   "typer>=0.15",
#   "watchfiles>=1.1,<2",
#   "websockets>=16,<17",
# ]
# ///

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import anyio
import typer
from pydantic import ValidationError
from rich import print as rprint
from watchfiles import awatch
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from trading_agent.dashboard_models import DashboardSnapshot
from trading_agent.dashboard_snapshot import (
    DashboardCredentialError,
    JobRow,
    collect_dashboard_snapshot,
    load_dashboard_credentials,
)

app = typer.Typer(help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.")
DEFAULT_OUTPUTS = Path(__file__).resolve().parent / "outputs"
DEFAULT_CREDENTIALS = Path.home() / ".config" / "trading-agent" / "dashboard.env"
MAX_RECONNECT_SECONDS = 60


@app.command(help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.")
def publish(
    outputs: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_OUTPUTS,
    credentials: Annotated[Path, typer.Option()] = DEFAULT_CREDENTIALS,
    once: Annotated[bool, typer.Option(help="한 번 전송한 뒤 종료")] = False,
    dry_run: Annotated[bool, typer.Option(help="외부 전송 없이 snapshot 경계만 검증")] = False,
) -> None:
    try:
        config = load_dashboard_credentials(credentials)
    except DashboardCredentialError as error:
        raise typer.BadParameter(str(error), param_hint="--credentials") from error
    snapshot = collect_dashboard_snapshot(outputs, jobs=_launchd_jobs())
    if dry_run:
        rprint(
            "[green]dashboard snapshot valid[/green] "
            f"session={snapshot.forward.session_date} "
            f"recommendations={len(snapshot.recommendations)}"
        )
        return
    try:
        anyio.run(
            _run_relay,
            outputs,
            config.dashboard_url,
            config.ingest_token.get_secret_value(),
            snapshot,
            once,
        )
        if once:
            rprint("[green]dashboard snapshot published by event relay[/green]")
    except (OSError, TimeoutError, ValidationError, WebSocketException) as error:
        rprint("[red]dashboard event relay failed[/red]")
        raise typer.Exit(code=1) from error


async def _relay(
    outputs: Path,
    dashboard_url: str,
    token: str,
    initial_snapshot: DashboardSnapshot,
    *,
    once: bool,
) -> None:
    attempt = 0
    snapshot = initial_snapshot
    while True:
        try:
            async with connect(
                _publisher_url(dashboard_url),
                additional_headers={"Authorization": f"Bearer {token}"},
                proxy=None,
                open_timeout=10,
                ping_interval=120,
                ping_timeout=20,
                close_timeout=5,
                max_size=512 * 1024,
                max_queue=16,
            ) as socket:
                attempt = 0
                await _send_snapshot(socket, snapshot)
                if once:
                    return
                await _watch_output_events(socket, outputs)
        except (OSError, TimeoutError, WebSocketException):
            if once:
                raise
            await anyio.sleep(_reconnect_delay_seconds(attempt))
            attempt += 1
            snapshot = collect_dashboard_snapshot(outputs, jobs=_launchd_jobs())


async def _run_relay(
    outputs: Path,
    dashboard_url: str,
    token: str,
    initial_snapshot: DashboardSnapshot,
    once: bool,
) -> None:
    await _relay(outputs, dashboard_url, token, initial_snapshot, once=once)


async def _watch_output_events(
    socket: ClientConnection,
    outputs: Path,
) -> None:
    async for _changes in awatch(*_watch_roots(outputs), debounce=2_000, step=250):
        snapshot = collect_dashboard_snapshot(outputs, jobs=_launchd_jobs())
        await _send_snapshot(socket, snapshot)


async def _send_snapshot(
    socket: ClientConnection,
    snapshot: DashboardSnapshot,
) -> None:
    await socket.send(
        json.dumps(
            {"type": "snapshot", "snapshot": snapshot.model_dump(mode="json")},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _publisher_url(dashboard_url: str) -> str:
    parsed = urlsplit(dashboard_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, "/api/realtime/publish", "", ""))


def _watch_roots(outputs: Path) -> tuple[Path, ...]:
    candidates = (
        outputs / "live_sessions",
        outputs / "experiment_control",
        outputs / "lane_control",
    )
    existing = tuple(path for path in candidates if path.is_dir())
    return existing or (outputs,)


def _reconnect_delay_seconds(attempt: int) -> int:
    return min(MAX_RECONNECT_SECONDS, 5 * 2 ** max(0, attempt))


def _launchd_jobs() -> tuple[JobRow, ...]:
    try:
        result = subprocess.run(
            ("launchctl", "list"),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    rows: list[JobRow] = []
    for line in result.stdout.splitlines()[1:]:
        columns = line.split("\t")
        if len(columns) != 3 or not columns[2].startswith("ai.trading-agent."):
            continue
        pid = None if columns[0] == "-" else int(columns[0])
        try:
            exit_code = int(columns[1])
        except ValueError:
            continue
        rows.append((columns[2], pid, exit_code))
    return tuple(rows)


if __name__ == "__main__":
    app()
