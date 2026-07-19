from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol, TypedDict, assert_never, override

from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_HEX = re.compile(r"^[0-9a-f]{64}$")


class RuntimeMinuteSupervisorError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime minute supervisor input is invalid"


class RuntimeSupervisorOperationBlockedError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "runtime supervisor operation is blocked"


class RuntimeSupervisorStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RuntimeMinuteSupervisorConfig:
    cycles: int
    interval_seconds: float


@dataclass(frozen=True, slots=True)
class RuntimeSupervisorOperationResult:
    fleet_cycle_id: str
    ready: bool


@dataclass(frozen=True, slots=True)
class RuntimeMinuteSupervisorRecord:
    attempt_id: str
    cycle_index: int
    started_at: dt.datetime
    finished_at: dt.datetime
    status: RuntimeSupervisorStatus
    reason: str | None
    fleet_cycle_id: str | None


class RuntimeSupervisorRecordWriter(Protocol):
    def append(self, record: RuntimeMinuteSupervisorRecord) -> bool: ...


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
    records: list[RuntimeMinuteSupervisorRecord] = []
    for offset in range(config.cycles):
        if shutdown_requested():
            break
        started_at = clock()
        if not _in_regular_session(started_at):
            break
        try:
            result = operation(started_at)
            if type(result) is not RuntimeSupervisorOperationResult or _HEX.fullmatch(result.fleet_cycle_id) is None:
                raise RuntimeMinuteSupervisorError
            status = RuntimeSupervisorStatus.READY if result.ready else RuntimeSupervisorStatus.BLOCKED
            reason = None if result.ready else "fleet_gate_blocked"
            fleet_cycle_id = result.fleet_cycle_id
        except RuntimeSupervisorOperationBlockedError:
            status = RuntimeSupervisorStatus.BLOCKED
            reason = "runtime_cycle_blocked"
            fleet_cycle_id = None
        finished_at = clock()
        record = build_runtime_minute_supervisor_record(
            offset + 1,
            started_at,
            finished_at,
            status,
            reason,
            fleet_cycle_id,
        )
        _ = writer.append(record)
        records.append(record)
        if offset + 1 < config.cycles:
            if shutdown_requested():
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
        or not callable(getattr(writer, "append", None))
        or not callable(shutdown_requested)
    ):
        raise RuntimeMinuteSupervisorError


def _valid_outcome(record: RuntimeMinuteSupervisorRecord) -> bool:
    match record.status:
        case RuntimeSupervisorStatus.READY:
            return record.reason is None and _valid_cycle_id(record.fleet_cycle_id)
        case RuntimeSupervisorStatus.BLOCKED:
            return record.reason in {"runtime_cycle_blocked", "fleet_gate_blocked"} and (
                record.fleet_cycle_id is None or _valid_cycle_id(record.fleet_cycle_id)
            )
        case unreachable:
            assert_never(unreachable)


def _valid_cycle_id(value: str | None) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _in_regular_session(value: dt.datetime) -> bool:
    if not _aware(value):
        raise RuntimeMinuteSupervisorError
    current = value.astimezone(NEW_YORK)
    bounds = regular_session_bounds(current.date())
    return bounds is not None and bounds[0] <= current < bounds[1]


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
