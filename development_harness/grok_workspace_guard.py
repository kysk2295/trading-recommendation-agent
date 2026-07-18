from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

_GIT_TIMEOUT_SECONDS: Final = 30
_USER_OWNED_ROOTS: Final = (".hermes", ".omo")
_USER_OWNED_STATUS_ENTRIES: Final = frozenset({"?? .hermes/", "?? .omo/"})
type FileMeta = tuple[int, int, int, int, int]
type PathMetaMap = tuple[tuple[str, FileMeta], ...]
type OwnedMetaMap = tuple[tuple[str, PathMetaMap], ...]


class GrokWorkspaceGuardError(RuntimeError):
    """Raised when the repository is not safe for an in-place worker."""


def _run_git(repo: Path, *args: str) -> str:
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


def assert_main_repository_root(repo: Path) -> Path:
    """Require a non-linked, non-symlink main-branch repository root."""

    if repo.is_symlink():
        raise GrokWorkspaceGuardError("repository path must not include symlink components")
    try:
        resolved = repo.resolve(strict=True)
    except OSError as error:
        raise GrokWorkspaceGuardError("repository path is not usable") from error
    git_dir = resolved / ".git"
    if git_dir.is_symlink() or git_dir.is_file() or not git_dir.is_dir():
        raise GrokWorkspaceGuardError("linked worktree checkouts are not allowed")
    root = Path(_run_git(resolved, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
    if root != resolved:
        raise GrokWorkspaceGuardError("task runner requires the repository root")
    git_dir_value = Path(_run_git(resolved, "rev-parse", "--git-dir").strip())
    git_common = Path(_run_git(resolved, "rev-parse", "--git-common-dir").strip())
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
    if _run_git(resolved, "branch", "--show-current").strip() != "main":
        raise GrokWorkspaceGuardError("task runner requires the main branch")
    return root


def assert_checkout_is_safe(repo: Path) -> None:
    entries = tuple(
        entry for entry in _run_git(repo, "status", "--porcelain=v1", "-z").split("\0") if entry
    )
    if entries and not set(entries).issubset(_USER_OWNED_STATUS_ENTRIES):
        raise GrokWorkspaceGuardError("checkout contains changes outside the approved user-owned state")


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
        # Do not descend into symlinked directories; still record them once above.
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            relative = child.relative_to(root).as_posix()
            collected[relative] = _file_metadata(child)
    return tuple(sorted(collected.items()))


def _user_owned_metadata(repo: Path) -> OwnedMetaMap:
    return tuple((name, _walk_metadata(repo / name)) for name in _USER_OWNED_ROOTS)


def _ignored_metadata(repo: Path) -> PathMetaMap:
    output = _run_git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    collected: list[tuple[str, FileMeta]] = []
    for relative in output.split("\0"):
        if not relative or relative.split("/", 1)[0] in _USER_OWNED_ROOTS:
            continue
        path = repo / relative
        if path.exists() or path.is_symlink():
            collected.append((relative, _file_metadata(path)))
    return tuple(sorted(collected))


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
                f"hooks/{name}"
                if relative_root == "."
                else f"hooks/{relative_root}/{name}"
            )
            collected[relative] = _file_metadata(child)
            if not child.is_symlink():
                keep_dirs.append(name)
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            child = current / name
            relative = (
                f"hooks/{name}"
                if relative_root == "."
                else f"hooks/{relative_root}/{name}"
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


def _git_database_fingerprint(repo: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(_run_git(repo, "show-ref", "--head").encode())
    hasher.update(b"\0")
    hasher.update(_run_git(repo, "reflog", "--all", "--date=unix").encode())
    objects_root = repo / ".git" / "objects"
    object_entries: list[str] = []
    if objects_root.is_dir() and not objects_root.is_symlink():
        for path in sorted(objects_root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                meta = _file_metadata(path)
                relative = path.relative_to(objects_root).as_posix()
                object_entries.append(_format_meta(relative, meta))
    hasher.update(b"\0")
    hasher.update("\n".join(object_entries).encode())
    # Local control plane (config/hooks/exclude). Never includes .git/index.
    hasher.update(b"\0")
    hasher.update("\n".join(_git_control_metadata(repo)).encode())
    return hasher.hexdigest()


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


def assert_allowed_paths_have_no_symlinks(repo: Path, allowed_paths: tuple[str, ...]) -> None:
    for relative in allowed_paths:
        if path_has_symlink_component(repo, relative):
            raise GrokWorkspaceGuardError("allowed path must not include symlink components")


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    head: str
    refs_and_objects: str
    user_owned: OwnedMetaMap
    ignored: PathMetaMap


def capture_workspace_snapshot(repo: Path) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        head=_run_git(repo, "rev-parse", "HEAD").strip(),
        refs_and_objects=_git_database_fingerprint(repo),
        user_owned=_user_owned_metadata(repo),
        ignored=_ignored_metadata(repo),
    )


def verify_workspace_snapshot(repo: Path, snapshot: WorkspaceSnapshot) -> None:
    if _run_git(repo, "rev-parse", "HEAD").strip() != snapshot.head:
        raise GrokWorkspaceGuardError("worker committed changes; HEAD no longer matches the contract base")
    if _git_database_fingerprint(repo) != snapshot.refs_and_objects:
        raise GrokWorkspaceGuardError("Git database changed under the worker")
    if _user_owned_metadata(repo) != snapshot.user_owned:
        raise GrokWorkspaceGuardError("user-owned state changed under the worker")
    if _ignored_metadata(repo) != snapshot.ignored:
        raise GrokWorkspaceGuardError("ignored path metadata changed under the worker")
