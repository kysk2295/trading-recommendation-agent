from __future__ import annotations

import datetime as dt
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from trading_agent.future_session_execution_incident_shell import (
    render_optional_us_execution_incident_shell,
)


@dataclass(frozen=True, slots=True)
class OneShotRunnerSpec:
    label: str
    run_at: dt.datetime
    receipt: Path
    command: tuple[str, ...]
    expires_at: dt.datetime | None = None
    persistent_plist: Path | None = None
    authority_repository: Path | None = None
    source_commit: str | None = None
    role: str | None = None
    request_sha256: str | None = None
    plan_sha256: str | None = None
    runtime_commit_sha: str | None = None
    runtime_attestation_sha256: str | None = None
    preparation_manifest: Path | None = None
    authority_mode: Literal["current_main", "frozen_runtime"] = "current_main"
    market: Literal["us", "kr"] | None = None
    target_session: dt.date | None = None
    execution_incident_receipt: Path | None = None


def render_runner(spec: OneShotRunnerSpec) -> str:
    label = shlex.quote(spec.label)
    receipt = shlex.quote(str(spec.receipt))
    claim = shlex.quote(f"{spec.receipt}.claim")
    command = shlex.join(spec.command)
    run_epoch = int(spec.run_at.timestamp())
    return f"""#!/bin/zsh

set -u
umask 077

readonly job_label={label}
readonly run_epoch={run_epoch}
readonly receipt={receipt}
readonly claim={claim}

if [[ -f $receipt ]]; then
  /bin/launchctl remove $job_label >/dev/null 2>&1 || true
  exit 0
fi

while (( $(/bin/date +%s) < run_epoch )); do
  remaining=$(( run_epoch - $(/bin/date +%s) ))
  if (( remaining > 60 )); then
    /bin/sleep 60
  else
    /bin/sleep $remaining
  fi
done

if ! /bin/mkdir $claim 2>/dev/null; then
  print -u2 -r -- '{{"reason":"already_claimed","result":"blocked"}}'
  /bin/launchctl remove $job_label >/dev/null 2>&1 || true
  exit 75
fi
/bin/chmod 700 $claim

finalize() {{
  local exit_code=$?
  local temporary_receipt="${{receipt}}.tmp.$$"
  trap - EXIT
  /usr/bin/printf 'exit_code=%d\\ncompleted_at_epoch=%s\\n' \\
    $exit_code "$(/bin/date +%s)" > $temporary_receipt
  /bin/chmod 600 $temporary_receipt
  /bin/mv -f $temporary_receipt $receipt
  /bin/launchctl remove $job_label >/dev/null 2>&1 || true
  exit $exit_code
}}
trap finalize EXIT

{command}
"""


def render_persistent_runner(spec: OneShotRunnerSpec) -> str:
    if (
        spec.persistent_plist is None
        or spec.authority_repository is None
        or spec.source_commit is None
        or spec.expires_at is None
    ):
        raise ValueError
    label = shlex.quote(spec.label)
    receipt = shlex.quote(str(spec.receipt))
    claim = shlex.quote(f"{spec.receipt}.claim")
    persistent_plist = shlex.quote(str(spec.persistent_plist))
    repository = shlex.quote(str(spec.authority_repository))
    command = shlex.join(spec.command)
    run_epoch = int(spec.run_at.timestamp())
    expires_epoch = int(spec.expires_at.timestamp())
    provenance = (
        spec.role,
        spec.request_sha256,
        spec.plan_sha256,
        spec.runtime_commit_sha,
        spec.runtime_attestation_sha256,
        spec.preparation_manifest,
    )
    provenance_enabled = any(value is not None for value in provenance)
    if provenance_enabled and any(value is None for value in provenance):
        raise ValueError
    incident_declarations, incident_writer, incident_call = render_optional_us_execution_incident_shell(
        spec.market,
        spec.target_session,
        spec.execution_incident_receipt,
        provenance_enabled,
    )
    if provenance_enabled:
        manifest = shlex.quote(str(spec.preparation_manifest))
        provenance_declarations = (
            f"readonly role={shlex.quote(str(spec.role))}\n"
            f"readonly request_sha256={spec.request_sha256}\n"
            f"readonly plan_sha256={spec.plan_sha256}\n"
            f"readonly runtime_commit_sha={spec.runtime_commit_sha}\n"
            f"readonly runtime_attestation_sha256={spec.runtime_attestation_sha256}\n"
            f"readonly preparation_manifest={manifest}\n"
        )
        receipt_format = (
            '{"completed_at_epoch":%s,"exit_code":%d,"label":"%s",'
            '"plan_sha256":"%s","preparation_manifest_sha256":"%s",'
            '"request_sha256":"%s","result":"%s","role":"%s",'
            '"runtime_attestation_sha256":"%s","runtime_commit_sha":"%s",'
            '"schema_version":2,"source_commit_sha":"%s"}\\n'
        )
        receipt_arguments = (
            "$(/bin/date +%s) $exit_code $job_label $plan_sha256 "
            "$manifest_sha256 $request_sha256 $result $role "
            "$runtime_attestation_sha256 $runtime_commit_sha $source_commit"
        )
        manifest_hash = (
            "  local manifest_sha256\n"
            "  manifest_sha256=$(/usr/bin/shasum -a 256 $preparation_manifest | "
            "/usr/bin/awk '{print $1}')\n"
        )
    else:
        provenance_declarations = ""
        receipt_format = (
            '{"completed_at_epoch":%s,"exit_code":%d,"label":"%s",'
            '"result":"%s","schema_version":1,"source_commit_sha":"%s"}\\n'
        )
        receipt_arguments = "$(/bin/date +%s) $exit_code $job_label $result $source_commit"
        manifest_hash = ""
    match spec.authority_mode:
        case "current_main":
            authority_check = (
                "branch=$(/usr/bin/git -C $repository symbolic-ref --quiet --short HEAD 2>/dev/null)\n"
                "tracked=$(/usr/bin/git -C $repository status --porcelain=v1 --untracked-files=no 2>/dev/null)\n"
                "head=$(/usr/bin/git -C $repository rev-parse HEAD 2>/dev/null)\n"
                "local_main=$(/usr/bin/git -C $repository rev-parse refs/heads/main 2>/dev/null)\n"
                "origin_main=$(/usr/bin/git -C $repository rev-parse refs/remotes/origin/main 2>/dev/null)\n"
                "if [[ $branch != main || -n $tracked || $head != $source_commit || \\\n"
                "  $head != $local_main || $head != $origin_main ]]; then\n"
                f"{incident_call}"
                "  write_receipt blocked 78\n"
                "  cleanup_job\n"
                "  exit 78\n"
                "fi"
            )
        case "frozen_runtime":
            authority_check = (
                "tracked=$(/usr/bin/git -C $repository status --porcelain=v1 "
                "--untracked-files=all 2>/dev/null)\n"
                "head=$(/usr/bin/git -C $repository rev-parse HEAD 2>/dev/null)\n"
                "if [[ -n $tracked || $head != $source_commit ]]; then\n"
                f"{incident_call}"
                "  write_receipt blocked 78\n"
                "  cleanup_job\n"
                "  exit 78\n"
                "fi"
            )
        case unreachable:
            assert_never(unreachable)
    return f"""#!/bin/zsh

set -u
umask 077

readonly job_label={label}
readonly run_epoch={run_epoch}
readonly expires_epoch={expires_epoch}
readonly receipt={receipt}
readonly claim={claim}
readonly persistent_plist={persistent_plist}
readonly repository={repository}
readonly source_commit={spec.source_commit}
{provenance_declarations}
{incident_declarations}

cleanup_job() {{
  /bin/launchctl remove $job_label >/dev/null 2>&1 || true
  /bin/rm -f $persistent_plist
}}

write_receipt() {{
  local result=$1
  local exit_code=$2
  local temporary_receipt="${{receipt}}.tmp.$$"
{manifest_hash}\
  /usr/bin/printf '{receipt_format}' \\
    {receipt_arguments} > $temporary_receipt
  /bin/chmod 600 $temporary_receipt
  /bin/mv -f $temporary_receipt $receipt
}}
{incident_writer}

if [[ -f $receipt ]]; then
  cleanup_job
  exit 0
fi

while (( $(/bin/date +%s) < run_epoch )); do
  remaining=$(( run_epoch - $(/bin/date +%s) ))
  if (( remaining > 60 )); then
    /bin/sleep 60
  else
    /bin/sleep $remaining
  fi
done

if (( $(/bin/date +%s) > expires_epoch )); then
  write_receipt expired 0
  cleanup_job
  exit 0
fi

{authority_check}

if [[ -d $claim ]]; then
  /bin/rmdir $claim 2>/dev/null || {{
    print -u2 -r -- '{{"reason":"active_claim","result":"blocked"}}'
    cleanup_job
    exit 75
  }}
fi
if ! /bin/mkdir $claim 2>/dev/null; then
  print -u2 -r -- '{{"reason":"already_claimed","result":"blocked"}}'
  cleanup_job
  exit 75
fi
/bin/chmod 700 $claim

finalize() {{
  local exit_code=$?
  trap - EXIT
  write_receipt completed $exit_code
  cleanup_job
  exit $exit_code
}}
trap finalize EXIT

{command}
"""


__all__ = ("OneShotRunnerSpec", "render_persistent_runner", "render_runner")
