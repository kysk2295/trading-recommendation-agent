from __future__ import annotations

import datetime as dt
import shlex
from dataclasses import dataclass
from pathlib import Path

from trading_agent.future_session_execution_incident_shell import (
    render_kr_execution_incident_shell,
)


@dataclass(frozen=True, slots=True)
class KrSupervisorPayloadSpec:
    interpreter: Path
    current_main_entrypoint: Path
    manifest: Path
    phase_epochs: tuple[int, int, int, int, int, int]
    request_sha256: str
    plan_sha256: str
    ledger_identity_sha256: str
    rollover_bundle_sha256: str
    policy_sha256: str


@dataclass(frozen=True, slots=True)
class KrRestartableRunnerSpec:
    label: str
    run_epoch: int
    expires_epoch: int
    receipt: Path
    command: tuple[str, ...]
    persistent_plist: Path
    target_session: dt.date | None = None
    incident_receipt: Path | None = None
    incident_queue_receipt: Path | None = None
    incident_fsync_interpreter: Path | None = None
    manifest: Path | None = None
    request_sha256: str | None = None
    plan_sha256: str | None = None
    scheduler_main_sha: str | None = None
    runtime_commit_sha: str | None = None


def render_kr_supervisor_payload(spec: KrSupervisorPayloadSpec) -> str:
    epochs = " ".join(str(epoch) for epoch in spec.phase_epochs)
    command = shlex.join(
        (
            str(spec.interpreter),
            str(spec.current_main_entrypoint),
            "supervise-kr",
            "--manifest",
            str(spec.manifest),
        )
    )
    return f"""#!/bin/zsh

set -u
umask 077

readonly request_sha256={spec.request_sha256}
readonly plan_sha256={spec.plan_sha256}
readonly ledger_identity_sha256={spec.ledger_identity_sha256}
readonly rollover_bundle_sha256={spec.rollover_bundle_sha256}
readonly policy_sha256={spec.policy_sha256}
readonly -a internal_phase_epochs=({epochs})

if (( ${{#internal_phase_epochs}} != 6 )); then
  print -u2 -r -- '{{"reason":"invalid_internal_phase_count","result":"blocked"}}'
  exit 78
fi

exec {command}
"""


def render_kr_restartable_runner(spec: KrRestartableRunnerSpec) -> str:
    command = shlex.join(spec.command)
    incident_provenance = (
        spec.target_session,
        spec.incident_receipt,
        spec.incident_queue_receipt,
        spec.incident_fsync_interpreter,
        spec.manifest,
        spec.request_sha256,
        spec.plan_sha256,
        spec.scheduler_main_sha,
        spec.runtime_commit_sha,
    )
    incident_enabled = any(value is not None for value in incident_provenance)
    if incident_enabled and any(value is None for value in incident_provenance):
        raise ValueError
    if incident_enabled:
        if (
            spec.target_session is None
            or spec.incident_receipt is None
            or spec.incident_queue_receipt is None
            or spec.incident_fsync_interpreter is None
            or spec.manifest is None
            or spec.request_sha256 is None
            or spec.plan_sha256 is None
            or spec.scheduler_main_sha is None
            or spec.runtime_commit_sha is None
        ):
            raise ValueError
        incident_declarations, incident_writer = render_kr_execution_incident_shell(
            spec.target_session,
            spec.incident_receipt,
            spec.incident_queue_receipt,
            spec.incident_fsync_interpreter,
            spec.manifest,
            spec.request_sha256,
            spec.plan_sha256,
            spec.scheduler_main_sha,
            spec.runtime_commit_sha,
        )
        incident_failure = """if (( exit_code == 78 )); then
  if ! write_execution_incident; then
    print -u2 -r -- '{"reason":"execution_incident_publication_failed","result":"retryable"}'
    exit 75
  fi
  write_receipt blocked
  cleanup_job
fi
"""
    else:
        incident_declarations = ""
        incident_writer = ""
        incident_failure = ""
    return f"""#!/bin/zsh

set -u
umask 077

readonly job_label={shlex.quote(spec.label)}
readonly run_epoch={spec.run_epoch}
readonly expires_epoch={spec.expires_epoch}
readonly receipt={shlex.quote(str(spec.receipt))}
readonly claim={shlex.quote(f"{spec.receipt}.claim")}
readonly persistent_plist={shlex.quote(str(spec.persistent_plist))}
{incident_declarations}

cleanup_job() {{
  /bin/launchctl remove $job_label >/dev/null 2>&1 || true
  /bin/rm -f $persistent_plist
}}

write_receipt() {{
  local result=$1
  local temporary_receipt="${{receipt}}.tmp.$$"
  /usr/bin/printf '{{"completed_at_epoch":%s,"result":"%s","schema_version":1}}\n' \
    "$(/bin/date +%s)" "$result" > $temporary_receipt
  /bin/chmod 600 $temporary_receipt
  /bin/mv -f $temporary_receipt $receipt
}}
{incident_writer}

if [[ -f $receipt ]]; then
  cleanup_job
  exit 0
fi
while (( $(/bin/date +%s) < run_epoch )); do /bin/sleep 30; done
if (( $(/bin/date +%s) > expires_epoch )); then
  write_receipt expired
  cleanup_job
  exit 0
fi
/bin/rmdir $claim 2>/dev/null || true
if ! /bin/mkdir $claim 2>/dev/null; then exit 75; fi
/bin/chmod 700 $claim
{command}
exit_code=$?
/bin/rmdir $claim 2>/dev/null || true
{incident_failure}\
if (( exit_code == 0 )); then
  write_receipt terminal
  cleanup_job
fi
exit $exit_code
"""


__all__ = (
    "KrRestartableRunnerSpec",
    "KrSupervisorPayloadSpec",
    "render_kr_restartable_runner",
    "render_kr_supervisor_payload",
)
