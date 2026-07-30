from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import override

from trading_agent.future_session_plan_models import JobTimingSpec

_STAT_FORMAT = "'%d:%i:%z:%m:%c'"


@dataclass(frozen=True, slots=True)
class InvalidObserverPayloadSpecError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


def render_projection_payload(job: JobTimingSpec, command: str) -> str:
    deadline_epoch, poll_seconds = _polling_values(job)
    if len(job.source_paths) != 2:
        raise InvalidObserverPayloadSpecError("projection_sources_invalid")
    opportunities, signals = (shlex.quote(str(path)) for path in job.source_paths)
    return (
        f"readonly poll_deadline_epoch={deadline_epoch}\n"
        f"readonly poll_interval_seconds={poll_seconds}\n"
        f"readonly opportunity_outbox={opportunities}\n"
        f"readonly signal_outbox={signals}\n\n"
        "projected_signature=\n"
        "pending_exit_code=0\n"
        "while true; do\n"
        "  now_epoch=$(/bin/date +%s)\n"
        "  if (( now_epoch >= poll_deadline_epoch )); then\n"
        "    if (( pending_exit_code != 0 )); then\n"
        "      exit $pending_exit_code\n"
        "    fi\n"
        "    exit 0\n"
        "  fi\n"
        "  if [[ -f $opportunity_outbox ]] && "
        f"opportunity_stat=$(/usr/bin/stat -f {_STAT_FORMAT} "
        "$opportunity_outbox 2>/dev/null); then\n"
        "    signal_stat=missing\n"
        "    if [[ -f $signal_outbox ]]; then\n"
        f"      signal_stat=$(/usr/bin/stat -f {_STAT_FORMAT} "
        "$signal_outbox 2>/dev/null) || signal_stat=unreadable\n"
        "    fi\n"
        '    source_signature="${opportunity_stat}|${signal_stat}"\n'
        "    if [[ $source_signature != $projected_signature ]]; then\n"
        f"      {command}\n"
        "      last_exit_code=$?\n"
        "      if (( last_exit_code == 0 )); then\n"
        "        projected_signature=$source_signature\n"
        "        pending_exit_code=0\n"
        "      else\n"
        "        pending_exit_code=$last_exit_code\n"
        "      fi\n"
        "    fi\n"
        "  fi\n"
        f"{_poll_sleep()}"
        "done\n"
    )


def render_preflight_payload(job: JobTimingSpec, command: str) -> str:
    deadline_epoch, poll_seconds = _polling_values(job)
    if len(job.source_paths) != 1:
        raise InvalidObserverPayloadSpecError("preflight_source_invalid")
    watch_database = shlex.quote(str(job.source_paths[0]))
    return (
        f"readonly poll_deadline_epoch={deadline_epoch}\n"
        f"readonly poll_interval_seconds={poll_seconds}\n"
        f"readonly watch_database={watch_database}\n\n"
        "observed_signature=\n"
        "while true; do\n"
        "  now_epoch=$(/bin/date +%s)\n"
        "  if (( now_epoch >= poll_deadline_epoch )); then\n"
        "    print -r -- "
        "'{\"reason\":\"no_ready_current_setup\",\"result\":\"censored\"}'\n"
        "    exit 0\n"
        "  fi\n"
        "  if [[ -f $watch_database ]] && "
        f"watch_stat=$(/usr/bin/stat -f {_STAT_FORMAT} "
        "$watch_database 2>/dev/null); then\n"
        "    if [[ $watch_stat != $observed_signature ]]; then\n"
        "      observed_signature=$watch_stat\n"
        f"      {command}\n"
        "      last_exit_code=$?\n"
        "      if (( last_exit_code == 0 )); then\n"
        "        exit 0\n"
        "      fi\n"
        "      if (( last_exit_code > 1 )); then\n"
        "        exit $last_exit_code\n"
        "      fi\n"
        "    fi\n"
        "  fi\n"
        f"{_poll_sleep()}"
        "done\n"
    )


def _polling_values(job: JobTimingSpec) -> tuple[int, int]:
    if job.poll_until is None or job.poll_interval_seconds is None:
        raise InvalidObserverPayloadSpecError("polling_contract_missing")
    return int(job.poll_until.timestamp()), job.poll_interval_seconds


def _poll_sleep() -> str:
    return (
        "  now_epoch=$(/bin/date +%s)\n"
        "  remaining=$(( poll_deadline_epoch - now_epoch ))\n"
        "  if (( remaining > 0 )); then\n"
        "    sleep_seconds=$(( "
        "remaining < poll_interval_seconds ? remaining : poll_interval_seconds "
        "))\n"
        "    /bin/sleep $sleep_seconds\n"
        "  fi\n"
    )


__all__ = (
    "InvalidObserverPayloadSpecError",
    "render_preflight_payload",
    "render_projection_payload",
)
