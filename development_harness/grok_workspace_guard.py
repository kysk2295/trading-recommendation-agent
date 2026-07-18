"""Main-branch path safety preflight for in-place Grok workers.

Workspace snapshot fingerprinting lives in
:mod:`development_harness.grok_workspace_fingerprint`; this module re-exports
the public snapshot API so existing imports remain stable. Repository topology
helpers live in :mod:`development_harness.grok_worktree_topology`.
"""

from __future__ import annotations

from pathlib import Path

from development_harness.grok_workspace_fingerprint import (
    GrokWorkspaceGuardError,
    WorkspaceSnapshot,
    capture_workspace_snapshot,
    run_git,
    verify_workspace_snapshot,
)
from development_harness.grok_worktree_topology import (
    absolute_path_has_symlink_component,
    assert_git_index_topology,
    assert_main_repository_root,
    path_has_symlink_component,
)

__all__ = (
    "GrokWorkspaceGuardError",
    "WorkspaceSnapshot",
    "absolute_path_has_symlink_component",
    "assert_allowed_paths_have_no_symlinks",
    "assert_checkout_is_safe",
    "assert_git_index_topology",
    "assert_main_repository_root",
    "assert_no_index_masking",
    "assert_not_sparse_checkout",
    "capture_workspace_snapshot",
    "path_has_symlink_component",
    "verify_workspace_snapshot",
)


def _git_config_value(repo: Path, key: str) -> str | None:
    try:
        value = run_git(repo, "config", "--get", key).strip()
    except GrokWorkspaceGuardError:
        return None
    return value or None


def _is_approved_user_owned_status(entry: str) -> bool:
    """Allow only untracked ``.hermes`` / ``.omo`` roots and nested paths.

    ``--untracked-files=all`` reports nested files as ``?? .hermes/...`` rather
    than a single directory entry, so root-only allow-list membership is not
    enough. Tracked modifications under those roots remain forbidden.
    """

    if not entry.startswith("?? "):
        return False
    path = entry[3:]
    return (
        path in {".hermes", ".omo", ".hermes/", ".omo/"}
        or path.startswith(".hermes/")
        or path.startswith(".omo/")
    )


def assert_no_index_masking(repo: Path) -> None:
    """Reject pre-existing assume-unchanged / skip-worktree flags that hide edits."""

    output = run_git(repo, "ls-files", "-v", "-z")
    for entry in output.split("\0"):
        if not entry:
            continue
        tag = entry[0]
        # Lowercase tags mark assume-unchanged; S/s mark skip-worktree.
        if tag.islower() or tag == "S":
            raise GrokWorkspaceGuardError(
                "checkout has assume-unchanged or skip-worktree index masking"
            )


def assert_not_sparse_checkout(repo: Path) -> None:
    """Reject sparse-checkout configurations that mask repository paths."""

    sparse = (_git_config_value(repo, "core.sparseCheckout") or "").lower()
    if sparse in {"true", "1", "yes", "on"}:
        raise GrokWorkspaceGuardError("sparse checkout masking is not allowed")
    sparse_file = repo / ".git" / "info" / "sparse-checkout"
    if sparse_file.is_symlink() or sparse_file.is_file():
        # A present sparse-checkout definition is treated as active masking risk.
        raise GrokWorkspaceGuardError("sparse checkout masking is not allowed")


def assert_checkout_is_safe(repo: Path) -> None:
    # Force untracked reporting even when status.showUntrackedFiles=no.
    entries = tuple(
        entry
        for entry in run_git(
            repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).split("\0")
        if entry
    )
    if entries and not all(_is_approved_user_owned_status(entry) for entry in entries):
        raise GrokWorkspaceGuardError(
            "checkout contains changes outside the approved user-owned state"
        )
    assert_no_index_masking(repo)
    assert_not_sparse_checkout(repo)


def assert_allowed_paths_have_no_symlinks(repo: Path, allowed_paths: tuple[str, ...]) -> None:
    for relative in allowed_paths:
        if path_has_symlink_component(repo, relative):
            raise GrokWorkspaceGuardError("allowed path must not include symlink components")
