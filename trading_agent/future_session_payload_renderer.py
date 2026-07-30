from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import assert_never, override

from trading_agent.future_session_observer_payloads import (
    render_preflight_payload,
    render_projection_payload,
)
from trading_agent.future_session_plan_models import (
    FutureSessionPayloadMode,
    FutureSessionUsRole,
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
    match job.role:
        case FutureSessionUsRole.US_HERMES_PROJECTION:
            return header + render_projection_payload(job, command)
        case FutureSessionUsRole.US_DAY_PREFLIGHT_OBSERVER:
            return header + render_preflight_payload(job, command)
        case (
            FutureSessionUsRole.US_ORB_WATCHER
            | FutureSessionUsRole.US_DAY_CLOSE_FINALIZER
            | FutureSessionUsRole.US_DAY_ARM_OBSERVER
            | None
        ):
            pass
        case unreachable:
            assert_never(unreachable)
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
    prefix = (
        f"readonly poll_deadline_epoch={deadline_epoch}\n"
        f"readonly poll_interval_seconds={poll_seconds}\n"
        f"{not_before}\n"
    )
    if job.finalizer_gate is not None:
        return prefix + _gated_finalizer_retry(job, command)
    return (
        prefix
        + _deadline_guard()
        + "last_exit_code=1\n"
        "while true; do\n"
        f"  {command}\n"
        "  last_exit_code=$?\n"
        "  if (( last_exit_code == 0 )); then\n"
        "    exit 0\n"
        "  fi\n"
        f"{_sleep_or_finish('exit $last_exit_code')}"
        "done\n"
    )


def _gated_finalizer_retry(job: JobTimingSpec, command: str) -> str:
    gate = job.finalizer_gate
    if gate is None:
        raise InvalidFutureSessionPayloadSpecError("finalizer_gate_missing")
    watcher_probe = f"{shlex.join(gate.watcher_active_probe)} >/dev/null 2>&1"
    source_path = shlex.quote(str(gate.source_path))
    return (
        f"readonly stability_seconds={gate.stability_seconds}\n"
        f"readonly watch_source_path={source_path}\n\n"
        "block_finalizer() {\n"
        '  print -u2 -r -- "{\\"reason\\":\\"$1\\",'
        '\\"result\\":\\"blocked\\"}"\n'
        "  exit 78\n"
        "}\n\n"
        "wait_for_poll_or_block() {\n"
        "  now_epoch=$(/bin/date +%s)\n"
        "  if (( now_epoch >= poll_deadline_epoch )); then\n"
        "    block_finalizer $1\n"
        "  fi\n"
        "  remaining=$(( poll_deadline_epoch - now_epoch ))\n"
        "  sleep_seconds=$(( "
        "remaining < poll_interval_seconds ? remaining : poll_interval_seconds "
        "))\n"
        "  /bin/sleep $sleep_seconds\n"
        "}\n\n"
        "blocked_reason=watcher_active\n"
        "last_exit_code=1\n"
        "while true; do\n"
        f"  if {watcher_probe}; then\n"
        "    blocked_reason=watcher_active\n"
        "    wait_for_poll_or_block $blocked_reason\n"
        "    continue\n"
        "  fi\n"
        "  if [[ ! -f $watch_source_path ]]; then\n"
        "    blocked_reason=watch_source_missing\n"
        "    wait_for_poll_or_block $blocked_reason\n"
        "    continue\n"
        "  fi\n"
        "  if ! first_source_stat=$(/usr/bin/stat -f "
        "'%d:%i:%z:%m:%c' $watch_source_path 2>/dev/null); then\n"
        "    blocked_reason=watch_source_missing\n"
        "    wait_for_poll_or_block $blocked_reason\n"
        "    continue\n"
        "  fi\n"
        "  blocked_reason=watch_source_unstable\n"
        "  now_epoch=$(/bin/date +%s)\n"
        "  remaining=$(( poll_deadline_epoch - now_epoch ))\n"
        "  if (( remaining < stability_seconds )); then\n"
        "    if (( remaining > 0 )); then\n"
        "      /bin/sleep $remaining\n"
        "    fi\n"
        "    block_finalizer $blocked_reason\n"
        "  fi\n"
        "  /bin/sleep $stability_seconds\n"
        f"  if {watcher_probe}; then\n"
        "    blocked_reason=watcher_active\n"
        "    continue\n"
        "  fi\n"
        "  if [[ ! -f $watch_source_path ]]; then\n"
        "    blocked_reason=watch_source_missing\n"
        "    continue\n"
        "  fi\n"
        "  if ! second_source_stat=$(/usr/bin/stat -f "
        "'%d:%i:%z:%m:%c' $watch_source_path 2>/dev/null); then\n"
        "    blocked_reason=watch_source_missing\n"
        "    continue\n"
        "  fi\n"
        "  if [[ $first_source_stat != $second_source_stat ]]; then\n"
        "    blocked_reason=watch_source_unstable\n"
        "    continue\n"
        "  fi\n"
        f"  if {watcher_probe}; then\n"
        "    blocked_reason=watcher_active\n"
        "    continue\n"
        "  fi\n"
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
