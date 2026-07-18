"""Metadata-only workspace fingerprinting for in-place Grok workers.

Captures immutable Git database, logical index, user-owned, and ignored-path
inventories without reading file contents. Symlink directories are recorded
once and never followed.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_GIT_TIMEOUT_SECONDS: Final = 30
_USER_OWNED_ROOTS: Final = (".hermes", ".omo")
type FileMeta = tuple[int, int, int, int, int]
type PathMetaMap = tuple[tuple[str, FileMeta], ...]
type OwnedMetaMap = tuple[tuple[str, PathMetaMap], ...]


class GrokWorkspaceGuardError(RuntimeError):
    """Raised when the repository is not safe for an in-place worker."""


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise GrokWorkspaceGuardError("Git preflight failed")
    return completed.stdout


def _file_metadata(path: Path) -> FileMeta:
    metadata = path.lstat()
    return (
        stat.S_IFMT(metadata.st_mode) | stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _walk_metadata(root: Path) -> PathMetaMap:
    # Include symlink roots/entries via lstat only; never follow or read targets.
    if root.is_symlink():
        return ((".", _file_metadata(root)),)
    if not root.exists():
        return ()
    collected: dict[str, FileMeta] = {".": _file_metadata(root)}
    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_root)
        relative_root = current.relative_to(root).as_posix()
        key_root = "." if relative_root == "." else relative_root
        collected[key_root] = _file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            relative = child.relative_to(root).as_posix()
            collected[relative] = _file_metadata(child)
            if not child.is_symlink():
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            relative = child.relative_to(root).as_posix()
            collected[relative] = _file_metadata(child)
    return tuple(sorted(collected.items()))


def _user_owned_metadata(repo: Path) -> OwnedMetaMap:
    return tuple((name, _walk_metadata(repo / name)) for name in _USER_OWNED_ROOTS)


def _ignored_entry_key(relative: str) -> str:
    return relative.rstrip("/")


def _record_ignored_tree(repo: Path, relative_root: str, collected: dict[str, FileMeta]) -> None:
    """Record an ignored path and nested entries without following links."""

    root_path = repo / relative_root
    if not (root_path.exists() or root_path.is_symlink()):
        return
    collected[relative_root] = _file_metadata(root_path)
    if root_path.is_symlink() or not root_path.is_dir():
        return
    for current_root, dirnames, filenames in os.walk(root_path, topdown=True, followlinks=False):
        current = Path(current_root)
        relative = current.relative_to(repo).as_posix()
        collected[relative] = _file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            child_rel = child.relative_to(repo).as_posix()
            collected[child_rel] = _file_metadata(child)
            if not child.is_symlink():
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            collected[child.relative_to(repo).as_posix()] = _file_metadata(child)


def _ignored_metadata(repo: Path) -> PathMetaMap:
    """Inventory ignored files and directories, including empty/nested ignored dirs."""

    file_output = run_git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    dir_output = run_git(
        repo,
        "ls-files",
        "-z",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
    )
    collected: dict[str, FileMeta] = {}
    for relative in (*file_output.split("\0"), *dir_output.split("\0")):
        if not relative:
            continue
        key = _ignored_entry_key(relative)
        if not key or key.split("/", 1)[0] in _USER_OWNED_ROOTS:
            continue
        _record_ignored_tree(repo, key, collected)
    return tuple(sorted(collected.items()))


def _format_meta(relative: str, meta: FileMeta) -> str:
    mode, uid, size, mtime_ns, ctime_ns = meta
    return f"{relative}:{mode}:{uid}:{size}:{mtime_ns}:{ctime_ns}"


def _optional_path_meta(path: Path, relative: str) -> tuple[str, ...] | None:
    if path.is_symlink() or path.exists():
        return (_format_meta(relative, _file_metadata(path)),)
    return None


def _git_hooks_metadata(hooks_root: Path) -> tuple[str, ...]:
    if hooks_root.is_symlink():
        return (_format_meta("hooks", _file_metadata(hooks_root)),)
    if not hooks_root.exists():
        return ()
    collected: dict[str, FileMeta] = {"hooks": _file_metadata(hooks_root)}
    for current_root, dirnames, filenames in os.walk(hooks_root, topdown=True, followlinks=False):
        current = Path(current_root)
        relative_root = current.relative_to(hooks_root).as_posix()
        key_root = "hooks" if relative_root == "." else f"hooks/{relative_root}"
        collected[key_root] = _file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            relative = (
                f"hooks/{name}" if relative_root == "." else f"hooks/{relative_root}/{name}"
            )
            collected[relative] = _file_metadata(child)
            if not child.is_symlink():
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            relative = (
                f"hooks/{name}" if relative_root == "." else f"hooks/{relative_root}/{name}"
            )
            collected[relative] = _file_metadata(child)
    return tuple(_format_meta(relative, meta) for relative, meta in sorted(collected.items()))


def _git_control_metadata(repo: Path) -> tuple[str, ...]:
    """Immutable metadata-only view of local Git control files (no content reads)."""

    git_dir = repo / ".git"
    entries: list[str] = []
    for relative in ("HEAD", "config", "config.worktree", "packed-refs", "info/exclude"):
        formatted = _optional_path_meta(git_dir / relative, relative)
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
        _format_meta(
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


def _git_object_entries(objects_root: Path) -> tuple[str, ...]:
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


def git_index_fingerprint(repo: Path) -> str:
    """Stable logical index entries and flags (assume-unchanged / skip-worktree)."""

    output = run_git(repo, "ls-files", "--stage", "-v", "-z")
    return hashlib.sha256(output.encode()).hexdigest()


def git_database_fingerprint(repo: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(run_git(repo, "show-ref", "--head").encode())
    hasher.update(b"\0")
    hasher.update(run_git(repo, "reflog", "--all", "--date=unix").encode())
    object_entries = _git_object_entries(repo / ".git" / "objects")
    hasher.update(b"\0")
    hasher.update("\n".join(object_entries).encode())
    hasher.update(b"\0")
    hasher.update("\n".join(_git_control_metadata(repo)).encode())
    return hasher.hexdigest()


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
        user_owned=_user_owned_metadata(repo),
        ignored=_ignored_metadata(repo),
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
    if _user_owned_metadata(repo) != snapshot.user_owned:
        raise GrokWorkspaceGuardError("user-owned state changed under the worker")
    if _ignored_metadata(repo) != snapshot.ignored:
        raise GrokWorkspaceGuardError("ignored path metadata changed under the worker")
