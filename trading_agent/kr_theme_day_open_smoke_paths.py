from __future__ import annotations

import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)


@dataclass(frozen=True, slots=True)
class PrivateFileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    links: int


def path_resolves(path: Path) -> bool:
    try:
        _ = path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return True


def path_aliases(target_path: Path, protected: tuple[Path, ...]) -> bool:
    try:
        target = target_path.expanduser().resolve(strict=False)
        return any(
            _path_key(target) == _path_key(path.expanduser().resolve(strict=False))
            or (target.exists() and path.exists() and target.samefile(path))
            for path in protected
        )
    except (OSError, RuntimeError):
        return True


def path_uses_protected_file(target_path: Path, protected: tuple[Path, ...]) -> bool:
    try:
        target = target_path.expanduser().resolve(strict=False)
        target_key = _path_key(target)
        return path_aliases(target, protected) or any(
            target_key.startswith(f"{_path_key(path.expanduser().resolve(strict=False)).rstrip(os.sep)}{os.sep}")
            for path in protected
        )
    except (OSError, RuntimeError):
        return True


def private_file_identity(path: Path) -> PrivateFileIdentity:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise OSError
    return _file_identity(metadata)


def unlink_matching_identity(path: Path, identity: PrivateFileIdentity) -> bool:
    target = absolute_private_path(path)
    parent_descriptor = open_private_parent(target.parent, create=False)
    try:
        require_private_directory_query_only(parent_descriptor)
        try:
            metadata = os.stat(
                target.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or _file_identity(metadata) != identity:
            return False
        os.unlink(target.name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        require_open_directory_path(target.parent, parent_descriptor)
        return True
    finally:
        os.close(parent_descriptor)


def _path_key(path: Path) -> str:
    return unicodedata.normalize("NFC", str(path)).casefold()


def _file_identity(metadata: os.stat_result) -> PrivateFileIdentity:
    return PrivateFileIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_nlink,
    )
