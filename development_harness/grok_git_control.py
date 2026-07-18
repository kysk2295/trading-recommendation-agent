"""Git control-path and object-store metadata fingerprints (no content reads)."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

from development_harness.grok_path_metadata import (
    FileMeta,
    file_metadata,
    format_meta,
    optional_path_meta,
)

type GitRunner = Callable[..., str]


def _git_hooks_metadata(hooks_root: Path) -> tuple[str, ...]:
    if hooks_root.is_symlink():
        return (format_meta("hooks", file_metadata(hooks_root)),)
    if not hooks_root.exists():
        return ()
    collected: dict[str, FileMeta] = {"hooks": file_metadata(hooks_root)}
    for current_root, dirnames, filenames in os.walk(hooks_root, topdown=True, followlinks=False):
        current = Path(current_root)
        relative_root = current.relative_to(hooks_root).as_posix()
        key_root = "hooks" if relative_root == "." else f"hooks/{relative_root}"
        collected[key_root] = file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            relative = (
                f"hooks/{name}" if relative_root == "." else f"hooks/{relative_root}/{name}"
            )
            collected[relative] = file_metadata(child)
            if not child.is_symlink():
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            relative = (
                f"hooks/{name}" if relative_root == "." else f"hooks/{relative_root}/{name}"
            )
            collected[relative] = file_metadata(child)
    return tuple(format_meta(relative, meta) for relative, meta in sorted(collected.items()))


def git_control_metadata(repo: Path) -> tuple[str, ...]:
    """Immutable metadata-only view of local Git control files (no content reads)."""

    git_dir = repo / ".git"
    entries: list[str] = []
    for relative in (
        "HEAD",
        "config",
        "config.worktree",
        "packed-refs",
        "info/exclude",
        "shallow",
        "info/grafts",
    ):
        formatted = optional_path_meta(git_dir / relative, relative)
        if formatted is not None:
            entries.extend(formatted)
    entries.extend(_git_hooks_metadata(git_dir / "hooks"))
    return tuple(entries)


def _append_object_entry(object_entries: list[str], objects_root: Path, child: Path) -> None:
    try:
        meta = child.lstat()
    except OSError:
        return
    if not (stat.S_ISREG(meta.st_mode) or stat.S_ISLNK(meta.st_mode)):
        return
    relative = child.relative_to(objects_root).as_posix()
    object_entries.append(
        format_meta(
            relative,
            (
                stat.S_IFMT(meta.st_mode) | stat.S_IMODE(meta.st_mode),
                meta.st_uid,
                meta.st_size,
                meta.st_mtime_ns,
                meta.st_ctime_ns,
            ),
        )
    )


def git_object_entries(objects_root: Path) -> tuple[str, ...]:
    """Metadata inventory of object store files and symlink entries (no content reads)."""

    if not objects_root.is_dir() or objects_root.is_symlink():
        return ()
    object_entries: list[str] = []
    for current_root, dirnames, filenames in os.walk(objects_root, topdown=True, followlinks=False):
        current = Path(current_root)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            try:
                meta = child.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(meta.st_mode):
                _append_object_entry(object_entries, objects_root, child)
                continue
            keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            _append_object_entry(object_entries, objects_root, current / name)
    return tuple(sorted(object_entries))


def git_index_fingerprint(repo: Path, *, run_git: GitRunner) -> str:
    """Stable logical index entries and flags (assume-unchanged / skip-worktree)."""

    output = run_git(repo, "ls-files", "--stage", "-v", "-z")
    return hashlib.sha256(output.encode()).hexdigest()


def git_database_fingerprint(repo: Path, *, run_git: GitRunner) -> str:
    hasher = hashlib.sha256()
    hasher.update(run_git(repo, "show-ref", "--head").encode())
    hasher.update(b"\0")
    hasher.update(run_git(repo, "reflog", "--all", "--date=unix").encode())
    object_entries = git_object_entries(repo / ".git" / "objects")
    hasher.update(b"\0")
    hasher.update("\n".join(object_entries).encode())
    hasher.update(b"\0")
    hasher.update("\n".join(git_control_metadata(repo)).encode())
    return hasher.hexdigest()
