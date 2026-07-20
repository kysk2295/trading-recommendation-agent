from __future__ import annotations

import os
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


class InvalidPrivateImmutableAliasError(ValueError):
    @override
    def __str__(self) -> str:
        return "private immutable alias publication is invalid"


def publish_private_immutable_alias(source: Path, destination: Path) -> bool:
    try:
        source_target = absolute_private_path(source)
        destination_target = absolute_private_path(destination)
        if (
            not source_target.name
            or not destination_target.name
            or source_target.parent != destination_target.parent
            or source_target == destination_target
        ):
            raise InvalidPrivateImmutableAliasError
        parent_descriptor = open_private_parent(destination_target.parent, create=False)
        committed = False
        try:
            require_private_directory(parent_descriptor)
            source_descriptor = _open_private_file(parent_descriptor, source_target.name, (1,))
            linked = False
            published_state: tuple[int, int, int, int, int, int] | None = None
            try:
                os.link(
                    source_target.name,
                    destination_target.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                linked = True
                published_state = _file_state(source_descriptor)
                destination_descriptor = _open_private_file(parent_descriptor, destination_target.name, (2,))
                try:
                    require_same_file(source_descriptor, destination_descriptor)
                    os.fsync(parent_descriptor)
                    if os.fstat(source_descriptor).st_nlink != 2:
                        raise InvalidPrivateImmutableAliasError
                    confirmation = _open_private_file(parent_descriptor, destination_target.name, (2,))
                    try:
                        require_same_file(source_descriptor, confirmation)
                    finally:
                        os.close(confirmation)
                    require_open_directory_path(destination_target.parent, parent_descriptor)
                finally:
                    os.close(destination_descriptor)
                os.unlink(source_target.name, dir_fd=parent_descriptor)
                committed = True
                return True
            except (OSError, ValueError):
                if linked and published_state is not None:
                    _unlink_published_alias(
                        parent_descriptor,
                        source_descriptor,
                        destination_target.name,
                        published_state,
                    )
                raise
            finally:
                if committed:
                    with suppress(OSError):
                        os.close(source_descriptor)
                else:
                    os.close(source_descriptor)
        finally:
            if committed:
                with suppress(OSError):
                    os.close(parent_descriptor)
            else:
                os.close(parent_descriptor)
    except (OSError, TypeError, ValueError):
        raise InvalidPrivateImmutableAliasError from None


def _open_private_file(parent_descriptor: int, name: str, links: tuple[int, ...]) -> int:
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
            or metadata.st_nlink not in links
        ):
            raise InvalidPrivateImmutableAliasError
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _unlink_published_alias(
    parent_descriptor: int,
    source_descriptor: int,
    name: str,
    expected_state: tuple[int, int, int, int, int, int],
) -> None:
    destination_descriptor = _open_private_file(parent_descriptor, name, (1, 2))
    try:
        require_same_file(source_descriptor, destination_descriptor)
        if _file_state(destination_descriptor) != expected_state:
            return
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    finally:
        os.close(destination_descriptor)


def _file_state(descriptor: int) -> tuple[int, int, int, int, int, int]:
    metadata = os.fstat(descriptor)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_nlink,
    )
