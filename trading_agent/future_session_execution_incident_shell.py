from __future__ import annotations

import datetime as dt
import shlex
from pathlib import Path
from typing import Literal

_VERIFIED_PUBLISHER_PROGRAM = """import hashlib
import os
import stat
import sys

try:
    path = sys.argv[1]
    expected = sys.argv[2]
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > 256 * 1024
        ):
            raise ValueError
        payload = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, 256 * 1024 + 1 - len(payload))):
            payload.extend(chunk)
            if len(payload) > 256 * 1024:
                raise ValueError
        after = os.fstat(descriptor)
        if (
            len(payload) != before.st_size
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or hashlib.sha256(payload).hexdigest() != expected
        ):
            raise ValueError
    finally:
        os.close(descriptor)
    source = bytes(payload)
    arguments = sys.argv[3:]
except (OSError, TypeError, ValueError):
    raise SystemExit(2) from None

sys.argv = [path, *arguments]
exec(compile(source, path, "exec"), {"__file__": path, "__name__": "__main__"})
"""


def render_us_execution_incident_shell(
    market: Literal["us", "kr"],
    target_session: dt.date,
    receipt: Path,
    queue_receipt: Path,
    fsync_interpreter: Path,
    publisher: Path,
    publisher_sha256: str,
) -> tuple[str, str]:
    declarations = (
        f"readonly execution_incident_receipt={shlex.quote(str(receipt))}\n"
        f"readonly execution_incident_queue_receipt={shlex.quote(str(queue_receipt))}\n"
        f"readonly execution_incident_fsync_interpreter={shlex.quote(str(fsync_interpreter))}\n"
        f"readonly execution_incident_publisher={shlex.quote(str(publisher))}\n"
        f"readonly execution_incident_publisher_sha256={publisher_sha256}\n"
        f"readonly market={market}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident() {
  "$execution_incident_fsync_interpreter" -I -c __VERIFIED_PUBLISHER_PROGRAM__ \
    "$execution_incident_publisher" \
    "$execution_incident_publisher_sha256" \
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
""".replace("__VERIFIED_PUBLISHER_PROGRAM__", shlex.quote(_VERIFIED_PUBLISHER_PROGRAM))
    return declarations, writer


def render_optional_us_execution_incident_shell(
    market: Literal["us", "kr"] | None,
    target_session: dt.date | None,
    receipt: Path | None,
    queue_receipt: Path | None,
    fsync_interpreter: Path | None,
    publisher: Path | None,
    publisher_sha256: str | None,
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
            publisher,
            publisher_sha256,
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
        or publisher is None
        or publisher_sha256 is None
    ):
        raise ValueError
    declarations, writer = render_us_execution_incident_shell(
        market,
        target_session,
        receipt,
        queue_receipt,
        fsync_interpreter,
        publisher,
        publisher_sha256,
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
    publisher: Path,
    publisher_sha256: str,
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
        f"readonly incident_publisher={shlex.quote(str(publisher))}\n"
        f"readonly incident_publisher_sha256={publisher_sha256}\n"
        f"readonly manifest={shlex.quote(str(manifest))}\n"
        f"readonly request_sha256={request_sha256}\n"
        f"readonly plan_sha256={plan_sha256}\n"
        f"readonly scheduler_main_sha={scheduler_main_sha}\n"
        f"readonly runtime_commit_sha={runtime_commit_sha}\n"
        f"readonly target_session={target_session}\n"
    )
    writer = """
write_execution_incident() {
  "$incident_fsync_interpreter" -I -c __VERIFIED_PUBLISHER_PROGRAM__ \
    "$incident_publisher" \
    "$incident_publisher_sha256" \
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
""".replace("__VERIFIED_PUBLISHER_PROGRAM__", shlex.quote(_VERIFIED_PUBLISHER_PROGRAM))
    return declarations, writer


__all__ = (
    "render_kr_execution_incident_shell",
    "render_optional_us_execution_incident_shell",
    "render_us_execution_incident_shell",
)
