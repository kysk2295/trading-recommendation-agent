from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from typing import override


class InvalidPrivateImmutableFileError(ValueError):
    @override
    def __str__(self) -> str:
        return "private immutable file publication is invalid"


def publish_private_immutable_text(path: Path, payload: str) -> bool:
    try:
        target = path.expanduser().absolute()
        if not target.name or not payload:
            raise InvalidPrivateImmutableFileError
        parent = _prepare_parent(target.parent)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        parent_descriptor = os.open(parent, flags)
        try:
            return _publish(parent_descriptor, target.name, payload)
        finally:
            os.close(parent_descriptor)
    except (OSError, TypeError, ValueError):
        raise InvalidPrivateImmutableFileError from None


def _prepare_parent(path: Path) -> Path:
    _require_no_symlink_components(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _require_no_symlink_components(path)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise InvalidPrivateImmutableFileError
    os.chmod(path, 0o700, follow_symlinks=False)
    return path


def _require_no_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise InvalidPrivateImmutableFileError


def _publish(parent_descriptor: int, name: str, payload: str) -> bool:
    stage = f".{name}.{secrets.token_hex(12)}.staging"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_descriptor,
    )
    try:
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                _ = handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            os.link(
                stage,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        os.fsync(parent_descriptor)
        return True
    finally:
        with suppress(FileNotFoundError):
            os.unlink(stage, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
