from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol, TypedDict

from trading_agent.us_equity_calendar import NEW_YORK
from trading_agent.us_runtime_minute_supervisor_models import (
    RuntimeMinuteSupervisorConfig,
    RuntimeMinuteSupervisorError,
    RuntimeMinuteSupervisorRecord,
    RuntimeSupervisorOperationBlockedError,
    RuntimeSupervisorOperationResult,
    RuntimeSupervisorStatus,
)
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveAudit,
    build_runtime_supervisor_live_audit,
)
from trading_agent.us_runtime_supervisor_outcome import (
    runtime_supervisor_operation_is_valid,
    runtime_supervisor_outcome_is_valid,
)
from trading_agent.us_runtime_supervisor_session import runtime_supervisor_session_is_open

_HEX = re.compile(r"^[0-9a-f]{64}$")


class RuntimeSupervisorRecordWriter(Protocol):
    def append_attempt(
        self,
        record: RuntimeMinuteSupervisorRecord,
        live_audit: RuntimeSupervisorLiveAudit,
    ) -> bool: ...

    def records(self) -> tuple[RuntimeMinuteSupervisorRecord, ...]: ...


class _RecordPayload(TypedDict):
    attempt_id: str
    cycle_index: int
    finished_at: str
    fleet_cycle_id: str | None
    reason: str | None
    started_at: str
    status: str


def run_runtime_minute_supervisor(
    operation: Callable[[dt.datetime], RuntimeSupervisorOperationResult],
    config: RuntimeMinuteSupervisorConfig,
    *,
    clock: Callable[[], dt.datetime],
    sleeper: Callable[[float], None],
    writer: RuntimeSupervisorRecordWriter,
    shutdown_requested: Callable[[], bool] = lambda: False,
) -> tuple[RuntimeMinuteSupervisorRecord, ...]:
    _validate_runtime(operation, config, clock, sleeper, writer, shutdown_requested)
    history = _validated_history(writer)
    records: list[RuntimeMinuteSupervisorRecord] = []
    for _offset in range(config.cycles):
        if shutdown_requested():
            break
        started_at = clock()
        session_is_open = runtime_supervisor_session_is_open(started_at)
        if session_is_open is None:
            raise RuntimeMinuteSupervisorError
        if not session_is_open:
            break
        used_cycles = _used_cycles(history, started_at)
        if used_cycles >= config.cycles:
            break
        try:
            result = operation(started_at)
            if type(result) is not RuntimeSupervisorOperationResult or not runtime_supervisor_operation_is_valid(
                result.fleet_cycle_id, result.live_outcome
            ):
                raise RuntimeMinuteSupervisorError
            status = RuntimeSupervisorStatus.READY if result.ready else RuntimeSupervisorStatus.BLOCKED
            reason = None if result.ready else "fleet_gate_blocked"
            fleet_cycle_id = result.fleet_cycle_id
            live_outcome = result.live_outcome
        except RuntimeSupervisorOperationBlockedError as blocked:
            status = RuntimeSupervisorStatus.BLOCKED
            reason = "runtime_cycle_blocked"
            fleet_cycle_id = None
            live_outcome = blocked.live_outcome
        finished_at = clock()
        record = build_runtime_minute_supervisor_record(
            used_cycles + 1,
            started_at,
            finished_at,
            status,
            reason,
            fleet_cycle_id,
        )
        live_audit = build_runtime_supervisor_live_audit(record.attempt_id, live_outcome)
        if not writer.append_attempt(record, live_audit):
            raise RuntimeMinuteSupervisorError
        records.append(record)
        history += (record,)
        if record.cycle_index >= config.cycles or shutdown_requested():
            break
        sleeper(config.interval_seconds)
    return tuple(records)


def build_runtime_minute_supervisor_record(
    cycle_index: int,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    status: RuntimeSupervisorStatus,
    reason: str | None,
    fleet_cycle_id: str | None,
) -> RuntimeMinuteSupervisorRecord:
    provisional = RuntimeMinuteSupervisorRecord(
        "0" * 64,
        cycle_index,
        started_at,
        finished_at,
        status,
        reason,
        fleet_cycle_id,
    )
    record = replace(provisional, attempt_id=_record_sha256(provisional))
    validate_runtime_minute_supervisor_record(record)
    return record


def validate_runtime_minute_supervisor_record(record: RuntimeMinuteSupervisorRecord) -> None:
    if (
        type(record) is not RuntimeMinuteSupervisorRecord
        or _HEX.fullmatch(record.attempt_id) is None
        or type(record.cycle_index) is not int
        or not 1 <= record.cycle_index <= 390
        or not _aware(record.started_at)
        or not _aware(record.finished_at)
        or record.finished_at < record.started_at
        or type(record.status) is not RuntimeSupervisorStatus
        or not _valid_outcome(record)
        or record.attempt_id != _record_sha256(record)
    ):
        raise RuntimeMinuteSupervisorError


def record_bytes(record: RuntimeMinuteSupervisorRecord) -> bytes:
    validate_runtime_minute_supervisor_record(record)
    return _canonical_bytes(_payload(record)) + b"\n"


def record_from_bytes(value: bytes) -> RuntimeMinuteSupervisorRecord:
    try:
        payload = json.loads(value)
        if type(payload) is not dict or set(payload) != _RECORD_KEYS:
            raise RuntimeMinuteSupervisorError
        record = RuntimeMinuteSupervisorRecord(
            payload["attempt_id"],
            payload["cycle_index"],
            dt.datetime.fromisoformat(payload["started_at"]),
            dt.datetime.fromisoformat(payload["finished_at"]),
            RuntimeSupervisorStatus(payload["status"]),
            payload["reason"],
            payload["fleet_cycle_id"],
        )
        validate_runtime_minute_supervisor_record(record)
        if record_bytes(record) != value:
            raise RuntimeMinuteSupervisorError
        return record
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RuntimeMinuteSupervisorError from None


def _validate_runtime(operation, config, clock, sleeper, writer, shutdown_requested) -> None:
    if (
        not callable(operation)
        or type(config) is not RuntimeMinuteSupervisorConfig
        or type(config.cycles) is not int
        or not 1 <= config.cycles <= 390
        or type(config.interval_seconds) is not float
        or not 1.0 <= config.interval_seconds <= 3600.0
        or not callable(clock)
        or not callable(sleeper)
        or not callable(getattr(writer, "append_attempt", None))
        or not callable(getattr(writer, "records", None))
        or not callable(shutdown_requested)
    ):
        raise RuntimeMinuteSupervisorError


def _validated_history(writer: RuntimeSupervisorRecordWriter) -> tuple[RuntimeMinuteSupervisorRecord, ...]:
    records = writer.records()
    if type(records) is not tuple:
        raise RuntimeMinuteSupervisorError
    counts: dict[dt.date, int] = {}
    previous: dt.datetime | None = None
    for record in records:
        validate_runtime_minute_supervisor_record(record)
        market_date = record.started_at.astimezone(NEW_YORK).date()
        expected = counts.get(market_date, 0) + 1
        if record.cycle_index != expected or (previous is not None and record.started_at < previous):
            raise RuntimeMinuteSupervisorError
        counts[market_date] = expected
        previous = record.started_at
    return records


def _used_cycles(records: tuple[RuntimeMinuteSupervisorRecord, ...], started_at: dt.datetime) -> int:
    if records and records[-1].started_at > started_at:
        raise RuntimeMinuteSupervisorError
    market_date = started_at.astimezone(NEW_YORK).date()
    return sum(record.started_at.astimezone(NEW_YORK).date() == market_date for record in records)


def _valid_outcome(record: RuntimeMinuteSupervisorRecord) -> bool:
    return runtime_supervisor_outcome_is_valid(
        record.status.value,
        record.reason,
        record.fleet_cycle_id,
    )


def _payload(record: RuntimeMinuteSupervisorRecord) -> _RecordPayload:
    return {
        "attempt_id": record.attempt_id,
        "cycle_index": record.cycle_index,
        "finished_at": record.finished_at.isoformat(),
        "fleet_cycle_id": record.fleet_cycle_id,
        "reason": record.reason,
        "started_at": record.started_at.isoformat(),
        "status": record.status.value,
    }


def _record_sha256(record: RuntimeMinuteSupervisorRecord) -> str:
    payload = _payload(record)
    payload["attempt_id"] = ""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_bytes(value: _RecordPayload) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


_RECORD_KEYS = {"attempt_id", "cycle_index", "finished_at", "fleet_cycle_id", "reason", "started_at", "status"}


__all__ = (
    "RuntimeMinuteSupervisorConfig",
    "RuntimeMinuteSupervisorError",
    "RuntimeMinuteSupervisorRecord",
    "RuntimeSupervisorOperationBlockedError",
    "RuntimeSupervisorOperationResult",
    "RuntimeSupervisorStatus",
    "record_bytes",
    "record_from_bytes",
    "run_runtime_minute_supervisor",
)
