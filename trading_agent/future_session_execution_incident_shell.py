from __future__ import annotations

import datetime as dt
import shlex
from pathlib import Path
from typing import Literal

_FSYNC_PROGRAM = """import os
import sys

path = sys.argv[1]
file_descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
directory_descriptor = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    os.fsync(file_descriptor)
    os.fsync(directory_descriptor)
finally:
    os.close(directory_descriptor)
    os.close(file_descriptor)
"""


def render_us_execution_incident_shell(
    market: Literal["us", "kr"],
    target_session: dt.date,
    receipt: Path,
    queue_receipt: Path,
    fsync_interpreter: Path,
) -> tuple[str, str]:
    incident_format = (
        '{"completed_at_epoch":%s,"manifest_sha256":"%s","market":"%s",'
        '"plan_sha256":"%s","reason":"runtime_authority_invalid",'
        '"request_sha256":"%s","role":"%s","runtime_commit_sha":"%s",'
        '"scheduler_main_sha":"%s","schema_version":1,"target_session":"%s"}\\n'
    )
    declarations = (
        f"readonly execution_incident_receipt={shlex.quote(str(receipt))}\n"
        f"readonly execution_incident_queue_receipt={shlex.quote(str(queue_receipt))}\n"
        f"readonly execution_incident_fsync_interpreter={shlex.quote(str(fsync_interpreter))}\n"
        f"readonly market={market}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident_queue_pointer() {
  local incident_sha256
  local temporary_pointer
  temporary_pointer=$(/usr/bin/mktemp "${execution_incident_receipt}.queue.tmp.XXXXXXXX") || return 1
  incident_sha256=$(/usr/bin/shasum -a 256 "$execution_incident_receipt" | /usr/bin/awk '{print $1}')
  /usr/bin/printf \
    '{"incident_sha256":"%s","market":"%s","role":"%s","schema_version":1,"target_session":"%s"}\n' \
    "$incident_sha256" "$market" "$role" "$target_session" > "$temporary_pointer"
  /bin/chmod 600 "$temporary_pointer"
  if /bin/ln "$temporary_pointer" "$execution_incident_queue_receipt"; then
    /bin/rm -f "$temporary_pointer"
    "$execution_incident_fsync_interpreter" -c __FSYNC_PROGRAM__ \
      "$execution_incident_queue_receipt"
    return $?
  fi
  /usr/bin/cmp -s "$temporary_pointer" "$execution_incident_queue_receipt"
  local comparison=$?
  /bin/rm -f "$temporary_pointer"
  if (( comparison != 0 )); then return $comparison; fi
  "$execution_incident_fsync_interpreter" -c __FSYNC_PROGRAM__ \
    "$execution_incident_queue_receipt"
}

write_execution_incident() {
  if [[ ! -f $execution_incident_receipt ]]; then
    local temporary_incident
    temporary_incident=$(/usr/bin/mktemp "${execution_incident_receipt}.tmp.XXXXXXXX") || return 1
    local manifest_sha256
    manifest_sha256=$(/usr/bin/shasum -a 256 "$preparation_manifest" | /usr/bin/awk '{print $1}')
    /usr/bin/printf '__INCIDENT_FORMAT__' \
      "$(/bin/date +%s)" "$manifest_sha256" "$market" "$plan_sha256" "$request_sha256" \
      "$role" "$runtime_commit_sha" "$source_commit" "$target_session" > "$temporary_incident"
    /bin/chmod 600 "$temporary_incident"
    /bin/ln "$temporary_incident" "$execution_incident_receipt" || true
    /bin/rm -f "$temporary_incident"
  fi
  if [[ ! -f $execution_incident_receipt ]]; then return 1; fi
  "$execution_incident_fsync_interpreter" -c __FSYNC_PROGRAM__ \
    "$execution_incident_receipt" || return 1
  write_execution_incident_queue_pointer
}
""".replace("__INCIDENT_FORMAT__", incident_format).replace("__FSYNC_PROGRAM__", shlex.quote(_FSYNC_PROGRAM))
    return declarations, writer


def render_optional_us_execution_incident_shell(
    market: Literal["us", "kr"] | None,
    target_session: dt.date | None,
    receipt: Path | None,
    queue_receipt: Path | None,
    fsync_interpreter: Path | None,
    provenance_enabled: bool,
) -> tuple[str, str, str]:
    enabled = any(value is not None for value in (market, target_session, receipt, queue_receipt, fsync_interpreter))
    if not enabled:
        return "", "", ""
    if (
        not provenance_enabled
        or market is None
        or target_session is None
        or receipt is None
        or queue_receipt is None
        or fsync_interpreter is None
    ):
        raise ValueError
    declarations, writer = render_us_execution_incident_shell(
        market,
        target_session,
        receipt,
        queue_receipt,
        fsync_interpreter,
    )
    call = """  if ! write_execution_incident; then
    print -u2 -r -- '{"reason":"execution_incident_publication_failed","result":"retryable"}'
    exit 75
  fi
"""
    return declarations, writer, call


def render_kr_execution_incident_shell(
    target_session: dt.date,
    receipt: Path,
    queue_receipt: Path,
    fsync_interpreter: Path,
    manifest: Path,
    request_sha256: str,
    plan_sha256: str,
    scheduler_main_sha: str,
    runtime_commit_sha: str,
) -> tuple[str, str]:
    incident_format = (
        '{"completed_at_epoch":%s,"manifest_sha256":"%s","market":"kr",'
        '"plan_sha256":"%s","reason":"runtime_authority_invalid",'
        '"request_sha256":"%s","role":"kr_supervisor","runtime_commit_sha":"%s",'
        '"scheduler_main_sha":"%s","schema_version":1,"target_session":"%s"}\\n'
    )
    declarations = (
        f"readonly incident_receipt={shlex.quote(str(receipt))}\n"
        f"readonly incident_queue_receipt={shlex.quote(str(queue_receipt))}\n"
        f"readonly incident_fsync_interpreter={shlex.quote(str(fsync_interpreter))}\n"
        f"readonly manifest={shlex.quote(str(manifest))}\n"
        f"readonly request_sha256={request_sha256}\n"
        f"readonly plan_sha256={plan_sha256}\n"
        f"readonly scheduler_main_sha={scheduler_main_sha}\n"
        f"readonly runtime_commit_sha={runtime_commit_sha}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident_queue_pointer() {
  local incident_sha256
  local temporary_pointer
  temporary_pointer=$(/usr/bin/mktemp "${incident_receipt}.queue.tmp.XXXXXXXX") || return 1
  incident_sha256=$(/usr/bin/shasum -a 256 "$incident_receipt" | /usr/bin/awk '{print $1}')
  /usr/bin/printf \
    '{"incident_sha256":"%s","market":"kr","role":"kr_supervisor","schema_version":1,"target_session":"%s"}\n' \
    "$incident_sha256" "$target_session" > "$temporary_pointer"
  /bin/chmod 600 "$temporary_pointer"
  if /bin/ln "$temporary_pointer" "$incident_queue_receipt"; then
    /bin/rm -f "$temporary_pointer"
    "$incident_fsync_interpreter" -c __FSYNC_PROGRAM__ "$incident_queue_receipt"
    return $?
  fi
  /usr/bin/cmp -s "$temporary_pointer" "$incident_queue_receipt"
  local comparison=$?
  /bin/rm -f "$temporary_pointer"
  if (( comparison != 0 )); then return $comparison; fi
  "$incident_fsync_interpreter" -c __FSYNC_PROGRAM__ "$incident_queue_receipt"
}

write_execution_incident() {
  if [[ ! -f $incident_receipt ]]; then
    local temporary_incident
    temporary_incident=$(/usr/bin/mktemp "${incident_receipt}.tmp.XXXXXXXX") || return 1
    local manifest_sha256
    manifest_sha256=$(/usr/bin/shasum -a 256 "$manifest" | /usr/bin/awk '{print $1}')
    /usr/bin/printf '__INCIDENT_FORMAT__' \
      "$(/bin/date +%s)" "$manifest_sha256" "$plan_sha256" "$request_sha256" \
      "$runtime_commit_sha" "$scheduler_main_sha" "$target_session" > "$temporary_incident"
    /bin/chmod 600 "$temporary_incident"
    /bin/ln "$temporary_incident" "$incident_receipt" || true
    /bin/rm -f "$temporary_incident"
  fi
  if [[ ! -f $incident_receipt ]]; then return 1; fi
  "$incident_fsync_interpreter" -c __FSYNC_PROGRAM__ "$incident_receipt" || return 1
  write_execution_incident_queue_pointer
}
""".replace("__INCIDENT_FORMAT__", incident_format).replace("__FSYNC_PROGRAM__", shlex.quote(_FSYNC_PROGRAM))
    return declarations, writer


__all__ = (
    "render_kr_execution_incident_shell",
    "render_optional_us_execution_incident_shell",
    "render_us_execution_incident_shell",
)
