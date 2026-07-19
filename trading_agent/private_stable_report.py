from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from typing import Final, override

from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
    require_same_file,
)

_FILE_MODE: Final = 0o600


class InvalidPrivateStableReportError(ValueError):
    @override
    def __str__(self) -> str:
        return "private stable report publication is invalid"


def write_private_stable_report(destination: Path, content: str) -> None:
    try:
        target = absolute_private_path(destination)
        if not target.name or not content:
            raise InvalidPrivateStableReportError
        parent_descriptor = open_private_parent(target.parent, create=True)
        try:
            require_private_directory(parent_descriptor)
            require_open_directory_path(target.parent, parent_descriptor)
            _replace_report(parent_descriptor, target.name, content)
            require_open_directory_path(target.parent, parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except (OSError, TypeError, UnicodeError, ValueError):
        raise InvalidPrivateStableReportError from None


def _replace_report(parent_descriptor: int, name: str, content: str) -> None:
    stage = f".{name}.{secrets.token_hex(12)}.writing"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        _FILE_MODE,
        dir_fd=parent_descriptor,
    )
    try:
        with os.fdopen(os.dup(descriptor), "w", encoding="utf-8") as handle:
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _require_replaceable(parent_descriptor, name)
        os.replace(
            stage,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
        final = _open_private_report(parent_descriptor, name)
        try:
            require_same_file(descriptor, final)
        finally:
            os.close(final)
    finally:
        os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(stage, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)


def _require_replaceable(parent_descriptor: int, name: str) -> None:
    try:
        descriptor = _open_private_report(parent_descriptor, name)
    except FileNotFoundError:
        return
    os.close(descriptor)


def _open_private_report(parent_descriptor: int, name: str) -> int:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=parent_descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
            or metadata.st_nlink != 1
        ):
            raise InvalidPrivateStableReportError
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise
