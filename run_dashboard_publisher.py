#!/usr/bin/env -S uv run

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path
from typing import Annotated

import anyio
import typer
from pydantic import ValidationError
from rich import print as rprint
from websockets.exceptions import WebSocketException

from trading_agent.dashboard_agent_readiness import (
    AgentReadinessRequest,
    record_agent_readiness,
)
from trading_agent.dashboard_autonomous_publisher import DEFAULT_AUTONOMOUS_STATE
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_production_execution_boundary import (
    create_production_execution_boundary,
)
from trading_agent.dashboard_publisher_cli import register_execution_commands
from trading_agent.dashboard_publisher_events import (
    DashboardPublisherAuthorityError,
    current_code_sha,
    require_current_main_authority,
    watch_output_events,
)
from trading_agent.dashboard_publisher_pairing import (
    InteractionRuntime,
    PairingRequestRuntime,
    PairingRequestState,
    PairingTicketHandler,
    PublisherEventReceiver,
)
from trading_agent.dashboard_publisher_pairing import (
    receive_events as _receive_events,
)
from trading_agent.dashboard_publisher_runtime import (
    PublisherRelayRequest,
    PublisherRuntimeBinding,
    run_publisher_relay,
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
from trading_agent.research_agent_service_config import (
    InvalidResearchAgentServiceConfigError,
    load_research_agent_service_config,
)

app = typer.Typer(
    help="로컬 산출물을 redacted 운영 snapshot으로 안전하게 전송합니다.",
    invoke_without_command=True,
)
DEFAULT_OUTPUTS = Path(__file__).resolve().parent / "outputs"
DEFAULT_CREDENTIALS = Path.home() / ".config" / "trading-agent" / "dashboard.env"
DEFAULT_SYSTEM_AUTHORITY_CONFIG = Path.home() / ".config" / "trading-agent" / "system-authority.json"
DEFAULT_INTERACTIVE_STATE = Path.home() / ".local" / "state" / "trading-agent" / "dashboard-interactive"
DEFAULT_RESEARCH_AGENT_CONFIG = Path.home() / ".config" / "trading-agent" / "research-agent-runtime-v2.json"
HERMES_EXECUTABLE = Path(shutil.which("hermes") or Path.home() / ".local/bin/hermes")
WORKTREE = Path(__file__).resolve().parent
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
    research_agent_config: Annotated[Path, typer.Option()] = DEFAULT_RESEARCH_AGENT_CONFIG,
) -> None:
    if context.invoked_subcommand == "publish" and any(
        (source := context.get_parameter_source(parameter)) is not None and source.name == "COMMANDLINE"
        for parameter in (
            "outputs",
            "credentials",
            "system_authority_config",
            "once",
            "dry_run",
            "pair_browser",
            "research_agent_config",
        )
    ):
        raise typer.BadParameter("publish_options_must_follow_subcommand")
    if context.invoked_subcommand is None:
        publish(
            outputs,
            credentials,
            system_authority_config,
            once,
            dry_run,
            pair_browser,
            research_agent_config,
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
    research_agent_config: Annotated[Path, typer.Option()] = DEFAULT_RESEARCH_AGENT_CONFIG,
) -> None:
    try:
        require_current_main_authority()
    except DashboardPublisherAuthorityError as error:
        raise typer.BadParameter(str(error), param_hint="startup") from error
    try:
        config = load_dashboard_credentials(credentials)
    except DashboardCredentialError as error:
        raise typer.BadParameter(str(error), param_hint="--credentials") from error
    if not outputs.is_dir():
        raise typer.BadParameter(
            "outputs_directory_missing",
            param_hint="--outputs",
        )
    cycle_database = _cycle_database(research_agent_config)
    system_authority_verifier = load_system_authority_verifier(
        system_authority_config,
        untrusted_root=outputs,
    )
    snapshot = collect_dashboard_snapshot_v2(
        outputs,
        system_authority_verifier=system_authority_verifier,
        cycle_database=cycle_database,
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
            cycle_database,
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
    cycle_database: Path | None = None,
) -> None:
    await run_publisher_relay(
        PublisherRelayRequest(
            outputs,
            dashboard_url,
            token,
            initial_snapshot,
            once,
            pair_browser,
            system_authority_verifier,
            cycle_database,
        ),
        PublisherRuntimeBinding(HERMES_EXECUTABLE, WORKTREE, DEFAULT_INTERACTIVE_STATE),
    )


def _record_agent_readiness(outputs: Path) -> None:
    record_agent_readiness(
        AgentReadinessRequest(
            outputs,
            dt.datetime.now(dt.UTC),
            current_code_sha(),
            DEFAULT_AUTONOMOUS_STATE / "authorities",
            WORKTREE,
        ),
        create_production_execution_boundary,
    )


def _cycle_database(config_path: Path) -> Path | None:
    if not config_path.exists() and not config_path.is_symlink():
        return None
    try:
        return load_research_agent_service_config(config_path).cycle_database
    except InvalidResearchAgentServiceConfigError as error:
        raise typer.BadParameter("research_agent_config_invalid", param_hint="--research-agent-config") from error


__all__ = (
    "InteractionRuntime",
    "PairingRequestRuntime",
    "PairingRequestState",
    "PairingTicketHandler",
    "PublisherEventReceiver",
    "_receive_events",
    "watch_output_events",
)


if __name__ == "__main__":
    app()
