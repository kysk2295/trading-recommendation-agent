from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_publisher_events import (
    publisher_url,
    reconnect_delay_seconds,
    send_snapshot,
)
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.dashboard_system_current_authority import (
    SystemAuthorityVerifierInput,
)
from trading_agent.kr_autonomous_operator_paths import KrAutonomousOperatorPaths

EventConnection = Callable[
    [
        ClientConnection,
        Path,
        str,
        bool,
        SystemAuthorityVerifierInput,
    ],
    Awaitable[None],
]
PairBrowserOnce = Callable[[ClientConnection, str], Awaitable[None]]


async def relay_snapshots(
    outputs: Path,
    dashboard_url: str,
    token: str,
    initial_snapshot: DashboardSnapshotV2,
    *,
    once: bool,
    pair_browser: bool,
    system_authority_verifier: SystemAuthorityVerifierInput,
    event_connection: EventConnection,
    pair_browser_once: PairBrowserOnce,
    cycle_database: Path | None = None,
    kr_day_state_root: Path | None = None,
    kr_operator_paths: KrAutonomousOperatorPaths | None = None,
) -> None:
    attempt = 0
    snapshot = initial_snapshot
    while True:
        try:
            async with connect(
                publisher_url(dashboard_url),
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
                await send_snapshot(socket, snapshot)
                if pair_browser:
                    await socket.send('{"type":"pairing_request"}')
                if once and pair_browser:
                    await pair_browser_once(socket, dashboard_url)
                    return
                if once:
                    return
                await event_connection(
                    socket,
                    outputs,
                    dashboard_url,
                    pair_browser,
                    system_authority_verifier,
                )
        except (OSError, TimeoutError, WebSocketException):
            if once:
                raise
            await anyio.sleep(reconnect_delay_seconds(attempt))
            attempt += 1
            if cycle_database is None:
                if kr_day_state_root is None:
                    if kr_operator_paths is None:
                        snapshot = collect_dashboard_snapshot_v2(
                            outputs,
                            system_authority_verifier=system_authority_verifier,
                        )
                    else:
                        snapshot = collect_dashboard_snapshot_v2(
                            outputs,
                            system_authority_verifier=system_authority_verifier,
                            kr_operator_paths=kr_operator_paths,
                        )
                else:
                    snapshot = collect_dashboard_snapshot_v2(
                        outputs,
                        system_authority_verifier=system_authority_verifier,
                        kr_day_state_root=kr_day_state_root,
                        kr_operator_paths=kr_operator_paths,
                    )
            else:
                snapshot = collect_dashboard_snapshot_v2(
                    outputs,
                    system_authority_verifier=system_authority_verifier,
                    cycle_database=cycle_database,
                    kr_day_state_root=kr_day_state_root,
                    kr_operator_paths=kr_operator_paths,
                )


__all__ = ("EventConnection", "PairBrowserOnce", "relay_snapshots")
