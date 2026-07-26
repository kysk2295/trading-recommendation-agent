from __future__ import annotations

import signal
from collections.abc import AsyncIterator

import anyio
import pytest

from trading_agent.dashboard_publisher_pairing import (
    PairingRequestRuntime,
    PairingRequestState,
    forward_pairing_signals,
)


class _SendSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.anyio
async def test_pairing_signals_coalesce_to_one_in_memory_ticket_request() -> None:
    # Given: an active publisher connection with no outstanding ticket request.
    socket = _SendSocket()
    pairing = PairingRequestState()

    async def signals() -> AsyncIterator[signal.Signals]:
        yield signal.SIGUSR1
        yield signal.SIGUSR1

    # When: repeated operator signals arrive before a ticket response.
    await forward_pairing_signals(signals(), PairingRequestRuntime(socket, anyio.Lock(), pairing))

    # Then: the event relay sends exactly one in-memory ticket request.
    assert socket.messages == ['{"type":"pairing_request"}']
    assert pairing.pending is True
