from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from trading_agent.private_immutable_file import read_private_text

Clock = Callable[[], dt.datetime]
Sleeper = Callable[[float], None]

_SUCCESS_RECEIPT: Final = re.compile(r"exit_code=0\ncompleted_at_epoch=[1-9][0-9]*\n")
_STRICT_CLOSEOUT_RESULTS: Final = frozenset(
    {
        "- result: recovered",
        "- result: replayed",
    }
)
_STRICT_CLOSEOUT_MARKERS: Final = (
    "- failed cycle deletion: 0",
    "- quality gate relaxed: false",
    "- provider, credential, account, or order operation: 0",
)
_MINIMUM_WATCH_CYCLES: Final = 300
_MAXIMUM_WATCH_CYCLES: Final = 390
_POLL_SECONDS: Final = 1.0
_CYCLE_COUNT_LABELS: Final = (
    "watch cycles",
    "ranking cycles",
    "retry cycles",
    "candidate input cycles",
)


class CloseoutPrerequisiteError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class CloseoutPrerequisiteRequest:
    receipt: Path | None
    report: Path | None
    wait_until: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class CloseoutPrerequisiteRuntime:
    clock: Clock
    sleeper: Sleeper


_DEFAULT_RUNTIME: Final = CloseoutPrerequisiteRuntime(
    clock=lambda: dt.datetime.now(dt.UTC),
    sleeper=time.sleep,
)


def require_closeout_prerequisite(
    request: CloseoutPrerequisiteRequest,
    *,
    runtime: CloseoutPrerequisiteRuntime = _DEFAULT_RUNTIME,
) -> None:
    """Wait boundedly for and validate one strict closeout prerequisite."""
    receipt = request.receipt
    report = request.report
    if (receipt is None) != (report is None):
        raise CloseoutPrerequisiteError("prerequisite_paths_incomplete")
    if receipt is None or report is None:
        if request.wait_until is not None:
            raise CloseoutPrerequisiteError("prerequisite_wait_without_paths")
        return
    _wait_for_publication(request, runtime)
    receipt_payload = read_private_text(receipt)
    report_lines = read_private_text(report).splitlines()
    results = tuple(line for line in report_lines if line.startswith("- result: "))
    if (
        _SUCCESS_RECEIPT.fullmatch(receipt_payload) is None
        or len(results) != 1
        or results[0] not in _STRICT_CLOSEOUT_RESULTS
        or any(report_lines.count(marker) != 1 for marker in _STRICT_CLOSEOUT_MARKERS)
        or not _strict_cycle_contract(report_lines)
    ):
        raise CloseoutPrerequisiteError("closeout_prerequisite_invalid")


def _wait_for_publication(
    request: CloseoutPrerequisiteRequest,
    runtime: CloseoutPrerequisiteRuntime,
) -> None:
    wait_until = request.wait_until
    if wait_until is None:
        return
    if wait_until.tzinfo is None or wait_until.utcoffset() is None:
        raise CloseoutPrerequisiteError("prerequisite_wait_timezone_required")
    receipt = request.receipt
    report = request.report
    if receipt is None or report is None:
        raise CloseoutPrerequisiteError("prerequisite_wait_without_paths")
    while not (receipt.exists() and report.exists()):
        remaining = (wait_until - runtime.clock()).total_seconds()
        if remaining <= 0:
            raise CloseoutPrerequisiteError("closeout_prerequisite_timeout")
        runtime.sleeper(min(_POLL_SECONDS, remaining))


def _strict_cycle_contract(lines: list[str]) -> bool:
    minimum = _single_report_integer(lines, "minimum watch cycles")
    counts = tuple(_single_report_integer(lines, label) for label in _CYCLE_COUNT_LABELS)
    if minimum is None or any(value is None for value in counts):
        return False
    complete_counts = tuple(value for value in counts if value is not None)
    return (
        _MINIMUM_WATCH_CYCLES <= minimum <= _MAXIMUM_WATCH_CYCLES
        and len(set(complete_counts)) == 1
        and minimum <= complete_counts[0] <= _MAXIMUM_WATCH_CYCLES
    )


def _single_report_integer(lines: list[str], label: str) -> int | None:
    prefix = f"- {label}: "
    values = tuple(line.removeprefix(prefix) for line in lines if line.startswith(prefix))
    if len(values) != 1 or not values[0].isdigit():
        return None
    return int(values[0])


__all__ = (
    "CloseoutPrerequisiteError",
    "CloseoutPrerequisiteRequest",
    "CloseoutPrerequisiteRuntime",
    "require_closeout_prerequisite",
)
