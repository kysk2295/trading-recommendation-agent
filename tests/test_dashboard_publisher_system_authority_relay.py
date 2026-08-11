from __future__ import annotations

import base64
import datetime as dt
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import TracebackType

import anyio
import pytest
from dashboard_system_fixtures import system_authority_signer
from websockets.asyncio.client import ClientConnection

import trading_agent.dashboard_publisher_relay_runtime as relay_runtime
from tests.test_dashboard_publisher_system_authority import _write_system_outputs
from trading_agent.dashboard_models_v2 import DashboardSnapshotV2
from trading_agent.dashboard_publisher_events import watch_output_events
from trading_agent.dashboard_snapshot_v2 import collect_dashboard_snapshot_v2
from trading_agent.dashboard_system_current_authority import (
    SystemAuthorityVerifierInput,
)
from trading_agent.dashboard_system_operations import OPERATIONS_FILE


class _Socket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


class _Connection:
    def __init__(self, socket: _Socket) -> None:
        self._socket = socket

    async def __aenter__(self) -> _Socket:
        return self._socket

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None


class _StopReconnect(RuntimeError):
    pass


@pytest.mark.anyio
async def test_event_watch_reuses_startup_verifier_and_projects_signed_system(
    tmp_path: Path,
) -> None:
    now = dt.datetime.now(dt.UTC)
    signer = system_authority_signer()
    outputs = _write_system_outputs(tmp_path, signer, now)
    socket = _Socket()

    async def one_change(
        *_paths: Path,
        **_settings: int,
    ) -> AsyncIterator[frozenset[Path]]:
        yield frozenset({outputs / "system" / OPERATIONS_FILE})

    await watch_output_events(
        socket,
        outputs,
        anyio.Lock(),
        one_change,
        signer.verifier,
    )

    assert len(socket.messages) == 1
    payload = json.loads(socket.messages[0])
    assert payload["snapshot"]["workspaces"]["system"]["state"] == "populated"


@pytest.mark.anyio
async def test_reconnect_reuses_same_verifier_for_rebuilt_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = dt.datetime.now(dt.UTC)
    signer = system_authority_signer()
    outputs = _write_system_outputs(tmp_path, signer, now)
    initial = collect_dashboard_snapshot_v2(
        outputs,
        now=now,
        system_authority_verifier=signer.verifier,
    )
    sockets = [_Socket(), _Socket()]
    connections = iter(_Connection(socket) for socket in sockets)
    verifier_ids: list[int] = []
    projected_states: list[str] = []
    real_collect = collect_dashboard_snapshot_v2
    event_runs = 0

    def connect_without_network(
        *_args: str,
        **_kwargs: str | int | bool | dict[str, str] | None,
    ) -> _Connection:
        return next(connections)

    def collect_with_observation(
        path: Path,
        *,
        system_authority_verifier: SystemAuthorityVerifierInput,
    ) -> DashboardSnapshotV2:
        verifier_ids.append(id(system_authority_verifier))
        snapshot = real_collect(
            path,
            now=now,
            system_authority_verifier=system_authority_verifier,
        )
        projected_states.append(snapshot.workspaces.system.state)
        return snapshot

    async def event_connection(
        _socket: ClientConnection,
        _outputs: Path,
        _dashboard_url: str,
        _pair_browser: bool,
        _verifier: SystemAuthorityVerifierInput,
    ) -> None:
        nonlocal event_runs
        event_runs += 1
        if event_runs == 1:
            raise OSError("fixture disconnect")
        raise _StopReconnect

    async def no_wait(_seconds: float) -> None:
        return None

    async def no_pair(_socket: ClientConnection, _url: str) -> None:
        return None

    monkeypatch.setattr(relay_runtime, "connect", connect_without_network)
    monkeypatch.setattr(
        relay_runtime,
        "collect_dashboard_snapshot_v2",
        collect_with_observation,
    )
    monkeypatch.setattr(relay_runtime.anyio, "sleep", no_wait)

    with pytest.raises(_StopReconnect):
        await relay_runtime.relay_snapshots(
            outputs,
            "https://example.test",
            "redacted-fixture-token",
            initial,
            once=False,
            pair_browser=False,
            system_authority_verifier=signer.verifier,
            event_connection=event_connection,
            pair_browser_once=no_pair,
        )

    assert verifier_ids == [id(signer.verifier)]
    assert projected_states == ["populated"]
    assert len(sockets[0].messages) == 1
    assert len(sockets[1].messages) == 1
    assert base64.b64encode(signer.public_key).decode() not in "".join(sockets[0].messages + sockets[1].messages)
