from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Protocol

from trading_agent.alpaca_sip_trade_stream_models import AlpacaSipTradeStreamConfig
from trading_agent.alpaca_sip_trade_stream_store import AlpacaSipTradeStreamStore
from trading_agent.alpaca_sip_trade_stream_supervisor import (
    AlpacaSipReconnectPolicy,
    AlpacaSipSupervisorResult,
    AlpacaSipSupervisorStatus,
    AlpacaSipSupervisorStopReason,
    AlpacaSipTradeStreamSupervisorError,
    run_alpaca_sip_trade_stream_supervisor,
)
from trading_agent.alpaca_sip_trade_stream_supervisor_audit_models import (
    AlpacaSipSupervisorAuditEvent,
    AlpacaSipSupervisorAuditKind,
    build_audit_event,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AlpacaSipSupervisorAuditWriter(Protocol):
    def append(self, event: AlpacaSipSupervisorAuditEvent) -> bool: ...

    def events(self, run_id: str) -> tuple[AlpacaSipSupervisorAuditEvent, ...]: ...


def run_audited_alpaca_sip_trade_stream_supervisor(
    operation: Callable[[], str],
    config: AlpacaSipTradeStreamConfig,
    controls: AlpacaSipTradeStreamStore,
    policy: AlpacaSipReconnectPolicy,
    *,
    run_id: str,
    audit_store: AlpacaSipSupervisorAuditWriter,
    clock: Callable[[], dt.datetime],
    sleeper: Callable[[float], None],
    shutdown_requested: Callable[[], bool],
) -> AlpacaSipSupervisorResult:
    if (
        not callable(operation)
        or type(config) is not AlpacaSipTradeStreamConfig
        or type(controls) is not AlpacaSipTradeStreamStore
        or type(policy) is not AlpacaSipReconnectPolicy
        or _HEX64.fullmatch(run_id) is None
        or not callable(getattr(audit_store, "append", None))
        or not callable(getattr(audit_store, "events", None))
        or not callable(clock)
        or not callable(sleeper)
        or not callable(shutdown_requested)
    ):
        raise AlpacaSipTradeStreamSupervisorError
    if audit_store.events(run_id):
        raise AlpacaSipTradeStreamSupervisorError
    recorder = _AuditRecorder(run_id, audit_store, clock)
    recorder.append(AlpacaSipSupervisorAuditKind.STARTED, 0, _total(controls, config))
    operation_count = 0

    def audited_operation() -> str:
        nonlocal operation_count
        operation_count += 1
        recorder.append(AlpacaSipSupervisorAuditKind.CONNECTING, operation_count, _total(controls, config))
        return operation()

    def audited_sleeper(delay: float) -> None:
        recorder.append(
            AlpacaSipSupervisorAuditKind.RETRY_SCHEDULED,
            operation_count,
            _total(controls, config),
            retry_delay_seconds=delay,
        )
        sleeper(delay)

    result = run_alpaca_sip_trade_stream_supervisor(
        audited_operation,
        config,
        controls,
        policy,
        sleeper=audited_sleeper,
        shutdown_requested=shutdown_requested,
    )
    recorder.append(
        _terminal_kind(result.status),
        result.operation_count,
        result.total_connection_count,
        status=result.status,
        stop_reason=result.stop_reason,
    )
    return result


class _AuditRecorder:
    __slots__ = ("_clock", "_previous", "_run_id", "_sequence", "_store")

    def __init__(
        self,
        run_id: str,
        store: AlpacaSipSupervisorAuditWriter,
        clock: Callable[[], dt.datetime],
    ) -> None:
        self._run_id = run_id
        self._store = store
        self._clock = clock
        self._sequence = 0
        self._previous: str | None = None

    def append(
        self,
        kind: AlpacaSipSupervisorAuditKind,
        operation_count: int,
        total: int,
        *,
        retry_delay_seconds: float | None = None,
        status: AlpacaSipSupervisorStatus | None = None,
        stop_reason: AlpacaSipSupervisorStopReason | None = None,
    ) -> None:
        self._sequence += 1
        event = build_audit_event(
            self._run_id,
            self._sequence,
            self._previous,
            self._clock(),
            kind,
            operation_count,
            total,
            retry_delay_seconds=retry_delay_seconds,
            status=status,
            stop_reason=stop_reason,
        )
        if not self._store.append(event):
            raise AlpacaSipTradeStreamSupervisorError
        self._previous = event.event_id


def _terminal_kind(status: AlpacaSipSupervisorStatus) -> AlpacaSipSupervisorAuditKind:
    return AlpacaSipSupervisorAuditKind(status.value)


def _total(controls: AlpacaSipTradeStreamStore, config: AlpacaSipTradeStreamConfig) -> int:
    return len(controls.load_connection_attempts(config)) + len(controls.load_session_history(config))


__all__ = (
    "AlpacaSipSupervisorAuditEvent",
    "AlpacaSipSupervisorAuditKind",
    "AlpacaSipSupervisorAuditWriter",
    "run_audited_alpaca_sip_trade_stream_supervisor",
)
