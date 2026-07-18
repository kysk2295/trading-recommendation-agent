"""Metadata-only workspace fingerprinting for in-place Grok workers.

Captures immutable Git database, logical index, user-owned, and ignored-path
inventories without reading file contents. Symlink directories are recorded
once and never followed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from development_harness.grok_git_control import (
    git_database_fingerprint as _git_database_fingerprint,
)
from development_harness.grok_git_control import (
    git_index_fingerprint as _git_index_fingerprint,
)
from development_harness.grok_path_metadata import (
    OwnedMetaMap,
    PathMetaMap,
    ignored_metadata,
    user_owned_metadata,
)
from development_harness.grok_process_env import sanitize_git_routing_environ

_GIT_TIMEOUT_SECONDS: Final = 30


class GrokWorkspaceGuardError(RuntimeError):
    """Raised when the repository is not safe for an in-place worker."""


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        env=sanitize_git_routing_environ(),
    )
    if completed.returncode != 0:
        raise GrokWorkspaceGuardError("Git preflight failed")
    return completed.stdout


def git_index_fingerprint(repo: Path) -> str:
    return _git_index_fingerprint(repo, run_git=run_git)


def git_database_fingerprint(repo: Path) -> str:
    return _git_database_fingerprint(repo, run_git=run_git)


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    head: str
    refs_and_objects: str
    index_entries: str
    user_owned: OwnedMetaMap
    ignored: PathMetaMap


def capture_workspace_snapshot(repo: Path) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        head=run_git(repo, "rev-parse", "HEAD").strip(),
        refs_and_objects=git_database_fingerprint(repo),
        index_entries=git_index_fingerprint(repo),
        user_owned=user_owned_metadata(repo),
        ignored=ignored_metadata(repo, run_git=run_git),
    )


def verify_workspace_snapshot(repo: Path, snapshot: WorkspaceSnapshot) -> None:
    if run_git(repo, "rev-parse", "HEAD").strip() != snapshot.head:
        raise GrokWorkspaceGuardError(
            "worker committed changes; HEAD no longer matches the contract base"
        )
    if git_database_fingerprint(repo) != snapshot.refs_and_objects:
        raise GrokWorkspaceGuardError("Git database changed under the worker")
    if git_index_fingerprint(repo) != snapshot.index_entries:
        raise GrokWorkspaceGuardError("Git index entries or flags changed under the worker")
    if user_owned_metadata(repo) != snapshot.user_owned:
        raise GrokWorkspaceGuardError("user-owned state changed under the worker")
    if ignored_metadata(repo, run_git=run_git) != snapshot.ignored:
        raise GrokWorkspaceGuardError("ignored path metadata changed under the worker")
