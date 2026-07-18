"""Repository root and Git index topology checks for in-place Grok workers."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from development_harness.grok_workspace_fingerprint import GrokWorkspaceGuardError, run_git

__all__ = (
    "absolute_path_has_symlink_component",
    "assert_git_index_topology",
    "assert_main_repository_root",
    "path_has_symlink_component",
)


def _absolute_without_symlink_resolve(path: Path) -> Path:
    return Path(os.path.abspath(path))


def path_has_symlink_component(repo: Path, relative: str) -> bool:
    current = repo
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
        if not current.exists():
            break
    return False


def absolute_path_has_symlink_component(path: Path) -> bool:
    """Return True when any path component is a symlink (no depth exceptions).

    Callers that need macOS temp compatibility should resolve the path first
    (``Path.resolve`` expands ``/var`` → ``/private/var``) and then check the
    resolved path, or ensure fixtures already use resolved bases.
    """

    absolute = _absolute_without_symlink_resolve(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
        if not current.exists():
            break
    return False


def assert_git_index_topology(repo: Path) -> None:
    """Reject a symlinked index and require the effective path to be repo-owned."""

    owned = repo / ".git" / "index"
    if owned.is_symlink():
        raise GrokWorkspaceGuardError("git index must not be a symlink")
    raw = run_git(repo, "rev-parse", "--git-path", "index").strip()
    if not raw:
        raise GrokWorkspaceGuardError("git index path is not usable")
    effective = Path(raw) if Path(raw).is_absolute() else repo / raw
    if effective.is_symlink():
        raise GrokWorkspaceGuardError("git index must not be a symlink")
    try:
        effective_resolved = effective.resolve(strict=True)
        owned_resolved = owned.resolve(strict=True)
    except OSError as error:
        raise GrokWorkspaceGuardError("git index path is not usable") from error
    if effective_resolved != owned_resolved:
        raise GrokWorkspaceGuardError(
            "effective git index path must resolve to repository-owned .git/index"
        )


def assert_main_repository_root(repo: Path) -> Path:
    """Require a non-linked, non-symlink main-branch repository root."""

    absolute = _absolute_without_symlink_resolve(repo)
    # Reject every symlink component on the caller spelling (parent and leaf).
    # macOS pytest temps should pass a resolved base (``/private/var/...``);
    # un-resolved ``/var/...`` spellings still contain the ``/var`` symlink.
    if absolute_path_has_symlink_component(absolute):
        raise GrokWorkspaceGuardError("repository path must not include symlink components")
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as error:
        raise GrokWorkspaceGuardError("repository path is not usable") from error
    if absolute_path_has_symlink_component(resolved):
        raise GrokWorkspaceGuardError("repository path must not include symlink components")
    git_dir = resolved / ".git"
    if git_dir.is_symlink() or git_dir.is_file() or not git_dir.is_dir():
        raise GrokWorkspaceGuardError("linked worktree checkouts are not allowed")
    root = Path(run_git(resolved, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
    if root != resolved:
        raise GrokWorkspaceGuardError("task runner requires the repository root")
    git_dir_value = Path(run_git(resolved, "rev-parse", "--git-dir").strip())
    git_common = Path(run_git(resolved, "rev-parse", "--git-common-dir").strip())
    git_dir_value = (
        (resolved / git_dir_value).resolve()
        if not git_dir_value.is_absolute()
        else git_dir_value.resolve()
    )
    git_common = (
        (resolved / git_common).resolve() if not git_common.is_absolute() else git_common.resolve()
    )
    if git_dir_value != git_common:
        raise GrokWorkspaceGuardError("linked worktree checkouts are not allowed")
    if run_git(resolved, "branch", "--show-current").strip() != "main":
        raise GrokWorkspaceGuardError("task runner requires the main branch")
    assert_git_index_topology(resolved)
    return root
