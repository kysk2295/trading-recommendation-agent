from __future__ import annotations

import datetime as dt
import shlex
from pathlib import Path
from typing import Literal


def render_us_execution_incident_shell(
    market: Literal["us", "kr"],
    target_session: dt.date,
    receipt: Path,
) -> tuple[str, str]:
    incident_format = (
        '{"completed_at_epoch":%s,"manifest_sha256":"%s","market":"%s",'
        '"plan_sha256":"%s","reason":"runtime_authority_invalid",'
        '"request_sha256":"%s","role":"%s","runtime_commit_sha":"%s",'
        '"scheduler_main_sha":"%s","schema_version":1,"target_session":"%s"}\\n'
    )
    declarations = (
        f"readonly execution_incident_receipt={shlex.quote(str(receipt))}\n"
        f"readonly market={market}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident() {
  if [[ -f $execution_incident_receipt ]]; then return 0; fi
  local temporary_incident="${execution_incident_receipt}.tmp.$$"
  local manifest_sha256
  manifest_sha256=$(/usr/bin/shasum -a 256 "$preparation_manifest" | /usr/bin/awk '{print $1}')
  /usr/bin/printf '__INCIDENT_FORMAT__' \
    "$(/bin/date +%s)" "$manifest_sha256" "$market" "$plan_sha256" "$request_sha256" \
    "$role" "$runtime_commit_sha" "$source_commit" "$target_session" > "$temporary_incident"
  /bin/chmod 600 "$temporary_incident"
  if /bin/ln "$temporary_incident" "$execution_incident_receipt"; then
    /bin/rm -f "$temporary_incident"
    return 0
  fi
  /bin/rm -f "$temporary_incident"
  [[ -f $execution_incident_receipt ]]
}
""".replace("__INCIDENT_FORMAT__", incident_format)
    return declarations, writer


def render_optional_us_execution_incident_shell(
    market: Literal["us", "kr"] | None,
    target_session: dt.date | None,
    receipt: Path | None,
    provenance_enabled: bool,
) -> tuple[str, str, str]:
    enabled = market is not None or target_session is not None or receipt is not None
    if not enabled:
        return "", "", ""
    if not provenance_enabled or market is None or target_session is None or receipt is None:
        raise ValueError
    declarations, writer = render_us_execution_incident_shell(market, target_session, receipt)
    return declarations, writer, "  write_execution_incident\n"


def render_kr_execution_incident_shell(
    target_session: dt.date,
    receipt: Path,
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
        f"readonly manifest={shlex.quote(str(manifest))}\n"
        f"readonly request_sha256={request_sha256}\n"
        f"readonly plan_sha256={plan_sha256}\n"
        f"readonly scheduler_main_sha={scheduler_main_sha}\n"
        f"readonly runtime_commit_sha={runtime_commit_sha}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident() {
  if [[ -f $incident_receipt ]]; then return 0; fi
  local temporary_incident="${incident_receipt}.tmp.$$"
  local manifest_sha256
  manifest_sha256=$(/usr/bin/shasum -a 256 "$manifest" | /usr/bin/awk '{print $1}')
  /usr/bin/printf '__INCIDENT_FORMAT__' \
    "$(/bin/date +%s)" "$manifest_sha256" "$plan_sha256" "$request_sha256" \
    "$runtime_commit_sha" "$scheduler_main_sha" "$target_session" > "$temporary_incident"
  /bin/chmod 600 "$temporary_incident"
  if /bin/ln "$temporary_incident" "$incident_receipt"; then
    /bin/rm -f "$temporary_incident"
    return 0
  fi
  /bin/rm -f "$temporary_incident"
  [[ -f $incident_receipt ]]
}
""".replace("__INCIDENT_FORMAT__", incident_format)
    return declarations, writer


__all__ = (
    "render_kr_execution_incident_shell",
    "render_optional_us_execution_incident_shell",
    "render_us_execution_incident_shell",
)
