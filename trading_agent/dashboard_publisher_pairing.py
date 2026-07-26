from __future__ import annotations

import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, assert_never

import anyio
from anyio.abc import TaskGroup

from trading_agent.dashboard_commands import DashboardInteractionMessage, PairingTicketMessage, parse_dashboard_event
from trading_agent.dashboard_relay import pairing_url, run_interaction


class PairingRequestState:
    __slots__ = ("pending",)

    def __init__(self) -> None:
        self.pending = False


class PairingSignalSocket(Protocol):
    async def send(self, message: str) -> None: ...


class PublisherEventSocket(PairingSignalSocket, Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


BrowserOpener = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PairingRequestRuntime:
    socket: PairingSignalSocket
    send_lock: anyio.Lock
    request: PairingRequestState


@dataclass(frozen=True, slots=True)
class PairingTicketHandler:
    dashboard_url: str
    pair_browser: bool
    request: PairingRequestState
    browser_opener: BrowserOpener

    async def open(self, ticket: PairingTicketMessage) -> None:
        if self.pair_browser or self.request.pending:
            signal_requested = self.request.pending
            try:
                await self.browser_opener(pairing_url(self.dashboard_url, ticket.path))
            finally:
                if signal_requested:
                    self.request.pending = False


@dataclass(frozen=True, slots=True)
class InteractionRuntime:
    outputs: Path
    send_lock: anyio.Lock
    limiter: anyio.CapacityLimiter
    tasks: TaskGroup
    hermes_executable: Path
    worktree: Path
    interactive_state: Path

    def start(self, socket: PublisherEventSocket, event: DashboardInteractionMessage) -> None:
        self.tasks.start_soon(
            run_interaction,
            socket,
            event.interaction,
            self.send_lock,
            self.limiter,
            self.hermes_executable,
            self.worktree,
            self.interactive_state,
            self.outputs / "source_evidence",
        )


@dataclass(frozen=True, slots=True)
class PublisherEventReceiver:
    pairing: PairingTicketHandler
    interactions: InteractionRuntime


async def receive_events(socket: PublisherEventSocket, receiver: PublisherEventReceiver) -> None:
    async for raw in socket:
        if not isinstance(raw, str):
            continue
        event = parse_dashboard_event(raw)
        match event:
            case PairingTicketMessage():
                await receiver.pairing.open(event)
            case DashboardInteractionMessage():
                receiver.interactions.start(socket, event)
            case unreachable:
                assert_never(unreachable)


async def watch_pairing_signal(runtime: PairingRequestRuntime) -> None:
    with anyio.open_signal_receiver(signal.SIGUSR1) as signals:
        await forward_pairing_signals(signals, runtime)


async def forward_pairing_signals(
    signals: AsyncIterator[signal.Signals],
    runtime: PairingRequestRuntime,
) -> None:
    async for _received_signal in signals:
        if runtime.request.pending:
            continue
        runtime.request.pending = True
        async with runtime.send_lock:
            await runtime.socket.send('{"type":"pairing_request"}')


__all__ = (
    "BrowserOpener",
    "InteractionRuntime",
    "PairingRequestRuntime",
    "PairingRequestState",
    "PairingSignalSocket",
    "PairingTicketHandler",
    "PublisherEventReceiver",
    "PublisherEventSocket",
    "forward_pairing_signals",
    "receive_events",
    "watch_pairing_signal",
)
