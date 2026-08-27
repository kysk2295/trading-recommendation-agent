from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path
from typing import override


class KrLoopMutationExecutionError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KR Loop Engineer mutation execution failed"


def prepare_private_root(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise KrLoopMutationExecutionError


def clone_at(repository: Path, checkout: Path, base_commit: str) -> None:
    if checkout.exists() or repository.is_symlink() or not repository.is_dir():
        raise KrLoopMutationExecutionError
    completed = subprocess.run(
        (
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            "--branch",
            "main",
            "--single-branch",
            str(repository),
            str(checkout),
        ),
        check=False,
        capture_output=True,
        timeout=120,
        env=_git_environment(),
    )
    if completed.returncode != 0 or git(checkout, "rev-parse", "HEAD").strip() != base_commit:
        raise KrLoopMutationExecutionError


def changed_paths(checkout: Path, base_commit: str) -> tuple[str, ...]:
    modified = git(checkout, "diff", "--name-only", "--no-renames", base_commit).splitlines()
    untracked = git(checkout, "ls-files", "--others", "--exclude-standard").splitlines()
    return tuple(sorted(set((*modified, *untracked))))


def commit_candidate(checkout: Path, allowed_paths: tuple[str, ...], now: dt.datetime) -> None:
    _ = git(checkout, "add", "--", *allowed_paths)
    environment = _git_environment()
    timestamp = now.isoformat()
    environment.update(
        {
            "GIT_AUTHOR_DATE": timestamp,
            "GIT_AUTHOR_EMAIL": "loop-engineer@local.invalid",
            "GIT_AUTHOR_NAME": "Loop Engineer",
            "GIT_COMMITTER_DATE": timestamp,
            "GIT_COMMITTER_EMAIL": "loop-engineer@local.invalid",
            "GIT_COMMITTER_NAME": "Loop Engineer",
        }
    )
    completed = subprocess.run(
        (
            "git",
            "-C",
            str(checkout),
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "--no-gpg-sign",
            "-m",
            "KR Loop Engineer challenger",
        ),
        check=False,
        capture_output=True,
        timeout=60,
        env=environment,
    )
    if completed.returncode != 0:
        raise KrLoopMutationExecutionError


def git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), *arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=_git_environment(),
    )
    if completed.returncode != 0:
        raise KrLoopMutationExecutionError
    return completed.stdout


def cleanup_checkout(checkout: Path, task_root: Path) -> None:
    if not checkout.exists():
        return
    try:
        if checkout.is_symlink() or checkout.parent != task_root:
            raise KrLoopMutationExecutionError
        shutil.rmtree(checkout)
    except OSError as error:
        raise KrLoopMutationExecutionError from error


def _git_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


__all__ = (
    "KrLoopMutationExecutionError",
    "changed_paths",
    "cleanup_checkout",
    "clone_at",
    "commit_candidate",
    "git",
    "prepare_private_root",
)
