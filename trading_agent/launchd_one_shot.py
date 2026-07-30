from __future__ import annotations

import datetime as dt
import os
import plistlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from trading_agent.launchd_one_shot_runner import (
    OneShotRunnerSpec,
    render_persistent_runner,
    render_runner,
)
from trading_agent.repository_current_main import (
    CurrentMainAuthorityError,
    current_main_commit,
)

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
    expires_at: dt.datetime | None = None
    persistent_plist: Path | None = None
    authority_repository: Path | None = None
    recovery_safe: bool = False

    @field_validator("label")
    @classmethod
    def parse_label(cls, value: str) -> str:
        if LABEL_PATTERN.fullmatch(value) is None:
            raise InvalidOneShotFieldError("invalid_label")
        return value

    @field_validator("run_at", "expires_at")
    @classmethod
    def parse_run_at(cls, value: dt.datetime | None) -> dt.datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise InvalidOneShotFieldError("timezone_required")
        return value

    @field_validator(
        "wrapper",
        "stdout_log",
        "stderr_log",
        "receipt",
        "persistent_plist",
        "authority_repository",
    )
    @classmethod
    def parse_artifact_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
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
        if self.persistent_plist is not None:
            artifacts.add(self.persistent_plist)
        if len(artifacts) != 4 + (self.persistent_plist is not None):
            raise InvalidOneShotFieldError("artifact_paths_must_be_distinct")
        persistent = self.persistent_plist is not None
        persistent_contract = (
            self.authority_repository is not None
            and self.recovery_safe
            and self.expires_at is not None
            and self.expires_at > self.run_at
        )
        persistent_inputs = (
            self.authority_repository is not None
            or self.recovery_safe
            or self.expires_at is not None
        )
        if (persistent and not persistent_contract) or (not persistent and persistent_inputs):
            raise InvalidOneShotFieldError("persistent_recovery_authority_required")
        return self


def prepare_one_shot(request: OneShotRequest) -> None:
    _require_explicit_interpreter(Path(request.command[0]))
    if os.path.lexists(request.receipt) or os.path.lexists(f"{request.receipt}.claim"):
        raise OneShotInstallError("schedule_already_claimed")
    paths = (
        request.wrapper,
        request.stdout_log,
        request.stderr_log,
        request.receipt,
    )
    for path in (*paths, *((request.persistent_plist,) if request.persistent_plist is not None else ())):
        path.parent.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    _prepare_private_log(request.stdout_log)
    _prepare_private_log(request.stderr_log)
    if request.persistent_plist is None:
        _write_private_executable(
            request.wrapper,
            render_runner(
                OneShotRunnerSpec(
                    request.label,
                    request.run_at,
                    request.receipt,
                    request.command,
                )
            ),
        )
        return
    if request.authority_repository is None:
        raise OneShotInstallError("current_main_authority_invalid")
    try:
        source_commit = current_main_commit(request.authority_repository)
    except CurrentMainAuthorityError:
        raise OneShotInstallError("current_main_authority_invalid") from None
    _write_private_executable(
        request.wrapper,
        render_persistent_runner(
            OneShotRunnerSpec(
                request.label,
                request.run_at,
                request.receipt,
                request.command,
                request.expires_at,
                request.persistent_plist,
                request.authority_repository,
                source_commit,
            )
        ),
    )
    _write_private_file(
        request.persistent_plist,
        plistlib.dumps(_persistent_launch_agent(request), sort_keys=True),
    )


def submit_one_shot(request: OneShotRequest) -> None:
    if request.persistent_plist is None:
        command = (
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
        )
    else:
        command = (
            str(LAUNCHCTL),
            "bootstrap",
            f"gui/{os.getuid()}",
            str(request.persistent_plist),
        )
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise OneShotInstallError("launchctl_submit_failed")


def _persistent_launch_agent(request: OneShotRequest) -> dict[str, str | int | bool | list[str]]:
    return {
        "Label": request.label,
        "ProcessType": "Background",
        "ProgramArguments": [str(ZSH), str(request.wrapper)],
        "RunAtLoad": True,
        "StandardErrorPath": str(request.stderr_log),
        "StandardOutPath": str(request.stdout_log),
        "ThrottleInterval": 30,
        "Umask": 0o077,
    }


def _prepare_private_log(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)


def _write_private_executable(path: Path, content: str) -> None:
    _write_private_file(path, content.encode(), mode=PRIVATE_EXECUTABLE_MODE)


def _write_private_file(
    path: Path,
    content: bytes,
    *,
    mode: int = PRIVATE_FILE_MODE,
) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        _ = os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_explicit_interpreter(executable: Path) -> None:
    with executable.open("rb") as handle:
        first_line = handle.readline(4096)
    if not first_line.startswith(b"#!"):
        return
    interpreter = first_line[2:].lstrip().split(maxsplit=1)[0]
    if interpreter == b"/usr/bin/env":
        raise OneShotInstallError("explicit_interpreter_required")


__all__ = (
    "InvalidOneShotFieldError",
    "OneShotInstallError",
    "OneShotRequest",
    "prepare_one_shot",
    "submit_one_shot",
)
