#!/usr/bin/env -S uv run

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import anyio
import typer
from anyio.abc import TaskGroup
from pydantic import ValidationError
from rich import print as rprint
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import WebSocketException

from trading_agent.dashboard_commands import (
    DashboardInteractionMessage,
    PairingTicketMessage,
    parse_dashboard_event,
)
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_publisher_cli import register_execution_commands
from trading_agent.dashboard_publisher_events import (
    SnapshotSocket,
    WatchFactory,
    publisher_url,
    reconnect_delay_seconds,
    watch_output_events,
)
from trading_agent.dashboard_publisher_relay_runtime import relay_snapshots
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
from trading_agent.dashboard_system_authority_config import (
    load_system_authority_verifier,
)
from trading_agent.dashboard_system_current_authority import (
    SystemAuthorityVerifierInput,
)

app = typer.Typer(
    help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.",
    invoke_without_command=True,
)
DEFAULT_OUTPUTS = Path(__file__).resolve().parent / "outputs"
DEFAULT_CREDENTIALS = Path.home() / ".config" / "trading-agent" / "dashboard.env"
DEFAULT_SYSTEM_AUTHORITY_CONFIG = (
    Path.home() / ".config" / "trading-agent" / "system-authority.json"
)
DEFAULT_INTERACTIVE_STATE = Path.home() / ".local" / "state" / "trading-agent" / "dashboard-interactive"
HERMES_EXECUTABLE = Path(shutil.which("hermes") or Path.home() / ".local/bin/hermes")
WORKTREE = Path(__file__).resolve().parent
_publisher_url = publisher_url
_reconnect_delay_seconds = reconnect_delay_seconds
register_execution_commands(app, DEFAULT_INTERACTIVE_STATE)


@app.callback()
def publisher_default(
    context: typer.Context,
    outputs: Annotated[Path, typer.Option(file_okay=False)] = DEFAULT_OUTPUTS,
    credentials: Annotated[Path, typer.Option()] = DEFAULT_CREDENTIALS,
    system_authority_config: Annotated[
        Path,
        typer.Option(
            help="고정 Ed25519 공개 검증키 설정 파일",
        ),
    ] = DEFAULT_SYSTEM_AUTHORITY_CONFIG,
    once: Annotated[bool, typer.Option(help="한 번 전송한 뒤 종료")] = False,
    dry_run: Annotated[bool, typer.Option(help="외부 전송 없이 snapshot 경계만 검증")] = False,
    pair_browser: Annotated[bool, typer.Option(help="일회용 운영자 브라우저 연결")] = False,
) -> None:
    if context.invoked_subcommand is None:
        publish(
            outputs,
            credentials,
            system_authority_config,
            once,
            dry_run,
            pair_browser,
        )


@app.command(help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.")
def publish(
    outputs: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_OUTPUTS,
    credentials: Annotated[Path, typer.Option()] = DEFAULT_CREDENTIALS,
    system_authority_config: Annotated[
        Path,
        typer.Option(
            help="고정 Ed25519 공개 검증키 설정 파일",
        ),
    ] = DEFAULT_SYSTEM_AUTHORITY_CONFIG,
    once: Annotated[bool, typer.Option(help="한 번 전송한 뒤 종료")] = False,
    dry_run: Annotated[bool, typer.Option(help="외부 전송 없이 snapshot 경계만 검증")] = False,
    pair_browser: Annotated[bool, typer.Option(help="일회용 운영자 브라우저 연결")] = False,
) -> None:
    try:
        config = load_dashboard_credentials(credentials)
    except DashboardCredentialError as error:
        raise typer.BadParameter(str(error), param_hint="--credentials") from error
    if not outputs.is_dir():
        raise typer.BadParameter(
            "outputs_directory_missing",
            param_hint="--outputs",
        )
    system_authority_verifier = load_system_authority_verifier(
        system_authority_config,
        untrusted_root=outputs,
    )
    snapshot = collect_dashboard_snapshot_v2(
        outputs,
        system_authority_verifier=system_authority_verifier,
    )
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
            system_authority_verifier,
        )
        if once:
            rprint("[green]dashboard snapshot published by event relay[/green]")
    except (OSError, TimeoutError, ValidationError, WebSocketException) as error:
        rprint("[red]dashboard event relay failed[/red]")
        raise typer.Exit(code=1) from error


async def _run_relay(
    outputs: Path,
    dashboard_url: str,
    token: str,
    initial_snapshot: DashboardSnapshotV2,
    once: bool,
    pair_browser: bool,
    system_authority_verifier: SystemAuthorityVerifierInput = None,
) -> None:
    await relay_snapshots(
        outputs,
        dashboard_url,
        token,
        initial_snapshot,
        once=once,
        pair_browser=pair_browser,
        system_authority_verifier=system_authority_verifier,
        event_connection=_run_event_connection,
        pair_browser_once=_pair_browser_once,
    )


async def _run_event_connection(
    socket: ClientConnection,
    outputs: Path,
    dashboard_url: str,
    pair_browser: bool,
    system_authority_verifier: SystemAuthorityVerifierInput = None,
) -> None:
    send_lock = anyio.Lock()
    limiter = anyio.CapacityLimiter(1)
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(
                _watch_output_events,
                socket,
                outputs,
                send_lock,
                None,
                system_authority_verifier,
            )
            tasks.start_soon(
                _receive_events,
                socket,
                dashboard_url,
                pair_browser,
                outputs,
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
    outputs: Path,
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
                DEFAULT_INTERACTIVE_STATE,
                outputs / "source_evidence",
            )


async def _watch_output_events(
    socket: SnapshotSocket,
    outputs: Path,
    send_lock: anyio.Lock,
    watcher: WatchFactory | None = None,
    system_authority_verifier: SystemAuthorityVerifierInput = None,
) -> None:
    await watch_output_events(
        socket,
        outputs,
        send_lock,
        watcher,
        system_authority_verifier,
    )


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
