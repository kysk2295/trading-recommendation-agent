from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never, override

from trading_agent.alpaca_sip_trade_stream_attempts import (
    AlpacaSipConnectionFailureCode,
    AlpacaSipFailedConnectionAttempt,
)
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipStreamTerminalStatus,
    AlpacaSipTradeStreamConfig,
    AlpacaSipTradeStreamError,
    AlpacaSipTradeStreamSessionEvidence,
)
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore


class AlpacaSipTradeStreamSupervisorError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP trade stream supervisor is invalid"


class AlpacaSipSupervisorStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class AlpacaSipSupervisorStopReason(StrEnum):
    NON_RETRYABLE_FAILURE = "non_retryable_failure"
    RETRY_BUDGET_EXHAUSTED = "retry_budget_exhausted"


@dataclass(frozen=True, slots=True)
class AlpacaSipReconnectPolicy:
    max_connections_per_market_date: int
    base_backoff_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.max_connections_per_market_date) is not int
            or not 1 <= self.max_connections_per_market_date <= 3
            or type(self.base_backoff_seconds) is not float
            or not 1.0 <= self.base_backoff_seconds <= 30.0
        ):
            raise AlpacaSipTradeStreamSupervisorError


@dataclass(frozen=True, slots=True)
class AlpacaSipSupervisorResult:
    status: AlpacaSipSupervisorStatus
    stop_reason: AlpacaSipSupervisorStopReason | None
    operation_count: int
    total_connection_count: int
    final_connection_epoch: str | None
    continuity_attested: bool

    def __post_init__(self) -> None:
        if (
            type(self.status) is not AlpacaSipSupervisorStatus
            or type(self.operation_count) is not int
            or self.operation_count < 0
            or type(self.total_connection_count) is not int
            or self.total_connection_count < self.operation_count
            or type(self.continuity_attested) is not bool
            or not _valid_result(self)
        ):
            raise AlpacaSipTradeStreamSupervisorError


def run_alpaca_sip_trade_stream_supervisor(
    operation: Callable[[], str],
    config: AlpacaSipTradeStreamConfig,
    controls: AlpacaSipTradeStreamStore,
    policy: AlpacaSipReconnectPolicy,
    *,
    sleeper: Callable[[float], None],
) -> AlpacaSipSupervisorResult:
    if (
        not callable(operation)
        or type(config) is not AlpacaSipTradeStreamConfig
        or type(controls) is not AlpacaSipTradeStreamStore
        or type(policy) is not AlpacaSipReconnectPolicy
        or not callable(sleeper)
    ):
        raise AlpacaSipTradeStreamSupervisorError
    operation_count = 0
    while True:
        attempts = controls.load_connection_attempts(config)
        sessions = controls.load_session_history(config)
        total = len(attempts) + len(sessions)
        completed_epoch = _latest_completed_epoch(attempts, sessions)
        if completed_epoch is not None:
            return _ready(operation_count, total, completed_epoch)
        if total >= policy.max_connections_per_market_date:
            return _blocked(operation_count, total, AlpacaSipSupervisorStopReason.RETRY_BUDGET_EXHAUSTED)
        operation_count += 1
        try:
            epoch = operation()
        except AlpacaSipTradeStreamError as error:
            failure, total = _new_failure(controls, config, attempts, sessions)
            if not _retryable(failure, error):
                return _blocked(operation_count, total, AlpacaSipSupervisorStopReason.NON_RETRYABLE_FAILURE)
            if total >= policy.max_connections_per_market_date:
                return _blocked(operation_count, total, AlpacaSipSupervisorStopReason.RETRY_BUDGET_EXHAUSTED)
            sleeper(policy.base_backoff_seconds * 2 ** (operation_count - 1))
            continue
        current_attempts = controls.load_connection_attempts(config)
        current_sessions = controls.load_session_history(config)
        if current_attempts != attempts or current_sessions[: len(sessions)] != sessions:
            raise AlpacaSipTradeStreamSupervisorError
        added = current_sessions[len(sessions) :]
        if len(added) != 1 or added[0].connection_epoch != epoch:
            raise AlpacaSipTradeStreamSupervisorError
        evidence = added[0]
        if evidence.status is not AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE:
            raise AlpacaSipTradeStreamSupervisorError
        return _ready(operation_count, total + 1, epoch)


def _new_failure(
    controls: AlpacaSipTradeStreamStore,
    config: AlpacaSipTradeStreamConfig,
    attempts: tuple[AlpacaSipFailedConnectionAttempt, ...],
    sessions: tuple[AlpacaSipTradeStreamSessionEvidence, ...],
) -> tuple[AlpacaSipFailedConnectionAttempt | AlpacaSipTradeStreamSessionEvidence, int]:
    current_attempts = controls.load_connection_attempts(config)
    current_sessions = controls.load_session_history(config)
    if current_attempts[: len(attempts)] != attempts or current_sessions[: len(sessions)] != sessions:
        raise AlpacaSipTradeStreamSupervisorError
    added_attempts = current_attempts[len(attempts) :]
    added_sessions = current_sessions[len(sessions) :]
    if len(added_attempts) + len(added_sessions) != 1:
        raise AlpacaSipTradeStreamSupervisorError
    if added_attempts:
        return added_attempts[0], len(current_attempts) + len(current_sessions)
    failure = added_sessions[0]
    if failure.status is not AlpacaSipStreamTerminalStatus.FAILED:
        raise AlpacaSipTradeStreamSupervisorError
    return failure, len(current_attempts) + len(current_sessions)


def _retryable(
    failure: AlpacaSipFailedConnectionAttempt | AlpacaSipTradeStreamSessionEvidence,
    error: AlpacaSipTradeStreamError,
) -> bool:
    match failure:
        case AlpacaSipFailedConnectionAttempt(failure_code=code):
            return code in {
                AlpacaSipConnectionFailureCode.TRANSPORT_FAILED,
                AlpacaSipConnectionFailureCode.HANDSHAKE_FAILED,
                AlpacaSipConnectionFailureCode.PROVIDER_INTERNAL_ERROR,
            }
        case AlpacaSipTradeStreamSessionEvidence():
            return type(error) is AlpacaSipTradeStreamError
        case unreachable:
            assert_never(unreachable)


def _latest_completed_epoch(
    attempts: tuple[AlpacaSipFailedConnectionAttempt, ...],
    sessions: tuple[AlpacaSipTradeStreamSessionEvidence, ...],
) -> str | None:
    evidence = tuple((item.failed_at, None) for item in attempts) + tuple(
        (
            item.terminal_at,
            item.connection_epoch if item.status is AlpacaSipStreamTerminalStatus.BOUNDED_COMPLETE else None,
        )
        for item in sessions
    )
    return None if not evidence else max(evidence, key=lambda item: item[0])[1]


def _ready(operation_count: int, total: int, epoch: str) -> AlpacaSipSupervisorResult:
    return AlpacaSipSupervisorResult(
        AlpacaSipSupervisorStatus.READY,
        None,
        operation_count,
        total,
        epoch,
        total == 1,
    )


def _blocked(
    operation_count: int,
    total: int,
    reason: AlpacaSipSupervisorStopReason,
) -> AlpacaSipSupervisorResult:
    return AlpacaSipSupervisorResult(
        AlpacaSipSupervisorStatus.BLOCKED,
        reason,
        operation_count,
        total,
        None,
        False,
    )


def _valid_result(result: AlpacaSipSupervisorResult) -> bool:
    match result.status:
        case AlpacaSipSupervisorStatus.READY:
            return (
                result.stop_reason is None
                and type(result.final_connection_epoch) is str
                and len(result.final_connection_epoch) == 32
            )
        case AlpacaSipSupervisorStatus.BLOCKED:
            return (
                type(result.stop_reason) is AlpacaSipSupervisorStopReason
                and result.final_connection_epoch is None
                and not result.continuity_attested
            )
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "AlpacaSipReconnectPolicy",
    "AlpacaSipSupervisorResult",
    "AlpacaSipSupervisorStatus",
    "AlpacaSipSupervisorStopReason",
    "AlpacaSipTradeStreamSupervisorError",
    "run_alpaca_sip_trade_stream_supervisor",
)
