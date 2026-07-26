from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import anyio
from pydantic import ValidationError
from websockets.exceptions import WebSocketException

from trading_agent.dashboard_commands import InteractionPayload, InteractionResult, execute_interaction
from trading_agent.dashboard_directed_jobs import DirectedEventDeliveryError, DirectedJobEvent


class DashboardSendSocket(Protocol):
    async def send(self, message: str) -> None: ...


class DashboardRelayConnectionError(OSError):
    pass


async def run_interaction(
    socket: DashboardSendSocket,
    interaction: InteractionPayload,
    send_lock: anyio.Lock,
    limiter: anyio.CapacityLimiter,
    hermes_executable: Path,
    worktree: Path,
    state_root: Path,
    source_evidence_root: Path,
) -> None:
    async with limiter:
        await send_result(
            socket,
            InteractionResult(
                interaction_id=interaction.id,
                state="running",
                response=None,
            ),
            send_lock,
        )
        execution = await execute_interaction(
            interaction,
            hermes_executable=hermes_executable,
            worktree=worktree,
            state_root=state_root,
            source_evidence_root=source_evidence_root,
            directed_event_sink=lambda event: send_directed_event(
                socket,
                event,
                send_lock,
            ),
        )
        if not execution.process_started:
            for event in execution.directed_events:
                await send_payload(socket, event.model_dump_json(), send_lock)
        await send_result(socket, execution.result, send_lock)


async def send_result(
    socket: DashboardSendSocket,
    result: InteractionResult,
    send_lock: anyio.Lock,
) -> None:
    await send_payload(
        socket,
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        send_lock,
    )


async def send_directed_event(
    socket: DashboardSendSocket,
    event: DirectedJobEvent,
    send_lock: anyio.Lock,
) -> None:
    try:
        await send_payload(socket, event.model_dump_json(), send_lock)
    except (OSError, TimeoutError, WebSocketException) as error:
        raise DirectedEventDeliveryError from error


async def send_payload(
    socket: DashboardSendSocket,
    payload: str,
    send_lock: anyio.Lock,
) -> None:
    async with send_lock:
        await socket.send(payload)


def pairing_url(dashboard_url: str, path: str) -> str:
    parsed = urlsplit(dashboard_url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


async def open_pairing_url(url: str) -> None:
    result = await anyio.run_process(("open", url), check=False)
    if result.returncode != 0:
        raise OSError("operator pairing browser could not be opened")


def is_reconnectable_group(error: BaseExceptionGroup[BaseException]) -> bool:
    return all(
        is_reconnectable_group(item)
        if isinstance(item, BaseExceptionGroup)
        else isinstance(item, (OSError, TimeoutError, ValidationError, WebSocketException))
        for item in error.exceptions
    )
