from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import assert_never, override

from trading_agent.future_session_plan_models import (
    FutureSessionPayloadMode,
    JobTimingSpec,
)


@dataclass(frozen=True, slots=True)
class InvalidFutureSessionPayloadSpecError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def render_job_payload(job: JobTimingSpec) -> str:
    command = shlex.join(job.command)
    header = "#!/bin/zsh\n\nset -u\numask 077\n\n"
    match job.payload_mode:
        case FutureSessionPayloadMode.ONCE:
            return f"{header}exec {command}\n"
        case FutureSessionPayloadMode.REPEAT_THROUGH_DEADLINE:
            return header + _repeat_payload(job, command)
        case FutureSessionPayloadMode.RETRY_UNTIL_SUCCESS:
            return header + _retry_payload(job, command)
        case unreachable:
            assert_never(unreachable)


def _repeat_payload(job: JobTimingSpec, command: str) -> str:
    deadline_epoch, poll_seconds = _polling_values(job)
    return (
        f"readonly poll_deadline_epoch={deadline_epoch}\n"
        f"readonly poll_interval_seconds={poll_seconds}\n\n"
        f"{_deadline_guard()}"
        "last_exit_code=1\n"
        "while true; do\n"
        f"  {command}\n"
        "  last_exit_code=$?\n"
        f"{_sleep_or_finish('exit $last_exit_code')}"
        "done\n"
    )


def _retry_payload(job: JobTimingSpec, command: str) -> str:
    deadline_epoch, poll_seconds = _polling_values(job)
    not_before = (
        ""
        if job.not_before is None
        else _wait_until(int(job.not_before.timestamp()))
    )
    return (
        f"readonly poll_deadline_epoch={deadline_epoch}\n"
        f"readonly poll_interval_seconds={poll_seconds}\n"
        f"{not_before}\n"
        f"{_deadline_guard()}"
        "last_exit_code=1\n"
        "while true; do\n"
        f"  {command}\n"
        "  last_exit_code=$?\n"
        "  if (( last_exit_code == 0 )); then\n"
        "    exit 0\n"
        "  fi\n"
        f"{_sleep_or_finish('exit $last_exit_code')}"
        "done\n"
    )


def _polling_values(job: JobTimingSpec) -> tuple[int, int]:
    if job.poll_until is None or job.poll_interval_seconds is None:
        raise InvalidFutureSessionPayloadSpecError("polling_contract_missing")
    return int(job.poll_until.timestamp()), job.poll_interval_seconds


def _deadline_guard() -> str:
    return (
        "if (( $(/bin/date +%s) > poll_deadline_epoch )); then\n"
        "  print -u2 -r -- "
        "'{\"reason\":\"deadline_elapsed\",\"result\":\"blocked\"}'\n"
        "  exit 78\n"
        "fi\n\n"
    )


def _wait_until(not_before_epoch: int) -> str:
    return (
        f"readonly not_before_epoch={not_before_epoch}\n"
        "while (( $(/bin/date +%s) < not_before_epoch )); do\n"
        "  remaining=$(( not_before_epoch - $(/bin/date +%s) ))\n"
        "  sleep_seconds=$(( remaining < 60 ? remaining : 60 ))\n"
        "  /bin/sleep $sleep_seconds\n"
        "done\n"
    )


def _sleep_or_finish(finish: str) -> str:
    return (
        "  now_epoch=$(/bin/date +%s)\n"
        "  if (( now_epoch >= poll_deadline_epoch )); then\n"
        f"    {finish}\n"
        "  fi\n"
        "  remaining=$(( poll_deadline_epoch - now_epoch ))\n"
        "  sleep_seconds=$(( "
        "remaining < poll_interval_seconds ? remaining : poll_interval_seconds "
        "))\n"
        "  /bin/sleep $sleep_seconds\n"
    )


__all__ = (
    "InvalidFutureSessionPayloadSpecError",
    "render_job_payload",
)
