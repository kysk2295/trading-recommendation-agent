#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.11"]
# ///
#
# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run run_launchd_one_shot.py --help
# 3. Or make executable and run:
#      chmod +x run_launchd_one_shot.py
#      ./run_launchd_one_shot.py --help
# ──────────────────

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

LABEL_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
LAUNCHCTL: Final = Path("/bin/launchctl")
ZSH: Final = Path("/bin/zsh")
PRIVATE_FILE_MODE: Final = 0o600
PRIVATE_DIRECTORY_MODE: Final = 0o700
PRIVATE_EXECUTABLE_MODE: Final = 0o700


@dataclass(frozen=True, slots=True)
class InvalidOneShotFieldError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class OneShotInstallError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


class OneShotRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    run_at: dt.datetime
    wrapper: Path
    stdout_log: Path
    stderr_log: Path
    receipt: Path
    command: tuple[str, ...]

    @field_validator("label")
    @classmethod
    def parse_label(cls, value: str) -> str:
        if LABEL_PATTERN.fullmatch(value) is None:
            raise InvalidOneShotFieldError("invalid_label")
        return value

    @field_validator("run_at")
    @classmethod
    def parse_run_at(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidOneShotFieldError("timezone_required")
        return value

    @field_validator("wrapper", "stdout_log", "stderr_log", "receipt")
    @classmethod
    def parse_artifact_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise InvalidOneShotFieldError("absolute_artifact_path_required")
        return value

    @model_validator(mode="after")
    def parse_command_and_artifacts(self) -> Self:
        if not self.command:
            raise InvalidOneShotFieldError("command_required")
        executable = Path(self.command[0])
        if not executable.is_absolute():
            raise InvalidOneShotFieldError("absolute_command_required")
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise InvalidOneShotFieldError("command_not_executable")
        artifacts = {
            self.wrapper,
            self.stdout_log,
            self.stderr_log,
            self.receipt,
        }
        if len(artifacts) != 4:
            raise InvalidOneShotFieldError("artifact_paths_must_be_distinct")
        return self


def prepare_one_shot(request: OneShotRequest) -> None:
    if os.path.lexists(request.receipt) or os.path.lexists(f"{request.receipt}.claim"):
        raise OneShotInstallError("schedule_already_claimed")
    for path in (
        request.wrapper,
        request.stdout_log,
        request.stderr_log,
        request.receipt,
    ):
        path.parent.mkdir(
            mode=PRIVATE_DIRECTORY_MODE,
            parents=True,
            exist_ok=True,
        )
    _prepare_private_log(request.stdout_log)
    _prepare_private_log(request.stderr_log)
    _write_private_executable(request.wrapper, _render_runner(request))


def submit_one_shot(request: OneShotRequest) -> None:
    completed = subprocess.run(
        (
            str(LAUNCHCTL),
            "submit",
            "-l",
            request.label,
            "-o",
            str(request.stdout_log),
            "-e",
            str(request.stderr_log),
            "--",
            str(ZSH),
            str(request.wrapper),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise OneShotInstallError("launchctl_submit_failed")


def _prepare_private_log(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)


def _write_private_executable(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    descriptor = os.open(path, flags, PRIVATE_EXECUTABLE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_EXECUTABLE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            _ = stream.write(content)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _render_runner(request: OneShotRequest) -> str:
    label = shlex.quote(request.label)
    receipt = shlex.quote(str(request.receipt))
    claim = shlex.quote(f"{request.receipt}.claim")
    command = shlex.join(request.command)
    run_epoch = int(request.run_at.timestamp())
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="macOS launchd에 at-most-once 실시간 검증 작업을 예약합니다."
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--run-at", required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--stdout-log", type=Path, required=True)
    parser.add_argument("--stderr-log", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        request = OneShotRequest(
            label=args.label,
            run_at=args.run_at,
            wrapper=args.wrapper,
            stdout_log=args.stdout_log,
            stderr_log=args.stderr_log,
            receipt=args.receipt,
            command=tuple(command),
        )
        prepare_one_shot(request)
        if not args.prepare_only:
            submit_one_shot(request)
    except ValidationError:
        print(
            json.dumps({"reason": "invalid_request", "result": "blocked"}),
            file=sys.stderr,
        )
        return 2
    except OneShotInstallError as error:
        print(
            json.dumps({"reason": error.reason, "result": "blocked"}),
            file=sys.stderr,
        )
        return 1
    except OSError:
        print(
            json.dumps({"reason": "artifact_io_failed", "result": "blocked"}),
            file=sys.stderr,
        )
        return 1
    status = "prepared" if args.prepare_only else "scheduled"
    print(json.dumps({"label": request.label, "result": status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
