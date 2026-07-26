#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "anyio>=4.9",
#   "pydantic>=2.11",
#   "rich>=13.9",
#   "typer>=0.15",
#   "websockets>=16,<17",
# ]
# ///

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import anyio
import typer
from anyio.abc import TaskGroup
from pydantic import ValidationError
from rich import print as rprint
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from trading_agent.dashboard_autonomous_publisher import (
    DEFAULT_AUTONOMOUS_STATE,
    InvalidAutonomousTriggerFixtureError,
    execute_autonomous_fixture,
)
from trading_agent.dashboard_commands import (
    DashboardInteractionMessage,
    PairingTicketMessage,
    parse_dashboard_event,
)
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_publisher_events import (
    SnapshotSocket,
    WatchFactory,
    watch_output_events,
)
from trading_agent.dashboard_publisher_events import (
    publisher_url as _publisher_url,
)
from trading_agent.dashboard_publisher_events import (
    reconnect_delay_seconds as _reconnect_delay_seconds,
)
from trading_agent.dashboard_publisher_events import (
    send_snapshot as _send_snapshot,
)
from trading_agent.dashboard_relay import (
    DashboardRelayConnectionError,
    is_reconnectable_group,
    open_pairing_url,
    pairing_url,
    run_interaction,
)
from trading_agent.dashboard_snapshot import (
    DashboardCredentialError,
    load_dashboard_credentials,
)
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2

app = typer.Typer(
    help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.",
    invoke_without_command=True,
)
DEFAULT_OUTPUTS = Path(__file__).resolve().parent / "outputs"
DEFAULT_CREDENTIALS = Path.home() / ".config" / "trading-agent" / "dashboard.env"
HERMES_EXECUTABLE = Path(shutil.which("hermes") or Path.home() / ".local/bin/hermes")
WORKTREE = Path(__file__).resolve().parent


@app.callback()
def publisher_default(
    context: typer.Context,
    outputs: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_OUTPUTS,
    credentials: Annotated[Path, typer.Option()] = DEFAULT_CREDENTIALS,
    once: Annotated[bool, typer.Option(help="한 번 전송한 뒤 종료")] = False,
    dry_run: Annotated[bool, typer.Option(help="외부 전송 없이 snapshot 경계만 검증")] = False,
    pair_browser: Annotated[bool, typer.Option(help="일회용 운영자 브라우저 연결")] = False,
) -> None:
    if context.invoked_subcommand is None:
        publish(outputs, credentials, once, dry_run, pair_browser)


@app.command("autonomous-agent", help="typed trigger 한 건을 격리된 autonomous 연구 task로 실행합니다.")
def autonomous_agent(
    trigger_fixture: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    state_root: Annotated[Path, typer.Option()] = DEFAULT_AUTONOMOUS_STATE,
    hermes_executable: Annotated[Path, typer.Option()] = HERMES_EXECUTABLE,
    fake_hermes: Annotated[bool, typer.Option(help="유료 호출 없는 로컬 fake process 사용")] = False,
    dry_run: Annotated[bool, typer.Option(help="외부 relay 전송 없이 로컬 control plane만 검증")] = False,
    expect_cleanup: Annotated[bool, typer.Option(help="격리 환경 cleanup receipt 필수")] = False,
) -> None:
    del dry_run
    try:
        outcome = execute_autonomous_fixture(
            trigger_fixture,
            state_root=state_root,
            hermes_executable=hermes_executable,
            fake_hermes=fake_hermes,
        )
    except InvalidAutonomousTriggerFixtureError as error:
        raise typer.BadParameter("invalid_autonomous_trigger", param_hint="--trigger-fixture") from error
    if expect_cleanup and not outcome.cleanup_completed:
        typer.echo("AUTONOMOUS_BLOCKED model_processes=0 receipt=1")
        raise typer.Exit(code=1)
    if outcome.state == "completed":
        typer.echo("AUTONOMOUS_OK claims=1 model_processes=1 duplicate_launches=0 evidence=append_only cleanup=1")
        return
    typer.echo(f"AUTONOMOUS_BLOCKED model_processes={outcome.model_processes} receipt=1")
    if outcome.state not in {"blocked", "duplicate"}:
        raise typer.Exit(code=1)


@app.command(help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.")
def publish(
    outputs: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_OUTPUTS,
    credentials: Annotated[Path, typer.Option()] = DEFAULT_CREDENTIALS,
    once: Annotated[bool, typer.Option(help="한 번 전송한 뒤 종료")] = False,
    dry_run: Annotated[bool, typer.Option(help="외부 전송 없이 snapshot 경계만 검증")] = False,
    pair_browser: Annotated[bool, typer.Option(help="일회용 운영자 브라우저 연결")] = False,
) -> None:
    try:
        config = load_dashboard_credentials(credentials)
    except DashboardCredentialError as error:
        raise typer.BadParameter(str(error), param_hint="--credentials") from error
    snapshot = collect_dashboard_snapshot_v2(outputs)
    if dry_run:
        typer.echo(snapshot.model_dump_json())
        return
    try:
        anyio.run(
            _run_relay,
            outputs,
            config.dashboard_url,
            config.ingest_token.get_secret_value(),
            snapshot,
            once,
            pair_browser,
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
    initial_snapshot: DashboardSnapshotV2,
    *,
    once: bool,
    pair_browser: bool,
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
                if pair_browser:
                    await socket.send('{"type":"pairing_request"}')
                if once and pair_browser:
                    await _pair_browser_once(socket, dashboard_url)
                    return
                if once:
                    return
                await _run_event_connection(socket, outputs, dashboard_url, pair_browser)
        except (OSError, TimeoutError, WebSocketException):
            if once:
                raise
            await anyio.sleep(_reconnect_delay_seconds(attempt))
            attempt += 1
            snapshot = collect_dashboard_snapshot_v2(outputs)


async def _run_relay(
    outputs: Path,
    dashboard_url: str,
    token: str,
    initial_snapshot: DashboardSnapshotV2,
    once: bool,
    pair_browser: bool,
) -> None:
    await _relay(
        outputs,
        dashboard_url,
        token,
        initial_snapshot,
        once=once,
        pair_browser=pair_browser,
    )


async def _run_event_connection(
    socket: ClientConnection,
    outputs: Path,
    dashboard_url: str,
    pair_browser: bool,
) -> None:
    send_lock = anyio.Lock()
    limiter = anyio.CapacityLimiter(1)
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(_watch_output_events, socket, outputs, send_lock)
            tasks.start_soon(
                _receive_events,
                socket,
                dashboard_url,
                pair_browser,
                send_lock,
                limiter,
                tasks,
            )
    except BaseExceptionGroup as error:
        if is_reconnectable_group(error):
            raise DashboardRelayConnectionError from error
        raise


async def _receive_events(
    socket: ClientConnection,
    dashboard_url: str,
    pair_browser: bool,
    send_lock: anyio.Lock,
    limiter: anyio.CapacityLimiter,
    tasks: TaskGroup,
) -> None:
    async for raw in socket:
        if not isinstance(raw, str):
            continue
        event = parse_dashboard_event(raw)
        if isinstance(event, PairingTicketMessage):
            if pair_browser:
                await open_pairing_url(pairing_url(dashboard_url, event.path))
            continue
        if isinstance(event, DashboardInteractionMessage):
            tasks.start_soon(
                run_interaction,
                socket,
                event.interaction,
                send_lock,
                limiter,
                HERMES_EXECUTABLE,
                WORKTREE,
            )


async def _watch_output_events(
    socket: SnapshotSocket,
    outputs: Path,
    send_lock: anyio.Lock,
    watcher: WatchFactory | None = None,
) -> None:
    await watch_output_events(socket, outputs, send_lock, HERMES_EXECUTABLE, watcher)


async def _pair_browser_once(socket: ClientConnection, dashboard_url: str) -> None:
    while True:
        raw = await socket.recv()
        if not isinstance(raw, str):
            continue
        event = parse_dashboard_event(raw)
        if isinstance(event, PairingTicketMessage):
            await open_pairing_url(pairing_url(dashboard_url, event.path))
            return


if __name__ == "__main__":
    app()
