from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from trading_agent.hermes_arm_request import HermesArmFailure, InvalidHermesArmRequestError


def require_current_clean_main(repository: Path, expected_commit: str) -> None:
    _require_secure_repository(repository)
    root = repository.resolve(strict=False)
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    commit = _git(root, "rev-parse", "HEAD")
    local_main = _git(root, "rev-parse", "refs/heads/main")
    origin_main = _git(root, "rev-parse", "refs/remotes/origin/main")
    dirty = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise InvalidHermesArmRequestError(HermesArmFailure.DIRTY_COMMIT)
    if branch != "main" or len({commit, local_main, origin_main, expected_commit}) != 1:
        raise InvalidHermesArmRequestError(HermesArmFailure.COMMIT_MISMATCH)


def require_frozen_commit(repository: Path, expected_commit: str) -> None:
    _require_secure_repository(repository)
    root = repository.resolve(strict=False)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise InvalidHermesArmRequestError(HermesArmFailure.DIRTY_COMMIT)
    if _git(root, "rev-parse", "HEAD") != expected_commit:
        raise InvalidHermesArmRequestError(HermesArmFailure.COMMIT_MISMATCH)


def _require_secure_repository(repository: Path) -> None:
    try:
        metadata = repository.lstat()
    except OSError:
        raise InvalidHermesArmRequestError(HermesArmFailure.DIRTY_COMMIT) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
        or repository.is_symlink()
    ):
        raise InvalidHermesArmRequestError(HermesArmFailure.DIRTY_COMMIT)


def _git(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository), *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise InvalidHermesArmRequestError(HermesArmFailure.DIRTY_COMMIT) from None
    return completed.stdout.strip()
