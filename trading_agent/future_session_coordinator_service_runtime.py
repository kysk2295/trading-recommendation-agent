from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
    canonical_service_config_json,
)
from trading_agent.future_session_us_activation_verifier import read_private_file
from trading_agent.repository_current_main import (
    CurrentMainAuthorityError,
    current_main_commit,
)


@dataclass(frozen=True, slots=True)
class FrozenRuntimeError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def load_service_config(path: Path) -> FutureSessionCoordinatorServiceConfig:
    try:
        payload = read_private_file(path, 0o600)
        config = FutureSessionCoordinatorServiceConfig.model_validate_json(payload)
    except (OSError, TypeError, ValidationError, ValueError):
        raise FrozenRuntimeError("invalid_private_config") from None
    if canonical_service_config_json(config).encode() != payload:
        raise FrozenRuntimeError("invalid_private_config")
    return config


def ensure_frozen_runtime(
    repository: Path,
    runtime_root: Path,
    expected_commit: str | None = None,
    *,
    require_current_main: bool = True,
) -> Path:
    if not require_current_main:
        if expected_commit is None:
            raise FrozenRuntimeError("configured_main_authority_missing")
        destination = runtime_root / expected_commit
        if destination.exists():
            _verify_runtime(destination, expected_commit)
            return destination
    try:
        commit = current_main_commit(repository)
        if expected_commit is not None and commit != expected_commit:
            raise FrozenRuntimeError("configured_main_authority_mismatch")
        if _git(repository, "status", "--porcelain=v1", "--untracked-files=all"):
            raise FrozenRuntimeError("current_main_authority_invalid")
    except CurrentMainAuthorityError:
        raise FrozenRuntimeError("current_main_authority_invalid") from None
    destination = runtime_root / commit
    _ensure_private_directory(runtime_root.parent)
    _ensure_private_directory(runtime_root)
    if destination.exists():
        _verify_runtime(destination, commit)
        return destination
    staging = runtime_root / f".{commit}.creating-{os.getpid()}"
    if staging.exists():
        raise FrozenRuntimeError("frozen_runtime_creation_conflict")
    try:
        _run_git(("clone", "--shared", "--no-checkout", str(repository), str(staging)))
        _git(staging, "checkout", "--detach", commit)
        os.chmod(staging, 0o700)
        _verify_runtime(staging, commit)
        staging.rename(destination)
    except (FrozenRuntimeError, OSError):
        if staging.exists():
            shutil.rmtree(staging)
        raise FrozenRuntimeError("frozen_runtime_invalid") from None
    return destination


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise FrozenRuntimeError("private_state_directory_invalid")


def _verify_runtime(runtime: Path, commit: str) -> None:
    try:
        metadata = runtime.lstat()
    except OSError:
        raise FrozenRuntimeError("frozen_runtime_invalid") from None
    if (
        runtime.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or _git(runtime, "rev-parse", "HEAD") != commit
        or _git(runtime, "status", "--porcelain=v1", "--untracked-files=all")
    ):
        raise FrozenRuntimeError("frozen_runtime_invalid")


def _git(repository: Path, *arguments: str) -> str:
    completed = _run_git(("-C", str(repository), *arguments))
    return completed.stdout.strip()


def _run_git(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ("/usr/bin/git", *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        raise FrozenRuntimeError("git_authority_failed") from None
    if completed.returncode != 0:
        raise FrozenRuntimeError("git_authority_failed")
    return completed


__all__ = ("FrozenRuntimeError", "ensure_frozen_runtime", "load_service_config")
