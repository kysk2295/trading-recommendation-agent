"""Metadata-only full Git topology and logical index fingerprints (no content reads)."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

from development_harness.grok_path_metadata import FileMeta, format_meta

type GitRunner = Callable[..., str]

# Binary index blob is intentionally omitted from topology fingerprints; logical
# index entries come from ``git ls-files`` instead.
_BINARY_INDEX_NAME: str = "index"
_LOCK_NAME: str = "index.lock"
_SHARED_INDEX_PREFIX: str = "sharedindex."

# In-progress Git operation state at the repository .git root (fail-closed).
_OPERATION_STATE_NAMES: frozenset[str] = frozenset(
    {
        "AUTO_MERGE",
        "BISECT_EXPECTED_REV",
        "BISECT_LOG",
        "BISECT_NAMES",
        "BISECT_START",
        "BISECT_TERMS",
        "CHERRY_PICK_HEAD",
        "MERGE_HEAD",
        "MERGE_MODE",
        "MERGE_MSG",
        "REBASE_HEAD",
        "REVERT_HEAD",
        "SQUASH_MSG",
        "rebase-apply",
        "rebase-merge",
        "sequencer",
    }
)


class GitControlError(RuntimeError):
    """Raised when Git control topology is unsafe or cannot be inventoried."""


def _raise_walk_error(error: OSError) -> None:
    raise GitControlError("git topology walk failed") from error


def _reject_git_entry(relative: str, name: str) -> None:
    if name == _LOCK_NAME or relative == _LOCK_NAME:
        raise GitControlError("git index.lock is not allowed")
    if name.startswith(_SHARED_INDEX_PREFIX) or relative.startswith(_SHARED_INDEX_PREFIX):
        raise GitControlError("git shared index state is not allowed")
    # Operation state is only meaningful at the .git root (no nested path).
    if "/" not in relative and name in _OPERATION_STATE_NAMES:
        raise GitControlError("git operation state is not allowed")


def _entry_meta(path: Path, *, relative: str) -> FileMeta:
    try:
        meta = path.lstat()
    except OSError as error:
        raise GitControlError("git topology walk failed") from error
    if stat.S_ISLNK(meta.st_mode):
        raise GitControlError("git internal symlink is not allowed")
    if (
        stat.S_ISREG(meta.st_mode)
        and not relative.startswith("objects/")
        and (meta.st_uid != os.getuid() or meta.st_nlink != 1)
    ):
        raise GitControlError("git control file must be current-user-owned with one hard link")
    return (
        stat.S_IFMT(meta.st_mode) | stat.S_IMODE(meta.st_mode),
        meta.st_uid,
        meta.st_size,
        meta.st_mtime_ns,
        meta.st_ctime_ns,
    )


def _record_git_entry(
    collected: dict[str, FileMeta],
    *,
    relative: str,
    name: str,
    path: Path,
) -> None:
    _reject_git_entry(relative, name)
    if relative == _BINARY_INDEX_NAME:
        _ = _entry_meta(path, relative=relative)
        return
    collected[relative] = _entry_meta(path, relative=relative)


def git_topology_metadata(repo: Path) -> tuple[str, ...]:
    """Metadata-only inventory of the full ``.git`` tree (excluding binary index).

    Rejects ``index.lock``, ``sharedindex.*``, in-progress operation state, every
    internal symlink, and a symlinked objects root. Every walk/stat error fails
    closed. File contents are never read. The ``.git`` directory node itself is
    omitted so ordinary index refreshes that only bump the directory mtime do not
    false-positive.
    """

    git_dir = repo / ".git"
    try:
        root_meta = git_dir.lstat()
    except OSError as error:
        raise GitControlError("git topology walk failed") from error
    if stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode):
        raise GitControlError("git directory topology is not usable")

    objects_root = git_dir / "objects"
    try:
        if objects_root.is_symlink():
            raise GitControlError("git objects root must not be a symlink")
    except OSError as error:
        raise GitControlError("git topology walk failed") from error

    collected: dict[str, FileMeta] = {}

    try:
        walker = os.walk(git_dir, topdown=True, followlinks=False, onerror=_raise_walk_error)
    except OSError as error:
        raise GitControlError("git topology walk failed") from error

    for current_root, dirnames, filenames in walker:
        current = Path(current_root)
        try:
            relative_root = current.relative_to(git_dir).as_posix()
        except ValueError as error:
            raise GitControlError("git topology walk failed") from error

        # Record nested directory nodes (not the .git root) once when entered.
        if relative_root != ".":
            _record_git_entry(
                collected,
                relative=relative_root,
                name=current.name,
                path=current,
            )

        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            relative = name if relative_root == "." else f"{relative_root}/{name}"
            _reject_git_entry(relative, name)
            try:
                meta = child.lstat()
            except OSError as error:
                raise GitControlError("git topology walk failed") from error
            if stat.S_ISLNK(meta.st_mode):
                raise GitControlError("git internal symlink is not allowed")
            # Directory metadata is recorded when the walk enters the child.
            keep_dirs.append(name)
        dirnames[:] = keep_dirs

        for name in sorted(filenames):
            child = current / name
            relative = name if relative_root == "." else f"{relative_root}/{name}"
            _record_git_entry(collected, relative=relative, name=name, path=child)

    return tuple(format_meta(relative, meta) for relative, meta in sorted(collected.items()))


def git_control_metadata(repo: Path) -> tuple[str, ...]:
    """Backward-compatible alias for full ``.git`` topology metadata inventory."""

    return git_topology_metadata(repo)


def git_index_fingerprint(repo: Path, *, run_git: GitRunner) -> str:
    """Stable logical index entries and flags (assume-unchanged / skip-worktree)."""

    output = run_git(repo, "ls-files", "--stage", "-v", "-z")
    return hashlib.sha256(output.encode()).hexdigest()


def git_database_fingerprint(repo: Path, *, run_git: GitRunner) -> str:
    hasher = hashlib.sha256()
    hasher.update(run_git(repo, "show-ref", "--head").encode())
    hasher.update(b"\0")
    hasher.update(run_git(repo, "reflog", "--all", "--date=unix").encode())
    hasher.update(b"\0")
    # Full metadata-only .git topology/state inventory (binary index excluded).
    hasher.update("\n".join(git_topology_metadata(repo)).encode())
    return hasher.hexdigest()
