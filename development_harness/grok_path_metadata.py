"""Metadata-only inventory of user-owned and ignored working-tree paths."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Final

_USER_OWNED_ROOTS: Final = (".hermes", ".omo")
_SKIP_ROOT_NAMES: Final = frozenset({".git", *_USER_OWNED_ROOTS})
type FileMeta = tuple[int, int, int, int, int]
type PathMetaMap = tuple[tuple[str, FileMeta], ...]
type OwnedMetaMap = tuple[tuple[str, PathMetaMap], ...]
type GitRunner = Callable[..., str]

__all__ = (
    "EmptyDirectoryInventoryError",
    "OwnedMetaMap",
    "PathMetaMap",
    "allowed_parent_directories",
    "empty_unignored_directories",
    "file_metadata",
    "format_meta",
    "ignored_metadata",
    "optional_path_meta",
    "user_owned_metadata",
    "verify_empty_directory_inventory",
    "walk_metadata",
)


class EmptyDirectoryInventoryError(RuntimeError):
    """Raised when unignored empty-directory inventory diverges unexpectedly."""


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


def _is_ignored_or_under_ignored(relative: str, ignored_keys: frozenset[str]) -> bool:
    if relative in ignored_keys:
        return True
    return any(
        relative.startswith(f"{key}/") for key in ignored_keys if key and not key.endswith("/")
    )


def allowed_parent_directories(allowed_paths: Iterable[str]) -> frozenset[str]:
    """Return directory ancestors required by allowed file paths (not the files)."""

    parents: set[str] = set()
    for relative in allowed_paths:
        path = PurePosixPath(relative)
        for ancestor in path.parents:
            if ancestor == PurePosixPath("."):
                break
            parents.add(ancestor.as_posix())
    return frozenset(parents)


def empty_unignored_directories(repo: Path, *, run_git: GitRunner) -> tuple[str, ...]:
    """Inventory empty unignored directories outside user-owned roots.

    Ignored trees (including empty ignored directories) are excluded so their
    handling remains solely in :func:`ignored_metadata`.
    """

    ignored_keys = _ignored_path_keys(repo, run_git=run_git)
    empty: list[str] = []
    for current_root, dirnames, filenames in os.walk(repo, topdown=True, followlinks=False):
        current = Path(current_root)
        if current == repo:
            keep: list[str] = []
            for name in sorted(dirnames):
                if name in _SKIP_ROOT_NAMES:
                    continue
                child = current / name
                if child.is_symlink():
                    continue
                keep.append(name)
            dirnames[:] = keep
            continue

        relative = current.relative_to(repo).as_posix()
        if _is_ignored_or_under_ignored(relative, ignored_keys):
            dirnames[:] = []
            continue

        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            child_rel = child.relative_to(repo).as_posix()
            if child.is_symlink() or _is_ignored_or_under_ignored(child_rel, ignored_keys):
                continue
            keep_dirs.append(name)
        dirnames[:] = keep_dirs

        # Empty means no children at all (including ignored/symlink-only entries).
        try:
            has_children = next(current.iterdir(), None) is not None
        except OSError:
            continue
        if not has_children and not filenames:
            empty.append(relative)
    return tuple(sorted(empty))


def verify_empty_directory_inventory(
    repo: Path,
    expected: tuple[str, ...],
    *,
    allowed_paths: tuple[str, ...] | None,
    run_git: GitRunner,
) -> None:
    """Reject unexpected creation/deletion of unignored empty directories.

    When ``allowed_paths`` is provided, only missing parent directories required by
    those paths may appear as new empty directories. Launch-time checks pass
    ``allowed_paths=None`` and require an exact inventory match.
    """

    actual = set(empty_unignored_directories(repo, run_git=run_git))
    expected_set = set(expected)
    if allowed_paths is None:
        if actual != expected_set:
            raise EmptyDirectoryInventoryError("unignored empty directory inventory changed")
        return

    allowed_parents = allowed_parent_directories(allowed_paths)
    for relative in sorted(actual - expected_set):
        if relative not in allowed_parents:
            raise EmptyDirectoryInventoryError(
                "unignored empty directory created under the worker"
            )
    for relative in sorted(expected_set - actual):
        path = repo / relative
        try:
            still_directory = path.is_dir() and not path.is_symlink()
        except OSError as error:
            raise EmptyDirectoryInventoryError(
                "unignored empty directory deleted under the worker"
            ) from error
        if still_directory:
            # Directory still exists but is no longer empty (for example after an
            # allowed file write). That is not a pure deletion of the empty dir.
            continue
        raise EmptyDirectoryInventoryError(
            "unignored empty directory deleted under the worker"
        )


def ignored_metadata(repo: Path, *, run_git: GitRunner) -> PathMetaMap:
    """Inventory ignored files and directories, including empty/nested ignored dirs.

    ``run_git`` is injected to avoid circular imports with the fingerprint module.
    """

    collected: dict[str, FileMeta] = {}
    for key in sorted(_ignored_path_keys(repo, run_git=run_git)):
        _record_ignored_tree(repo, key, collected)
    return tuple(sorted(collected.items()))
