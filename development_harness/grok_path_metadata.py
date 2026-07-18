"""Metadata-only inventory of user-owned and ignored working-tree paths."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Final

_USER_OWNED_ROOTS: Final = (".hermes", ".omo")
type FileMeta = tuple[int, int, int, int, int]
type PathMetaMap = tuple[tuple[str, FileMeta], ...]
type OwnedMetaMap = tuple[tuple[str, PathMetaMap], ...]
type GitRunner = Callable[..., str]


def file_metadata(path: Path) -> FileMeta:
    metadata = path.lstat()
    return (
        stat.S_IFMT(metadata.st_mode) | stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def format_meta(relative: str, meta: FileMeta) -> str:
    mode, uid, size, mtime_ns, ctime_ns = meta
    return f"{relative}:{mode}:{uid}:{size}:{mtime_ns}:{ctime_ns}"


def optional_path_meta(path: Path, relative: str) -> tuple[str, ...] | None:
    if path.is_symlink() or path.exists():
        return (format_meta(relative, file_metadata(path)),)
    return None


def walk_metadata(root: Path) -> PathMetaMap:
    # Include symlink roots/entries via lstat only; never follow or read targets.
    if root.is_symlink():
        return ((".", file_metadata(root)),)
    if not root.exists():
        return ()
    collected: dict[str, FileMeta] = {".": file_metadata(root)}
    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_root)
        relative_root = current.relative_to(root).as_posix()
        key_root = "." if relative_root == "." else relative_root
        collected[key_root] = file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            relative = child.relative_to(root).as_posix()
            collected[relative] = file_metadata(child)
            if not child.is_symlink():
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            relative = child.relative_to(root).as_posix()
            collected[relative] = file_metadata(child)
    return tuple(sorted(collected.items()))


def user_owned_metadata(repo: Path) -> OwnedMetaMap:
    return tuple((name, walk_metadata(repo / name)) for name in _USER_OWNED_ROOTS)


def _ignored_entry_key(relative: str) -> str:
    return relative.rstrip("/")


def _record_ignored_tree(repo: Path, relative_root: str, collected: dict[str, FileMeta]) -> None:
    """Record an ignored path and nested entries without following links."""

    root_path = repo / relative_root
    if not (root_path.exists() or root_path.is_symlink()):
        return
    collected[relative_root] = file_metadata(root_path)
    if root_path.is_symlink() or not root_path.is_dir():
        return
    for current_root, dirnames, filenames in os.walk(root_path, topdown=True, followlinks=False):
        current = Path(current_root)
        relative = current.relative_to(repo).as_posix()
        collected[relative] = file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            child_rel = child.relative_to(repo).as_posix()
            collected[child_rel] = file_metadata(child)
            if not child.is_symlink():
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            collected[child.relative_to(repo).as_posix()] = file_metadata(child)


def ignored_metadata(repo: Path, *, run_git: GitRunner) -> PathMetaMap:
    """Inventory ignored files and directories, including empty/nested ignored dirs.

    ``run_git`` is injected to avoid circular imports with the fingerprint module.
    """

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
