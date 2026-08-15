from __future__ import annotations

import datetime as dt
import shlex
from pathlib import Path
from typing import Literal


def render_us_execution_incident_shell(
    market: Literal["us", "kr"],
    target_session: dt.date,
    receipt: Path,
    queue_receipt: Path,
    fsync_interpreter: Path,
    publisher_root: Path,
) -> tuple[str, str]:
    declarations = (
        f"readonly execution_incident_receipt={shlex.quote(str(receipt))}\n"
        f"readonly execution_incident_queue_receipt={shlex.quote(str(queue_receipt))}\n"
        f"readonly execution_incident_fsync_interpreter={shlex.quote(str(fsync_interpreter))}\n"
        f"readonly execution_incident_publisher_root={shlex.quote(str(publisher_root))}\n"
        f"readonly market={market}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident() {
  PYTHONPATH="$execution_incident_publisher_root" \
    "$execution_incident_fsync_interpreter" \
    -m trading_agent.future_session_execution_incident_publisher \
    --receipt "$execution_incident_receipt" \
    --queue "$execution_incident_queue_receipt" \
    --manifest "$preparation_manifest" \
    --market "$market" \
    --target-session "$target_session" \
    --role "$role" \
    --request-sha256 "$request_sha256" \
    --plan-sha256 "$plan_sha256" \
    --scheduler-main-sha "$source_commit" \
    --runtime-commit-sha "$runtime_commit_sha"
}
"""
    return declarations, writer


def render_optional_us_execution_incident_shell(
    market: Literal["us", "kr"] | None,
    target_session: dt.date | None,
    receipt: Path | None,
    queue_receipt: Path | None,
    fsync_interpreter: Path | None,
    publisher_root: Path | None,
    provenance_enabled: bool,
) -> tuple[str, str, str]:
    enabled = any(
        value is not None
        for value in (
            market,
            target_session,
            receipt,
            queue_receipt,
            fsync_interpreter,
            publisher_root,
        )
    )
    if not enabled:
        return "", "", ""
    if (
        not provenance_enabled
        or market is None
        or target_session is None
        or receipt is None
        or queue_receipt is None
        or fsync_interpreter is None
        or publisher_root is None
    ):
        raise ValueError
    declarations, writer = render_us_execution_incident_shell(
        market,
        target_session,
        receipt,
        queue_receipt,
        fsync_interpreter,
        publisher_root,
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
    publisher_root: Path,
    manifest: Path,
    request_sha256: str,
    plan_sha256: str,
    scheduler_main_sha: str,
    runtime_commit_sha: str,
) -> tuple[str, str]:
    declarations = (
        f"readonly incident_receipt={shlex.quote(str(receipt))}\n"
        f"readonly incident_queue_receipt={shlex.quote(str(queue_receipt))}\n"
        f"readonly incident_fsync_interpreter={shlex.quote(str(fsync_interpreter))}\n"
        f"readonly incident_publisher_root={shlex.quote(str(publisher_root))}\n"
        f"readonly manifest={shlex.quote(str(manifest))}\n"
        f"readonly request_sha256={request_sha256}\n"
        f"readonly plan_sha256={plan_sha256}\n"
        f"readonly scheduler_main_sha={scheduler_main_sha}\n"
        f"readonly runtime_commit_sha={runtime_commit_sha}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident() {
  PYTHONPATH="$incident_publisher_root" \
    "$incident_fsync_interpreter" \
    -m trading_agent.future_session_execution_incident_publisher \
    --receipt "$incident_receipt" \
    --queue "$incident_queue_receipt" \
    --manifest "$manifest" \
    --market kr \
    --target-session "$target_session" \
    --role kr_supervisor \
    --request-sha256 "$request_sha256" \
    --plan-sha256 "$plan_sha256" \
    --scheduler-main-sha "$scheduler_main_sha" \
    --runtime-commit-sha "$runtime_commit_sha"
}
"""
    return declarations, writer


__all__ = (
    "render_kr_execution_incident_shell",
    "render_optional_us_execution_incident_shell",
    "render_us_execution_incident_shell",
)
