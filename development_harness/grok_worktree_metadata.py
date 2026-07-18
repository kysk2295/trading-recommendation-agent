"""Visible worktree and empty-directory metadata for in-place Grok workers."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Final

from development_harness.grok_path_metadata import (
    PathMetadataError,
    PathMetaMap,
    file_metadata,
)

_USER_OWNED_ROOTS: Final = (".hermes", ".omo")
_SKIP_ROOT_NAMES: Final = frozenset({".git", *_USER_OWNED_ROOTS})
type GitRunner = Callable[..., str]


class WorktreeMetadataError(RuntimeError):
    """Raised when visible worktree metadata cannot be inventoried safely."""


class EmptyDirectoryInventoryError(RuntimeError):
    """Raised when unignored empty-directory inventory diverges unexpectedly."""


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


def _ignored_entry_key(relative: str) -> str:
    return relative.rstrip("/")


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


def _under_allowed_exact(relative: str, allowed_exact: frozenset[str]) -> bool:
    return any(relative.startswith(f"{path}/") for path in allowed_exact)


def _raise_walk_error(error: OSError) -> None:
    raise WorktreeMetadataError("worktree metadata walk failed") from error


def visible_worktree_metadata(
    repo: Path,
    *,
    allowed_paths: Iterable[str],
    run_git: GitRunner,
) -> PathMetaMap:
    """Fingerprint metadata for every visible worktree entry except exclusions.

    Excludes user-owned roots (``.hermes`` / ``.omo``), ignored paths, exact
    allowed paths (and paths under them), and required parent directory nodes of
    allowed paths. Parent directories are still walked so non-allowed siblings
    remain fingerprinted. Repository root metadata is omitted because any child
    create/delete updates it. Every walk/stat/enumeration error fails closed.
    Symlink entries are recorded via ``lstat`` only and never followed.
    """

    allowed_tuple = tuple(allowed_paths)
    allowed_exact = frozenset(allowed_tuple)
    allowed_parents = allowed_parent_directories(allowed_tuple)
    ignored_keys = _ignored_path_keys(repo, run_git=run_git)
    collected: dict[str, tuple[int, int, int, int, int]] = {}

    def _should_skip_tree(relative: str) -> bool:
        return _is_ignored_or_under_ignored(relative, ignored_keys) or (
            relative in allowed_exact or _under_allowed_exact(relative, allowed_exact)
        )

    def _record_unless_excluded(relative: str, path: Path) -> None:
        if relative in allowed_parents:
            return
        if relative in allowed_exact or _under_allowed_exact(relative, allowed_exact):
            return
        if _is_ignored_or_under_ignored(relative, ignored_keys):
            return
        try:
            collected[relative] = file_metadata(path)
        except PathMetadataError as error:
            raise WorktreeMetadataError("worktree metadata walk failed") from error

    try:
        walker = os.walk(repo, topdown=True, followlinks=False, onerror=_raise_walk_error)
    except OSError as error:
        raise WorktreeMetadataError("worktree metadata walk failed") from error

    for current_root, dirnames, filenames in walker:
        current = Path(current_root)
        if current == repo:
            keep: list[str] = []
            for name in sorted(dirnames):
                if name in _SKIP_ROOT_NAMES:
                    continue
                child = current / name
                if _should_skip_tree(name):
                    continue
                _record_unless_excluded(name, child)
                try:
                    is_symlink = child.is_symlink()
                except OSError as error:
                    raise WorktreeMetadataError("worktree metadata walk failed") from error
                if not is_symlink:
                    keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                child = current / name
                _record_unless_excluded(name, child)
            continue

        try:
            relative = current.relative_to(repo).as_posix()
        except ValueError as error:
            raise WorktreeMetadataError("worktree metadata walk failed") from error

        if _should_skip_tree(relative):
            dirnames[:] = []
            continue

        _record_unless_excluded(relative, current)

        keep_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            child_rel = f"{relative}/{name}"
            if _should_skip_tree(child_rel):
                continue
            _record_unless_excluded(child_rel, child)
            try:
                is_symlink = child.is_symlink()
            except OSError as error:
                raise WorktreeMetadataError("worktree metadata walk failed") from error
            if not is_symlink:
                keep_dirs.append(name)
        dirnames[:] = keep_dirs

        for name in sorted(filenames):
            child = current / name
            child_rel = f"{relative}/{name}"
            _record_unless_excluded(child_rel, child)

    return tuple(sorted(collected.items()))


def empty_unignored_directories(repo: Path, *, run_git: GitRunner) -> tuple[str, ...]:
    """Inventory empty unignored directories outside user-owned roots.

    Ignored trees (including empty ignored directories) are excluded so their
    handling remains solely in ignored-path metadata. Every walk/stat/
    enumeration error fails closed.
    """

    ignored_keys = _ignored_path_keys(repo, run_git=run_git)
    empty: list[str] = []
    try:
        walker = os.walk(repo, topdown=True, followlinks=False, onerror=_raise_walk_error)
    except OSError as error:
        raise WorktreeMetadataError("worktree metadata walk failed") from error
    for current_root, dirnames, filenames in walker:
        current = Path(current_root)
        if current == repo:
            keep: list[str] = []
            for name in sorted(dirnames):
                if name in _SKIP_ROOT_NAMES:
                    continue
                child = current / name
                try:
                    is_symlink = child.is_symlink()
                except OSError as error:
                    raise WorktreeMetadataError("worktree metadata walk failed") from error
                if is_symlink:
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
            try:
                is_symlink = child.is_symlink()
            except OSError as error:
                raise WorktreeMetadataError("worktree metadata walk failed") from error
            if is_symlink or _is_ignored_or_under_ignored(child_rel, ignored_keys):
                continue
            keep_dirs.append(name)
        dirnames[:] = keep_dirs

        try:
            has_children = next(current.iterdir(), None) is not None
        except OSError as error:
            raise WorktreeMetadataError("worktree metadata walk failed") from error
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
            continue
        raise EmptyDirectoryInventoryError(
            "unignored empty directory deleted under the worker"
        )


def required_parents_for(allowed_paths: Iterable[str]) -> frozenset[str]:
    """Expose required parent directories for callers/tests."""

    return allowed_parent_directories(allowed_paths)
