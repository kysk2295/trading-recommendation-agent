from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never, override

from websockets.exceptions import ConnectionClosed, InvalidHandshake

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_connection_owner import (
    AlpacaSipDynamicConnectionEvidence,
    run_alpaca_sip_dynamic_connection,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_reconnect_policy import (
    AlpacaSipDynamicReconnectDecision,
    AlpacaSipDynamicReconnectStatus,
    decide_alpaca_sip_dynamic_reconnect,
)
from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionError,
    AlpacaSipDynamicSubscriptionPlan,
)
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
from trading_agent.alpaca_sip_trade_stream import (
    AlpacaSipTradeStreamConnector,
    connect_alpaca_sip_trade_stream,
)
from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipTradeStreamError


class AlpacaSipDynamicReconnectSupervisorError(ValueError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic reconnect supervisor is invalid"


class AlpacaSipDynamicReconnectRunStatus(StrEnum):
    BOUNDED_COMPLETE = "bounded_complete"
    BLOCKED_COMPLETE = "blocked_complete"
    BLOCKED_BUDGET = "blocked_budget"
    BLOCKED_NON_RETRYABLE = "blocked_non_retryable"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicReconnectRunReport:
    status: AlpacaSipDynamicReconnectRunStatus
    attempted_this_run: int
    completed_attempts: int
    remaining_attempts: int
    connection_evidence: AlpacaSipDynamicConnectionEvidence | None

    def __post_init__(self) -> None:
        if (
            type(self.status) is not AlpacaSipDynamicReconnectRunStatus
            or self.attempted_this_run < 0
            or self.completed_attempts < self.attempted_this_run
            or self.remaining_attempts < 0
        ):
            raise AlpacaSipDynamicReconnectSupervisorError
        match self.status:
            case AlpacaSipDynamicReconnectRunStatus.BOUNDED_COMPLETE:
                valid = type(self.connection_evidence) is AlpacaSipDynamicConnectionEvidence
            case (
                AlpacaSipDynamicReconnectRunStatus.BLOCKED_COMPLETE
                | AlpacaSipDynamicReconnectRunStatus.BLOCKED_BUDGET
                | AlpacaSipDynamicReconnectRunStatus.BLOCKED_NON_RETRYABLE
            ):
                valid = self.connection_evidence is None
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise AlpacaSipDynamicReconnectSupervisorError


def run_alpaca_sip_dynamic_reconnect_supervisor(
    credentials: AlpacaCredentials,
    plan: AlpacaSipDynamicSubscriptionPlan,
    store: AlpacaSipDynamicReceiptStore,
    *,
    max_attempts: int,
    max_data_frames: int,
    timeout_seconds: float,
    connector: AlpacaSipTradeStreamConnector = connect_alpaca_sip_trade_stream,
    _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    _epoch_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> AlpacaSipDynamicReconnectRunReport:
    if (
        type(credentials) is not AlpacaCredentials
        or not credentials.key_id
        or not credentials.secret_key
        or type(store) is not AlpacaSipDynamicReceiptStore
        or type(max_data_frames) is not int
        or max_data_frames <= 0
        or type(timeout_seconds) is not float
        or timeout_seconds <= 0
    ):
        raise AlpacaSipDynamicReconnectSupervisorError
    terminals = AlpacaSipDynamicTerminalStore(store.path)
    decision = decide_alpaca_sip_dynamic_reconnect(terminals.load_history(plan), max_attempts=max_attempts)
    attempted = 0
    while decision.status is AlpacaSipDynamicReconnectStatus.READY:
        try:
            evidence = run_alpaca_sip_dynamic_connection(
                credentials,
                plan,
                store,
                max_data_frames=max_data_frames,
                timeout_seconds=timeout_seconds,
                connector=connector,
                _clock=_clock,
                _epoch_factory=_epoch_factory,
            )
        except (ConnectionClosed, InvalidHandshake, OSError, TimeoutError):
            attempted += 1
            decision = decide_alpaca_sip_dynamic_reconnect(
                terminals.load_history(plan),
                max_attempts=max_attempts,
            )
            continue
        except (AlpacaSipDynamicSubscriptionError, AlpacaSipTradeStreamError):
            attempted += 1
            decision = decide_alpaca_sip_dynamic_reconnect(
                terminals.load_history(plan),
                max_attempts=max_attempts,
            )
            return _report(
                AlpacaSipDynamicReconnectRunStatus.BLOCKED_NON_RETRYABLE,
                attempted,
                decision,
                None,
            )
        attempted += 1
        decision = decide_alpaca_sip_dynamic_reconnect(
            terminals.load_history(plan),
            max_attempts=max_attempts,
        )
        return _report(
            AlpacaSipDynamicReconnectRunStatus.BOUNDED_COMPLETE,
            attempted,
            decision,
            evidence,
        )
    return _blocked_report(attempted, decision)


def _blocked_report(
    attempted: int,
    decision: AlpacaSipDynamicReconnectDecision,
) -> AlpacaSipDynamicReconnectRunReport:
    match decision.status:
        case AlpacaSipDynamicReconnectStatus.BLOCKED_COMPLETE:
            status = AlpacaSipDynamicReconnectRunStatus.BLOCKED_COMPLETE
        case AlpacaSipDynamicReconnectStatus.BLOCKED_BUDGET:
            status = AlpacaSipDynamicReconnectRunStatus.BLOCKED_BUDGET
        case AlpacaSipDynamicReconnectStatus.READY:
            raise AlpacaSipDynamicReconnectSupervisorError
        case unreachable:
            assert_never(unreachable)
    return _report(status, attempted, decision, None)


def _report(
    status: AlpacaSipDynamicReconnectRunStatus,
    attempted: int,
    decision: AlpacaSipDynamicReconnectDecision,
    evidence: AlpacaSipDynamicConnectionEvidence | None,
) -> AlpacaSipDynamicReconnectRunReport:
    return AlpacaSipDynamicReconnectRunReport(
        status,
        attempted,
        decision.completed_attempts,
        decision.remaining_attempts,
        evidence,
    )


__all__ = (
    "AlpacaSipDynamicReconnectRunReport",
    "AlpacaSipDynamicReconnectRunStatus",
    "AlpacaSipDynamicReconnectSupervisorError",
    "run_alpaca_sip_dynamic_reconnect_supervisor",
)
