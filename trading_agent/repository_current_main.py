from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_COMMIT_PATTERN: Final = re.compile(r"[a-f0-9]{40}")


@dataclass(frozen=True, slots=True)
class CurrentMainAuthorityError(RuntimeError):
    reason: str = "current_main_authority_invalid"

    def __str__(self) -> str:
        return self.reason


def current_main_commit(repository: Path) -> str:
    if not repository.is_dir() or not (repository / ".git").exists():
        raise CurrentMainAuthorityError
    branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
    tracked = _git(repository, "status", "--porcelain=v1", "--untracked-files=no")
    head = _git(repository, "rev-parse", "HEAD")
    local_main = _git(repository, "rev-parse", "refs/heads/main")
    origin_main = _git(repository, "rev-parse", "refs/remotes/origin/main")
    if (
        branch != "main"
        or tracked
        or _COMMIT_PATTERN.fullmatch(head) is None
        or head != local_main
        or head != origin_main
    ):
        raise CurrentMainAuthorityError
    return head


def _git(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository), *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise CurrentMainAuthorityError from None
    if completed.returncode != 0:
        raise CurrentMainAuthorityError
    return completed.stdout.strip()


__all__ = ("CurrentMainAuthorityError", "current_main_commit")
