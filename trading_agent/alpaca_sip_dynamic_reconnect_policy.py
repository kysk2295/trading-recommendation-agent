from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import override

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicTerminalEvidence,
    AlpacaSipDynamicTerminalStatus,
)


class AlpacaSipDynamicReconnectError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic reconnect decision is invalid"


class AlpacaSipDynamicReconnectStatus(StrEnum):
    READY = "ready"
    BLOCKED_COMPLETE = "blocked_complete"
    BLOCKED_BUDGET = "blocked_budget"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicReconnectDecision:
    status: AlpacaSipDynamicReconnectStatus
    completed_attempts: int
    next_attempt_number: int | None
    remaining_attempts: int

    def __post_init__(self) -> None:
        if (
            type(self.status) is not AlpacaSipDynamicReconnectStatus
            or self.completed_attempts < 0
            or self.remaining_attempts < 0
            or (self.status is AlpacaSipDynamicReconnectStatus.READY)
            != (self.next_attempt_number == self.completed_attempts + 1)
        ):
            raise AlpacaSipDynamicReconnectError


def decide_alpaca_sip_dynamic_reconnect(
    history: tuple[AlpacaSipDynamicTerminalEvidence, ...],
    *,
    max_attempts: int,
) -> AlpacaSipDynamicReconnectDecision:
    if (
        type(history) is not tuple
        or any(type(item) is not AlpacaSipDynamicTerminalEvidence for item in history)
        or type(max_attempts) is not int
        or not 1 <= max_attempts <= 10
        or len(history) > max_attempts
        or len({item.connection_epoch for item in history}) != len(history)
        or len({item.plan_id for item in history}) > 1
        or history != tuple(sorted(history, key=lambda item: (item.terminal_at, item.connection_epoch)))
    ):
        raise AlpacaSipDynamicReconnectError
    complete = tuple(item for item in history if item.status is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE)
    if len(complete) > 1 or (complete and history[-1] != complete[0]):
        raise AlpacaSipDynamicReconnectError
    completed = len(history)
    remaining = max_attempts - completed
    if any(item.status is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE for item in history):
        status = AlpacaSipDynamicReconnectStatus.BLOCKED_COMPLETE
        next_attempt = None
    elif remaining == 0:
        status = AlpacaSipDynamicReconnectStatus.BLOCKED_BUDGET
        next_attempt = None
    else:
        status = AlpacaSipDynamicReconnectStatus.READY
        next_attempt = completed + 1
    return AlpacaSipDynamicReconnectDecision(status, completed, next_attempt, remaining)


__all__ = (
    "AlpacaSipDynamicReconnectDecision",
    "AlpacaSipDynamicReconnectError",
    "AlpacaSipDynamicReconnectStatus",
    "decide_alpaca_sip_dynamic_reconnect",
)
