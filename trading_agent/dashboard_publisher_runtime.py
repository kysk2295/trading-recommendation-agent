from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from websockets.asyncio.client import ClientConnection

from trading_agent.dashboard_commands import PairingTicketMessage, parse_dashboard_event
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_publisher_events import watch_output_events
from trading_agent.dashboard_publisher_pairing import (
    InteractionRuntime,
    PairingRequestRuntime,
    PairingRequestState,
    PairingTicketHandler,
    PublisherEventReceiver,
    receive_events,
    watch_pairing_signal,
)
from trading_agent.dashboard_publisher_relay_runtime import relay_snapshots
from trading_agent.dashboard_relay import (
    DashboardRelayConnectionError,
    is_reconnectable_group,
    open_pairing_url,
    pairing_url,
)
from trading_agent.dashboard_system_current_authority import SystemAuthorityVerifierInput
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths


@dataclass(frozen=True, slots=True)
class PublisherRuntimeBinding:
    hermes_executable: Path
    worktree: Path
    interactive_state: Path


@dataclass(frozen=True, slots=True)
class PublisherRelayRequest:
    outputs: Path
    dashboard_url: str
    token: str
    initial_snapshot: DashboardSnapshotV2
    once: bool
    pair_browser: bool
    system_authority_verifier: SystemAuthorityVerifierInput
    cycle_database: Path | None
    kr_day_state_root: Path
    kr_operator_paths: KrAutonomousOperatorPaths | None


async def run_publisher_relay(
    request: PublisherRelayRequest,
    binding: PublisherRuntimeBinding,
) -> None:
    await relay_snapshots(
        request.outputs,
        request.dashboard_url,
        request.token,
        request.initial_snapshot,
        once=request.once,
        pair_browser=request.pair_browser,
        system_authority_verifier=request.system_authority_verifier,
        event_connection=partial(
            _run_event_connection,
            binding=binding,
            cycle_database=request.cycle_database,
            kr_day_state_root=request.kr_day_state_root,
            kr_operator_paths=request.kr_operator_paths,
        ),
        pair_browser_once=_pair_browser_once,
        cycle_database=request.cycle_database,
        kr_day_state_root=request.kr_day_state_root,
        kr_operator_paths=request.kr_operator_paths,
    )


async def _run_event_connection(
    socket: ClientConnection,
    outputs: Path,
    dashboard_url: str,
    pair_browser: bool,
    system_authority_verifier: SystemAuthorityVerifierInput,
    *,
    binding: PublisherRuntimeBinding,
    cycle_database: Path | None,
    kr_day_state_root: Path,
    kr_operator_paths: KrAutonomousOperatorPaths | None,
) -> None:
    send_lock = anyio.Lock()
    limiter = anyio.CapacityLimiter(1)
    pairing = PairingRequestState()
    pairing_runtime = PairingRequestRuntime(socket, send_lock, pairing)
    try:
        async with anyio.create_task_group() as tasks:
            receiver = PublisherEventReceiver(
                PairingTicketHandler(dashboard_url, pair_browser, pairing, open_pairing_url),
                InteractionRuntime(
                    outputs,
                    send_lock,
                    limiter,
                    tasks,
                    binding.hermes_executable,
                    binding.worktree,
                    binding.interactive_state,
                ),
            )
            tasks.start_soon(
                watch_output_events,
                socket,
                outputs,
                send_lock,
                None,
                system_authority_verifier,
                cycle_database,
                kr_day_state_root,
                kr_operator_paths,
            )
            tasks.start_soon(receive_events, socket, receiver)
            tasks.start_soon(watch_pairing_signal, pairing_runtime)
    except BaseExceptionGroup as error:
        if is_reconnectable_group(error):
            raise DashboardRelayConnectionError from error
        raise


async def _pair_browser_once(socket: ClientConnection, dashboard_url: str) -> None:
    while True:
        raw = await socket.recv()
        if not isinstance(raw, str):
            continue
        event = parse_dashboard_event(raw)
        if isinstance(event, PairingTicketMessage):
            await open_pairing_url(pairing_url(dashboard_url, event.path))
            return


__all__ = (
    "PublisherRelayRequest",
    "PublisherRuntimeBinding",
    "run_publisher_relay",
)
