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

__all__ = (
    "OwnedMetaMap",
    "PathMetaMap",
    "PathMetadataError",
    "file_metadata",
    "format_meta",
    "ignored_metadata",
    "optional_path_meta",
    "user_owned_metadata",
    "walk_metadata",
)


class PathMetadataError(RuntimeError):
    """Raised when path metadata cannot be collected fail-closed."""


def file_metadata(path: Path) -> FileMeta:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PathMetadataError("path metadata walk failed") from error
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
    try:
        meta = file_metadata(path)
    except PathMetadataError:
        return None
    return (format_meta(relative, meta),)


def _raise_walk_error(error: OSError) -> None:
    raise PathMetadataError("path metadata walk failed") from error


def walk_metadata(root: Path) -> PathMetaMap:
    # Include symlink roots/entries via lstat only; never follow or read targets.
    try:
        root_meta = root.lstat()
    except OSError:
        return ()
    if stat.S_ISLNK(root_meta.st_mode):
        return ((".", file_metadata(root)),)
    if not stat.S_ISDIR(root_meta.st_mode):
        return ((".", file_metadata(root)),)
    collected: dict[str, FileMeta] = {".": file_metadata(root)}
    try:
        walker = os.walk(root, topdown=True, followlinks=False, onerror=_raise_walk_error)
    except OSError as error:
        raise PathMetadataError("path metadata walk failed") from error
    for current_root, dirnames, filenames in walker:
        current = Path(current_root)
        relative_root = current.relative_to(root).as_posix()
        key_root = "." if relative_root == "." else relative_root
        collected[key_root] = file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            relative = child.relative_to(root).as_posix()
            collected[relative] = file_metadata(child)
            try:
                is_symlink = child.is_symlink()
            except OSError as error:
                raise PathMetadataError("path metadata walk failed") from error
            if not is_symlink:
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
    try:
        root_meta = root_path.lstat()
    except OSError:
        return
    collected[relative_root] = file_metadata(root_path)
    if stat.S_ISLNK(root_meta.st_mode) or not stat.S_ISDIR(root_meta.st_mode):
        return
    try:
        walker = os.walk(root_path, topdown=True, followlinks=False, onerror=_raise_walk_error)
    except OSError as error:
        raise PathMetadataError("path metadata walk failed") from error
    for current_root, dirnames, filenames in walker:
        current = Path(current_root)
        relative = current.relative_to(repo).as_posix()
        collected[relative] = file_metadata(current)
        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            child_rel = child.relative_to(repo).as_posix()
            collected[child_rel] = file_metadata(child)
            try:
                is_symlink = child.is_symlink()
            except OSError as error:
                raise PathMetadataError("path metadata walk failed") from error
            if not is_symlink:
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            collected[child.relative_to(repo).as_posix()] = file_metadata(child)


def _ignored_path_keys(repo: Path, *, run_git: GitRunner) -> frozenset[str]:
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
    keys: set[str] = set()
    for relative in (*file_output.split("\0"), *dir_output.split("\0")):
        if not relative:
            continue
        key = _ignored_entry_key(relative)
        if key and key.split("/", 1)[0] not in _USER_OWNED_ROOTS:
            keys.add(key)
    return frozenset(keys)


def ignored_metadata(repo: Path, *, run_git: GitRunner) -> PathMetaMap:
    """Inventory ignored files and directories, including empty/nested ignored dirs.

    ``run_git`` is injected to avoid circular imports with the fingerprint module.
    """

    collected: dict[str, FileMeta] = {}
    for key in sorted(_ignored_path_keys(repo, run_git=run_git)):
        _record_ignored_tree(repo, key, collected)
    return tuple(sorted(collected.items()))
