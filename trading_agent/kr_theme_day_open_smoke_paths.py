from __future__ import annotations

import os
import stat
import unicodedata
from pathlib import Path

from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
)


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


def require_private_source_path(path: Path) -> None:
    target = absolute_private_path(path)
    parent_descriptor = open_private_parent(target.parent, create=False)
    try:
        require_private_directory_query_only(parent_descriptor)
        try:
            descriptor = os.open(
                target.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            require_open_directory_path(target.parent, parent_descriptor)
            return
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise OSError
            require_open_directory_path(target.parent, parent_descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)


def _path_key(path: Path) -> str:
    return unicodedata.normalize("NFC", str(path)).casefold()
