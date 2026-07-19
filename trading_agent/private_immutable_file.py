from __future__ import annotations

import fcntl
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from threading import Lock
from typing import Final, override

from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory,
    require_same_file,
)

_FILE_MODE: Final = 0o600
_MAX_TEXT_BYTES: Final = 64 * 1024 * 1024
_STAGING_SUFFIX: Final = ".staging"
_LOCK_SUFFIX: Final = ".publication.lock"
_PROCESS_PUBLICATION_LOCK: Final = Lock()


class InvalidPrivateImmutableFileError(ValueError):
    @override
    def __str__(self) -> str:
        return "private immutable file publication is invalid"


def publish_private_immutable_text(path: Path, payload: str) -> bool:
    try:
        target = absolute_private_path(path)
        if not target.name or not payload:
            raise InvalidPrivateImmutableFileError
        parent_descriptor = open_private_parent(target.parent, create=True)
        try:
            require_private_directory(parent_descriptor)
            with _PROCESS_PUBLICATION_LOCK:
                created = _publish(parent_descriptor, target.name, payload)
                require_open_directory_path(target.parent, parent_descriptor)
                return created
        finally:
            os.close(parent_descriptor)
    except (OSError, TypeError, ValueError):
        raise InvalidPrivateImmutableFileError from None


def read_private_text(path: Path) -> str:
    try:
        target = absolute_private_path(path)
        if not target.name:
            raise InvalidPrivateImmutableFileError
        parent_descriptor = open_private_parent(target.parent, create=False)
        try:
            descriptor = _open_private_file(parent_descriptor, target.name, (1, 2))
            try:
                with _PROCESS_PUBLICATION_LOCK:
                    lock_descriptor = _lock_publication(parent_descriptor, target.name)
                    try:
                        if os.fstat(descriptor).st_nlink == 2:
                            _repair_staging_alias(parent_descriptor, descriptor, target.name)
                        payload = _read_text(descriptor)
                        _require_final_file(parent_descriptor, target.name, descriptor)
                        require_open_directory_path(target.parent, parent_descriptor)
                        return payload
                    finally:
                        os.close(lock_descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent_descriptor)
    except (OSError, TypeError, UnicodeError, ValueError):
        raise InvalidPrivateImmutableFileError from None


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
            raise InvalidPrivateImmutableFileError
        return descriptor
    except (OSError, TypeError, ValueError):
        os.close(descriptor)
        raise


def _read_text(descriptor: int) -> str:
    before = os.fstat(descriptor)
    if before.st_size < 0 or before.st_size > _MAX_TEXT_BYTES:
        raise InvalidPrivateImmutableFileError
    content = bytearray()
    while chunk := os.read(descriptor, min(64 * 1024, _MAX_TEXT_BYTES + 1 - len(content))):
        content.extend(chunk)
        if len(content) > _MAX_TEXT_BYTES:
            raise InvalidPrivateImmutableFileError
    after = os.fstat(descriptor)
    if (
        len(content) != before.st_size
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise InvalidPrivateImmutableFileError
    return bytes(content).decode("utf-8")


def _require_existing(parent_descriptor: int, name: str, payload: str) -> bool | None:
    try:
        descriptor = _open_private_file(parent_descriptor, name, (1, 2))
    except FileNotFoundError:
        return None
    try:
        if os.fstat(descriptor).st_nlink == 2:
            _repair_staging_alias(parent_descriptor, descriptor, name)
        if _read_text(descriptor) != payload:
            raise InvalidPrivateImmutableFileError
        _require_final_file(parent_descriptor, name, descriptor)
        return False
    finally:
        os.close(descriptor)


def _repair_staging_alias(parent_descriptor: int, descriptor: int, name: str) -> None:
    identity = os.fstat(descriptor)
    prefix = f".{name}."
    matches: list[str] = []
    for candidate in os.listdir(parent_descriptor):
        if candidate.startswith(prefix) and candidate.endswith(_STAGING_SUFFIX):
            metadata = os.stat(candidate, dir_fd=parent_descriptor, follow_symlinks=False)
            if (metadata.st_dev, metadata.st_ino) == (identity.st_dev, identity.st_ino):
                matches.append(candidate)
    if len(matches) != 1:
        raise InvalidPrivateImmutableFileError
    os.unlink(matches[0], dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)
    if os.fstat(descriptor).st_nlink != 1:
        raise InvalidPrivateImmutableFileError


def _publish(parent_descriptor: int, name: str, payload: str) -> bool:
    lock_descriptor = _lock_publication(parent_descriptor, name)
    try:
        return _publish_locked(parent_descriptor, name, payload)
    finally:
        os.close(lock_descriptor)


def _lock_publication(parent_descriptor: int, name: str) -> int:
    descriptor = _open_publication_lock(parent_descriptor, name)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise InvalidPrivateImmutableFileError
        os.fchmod(descriptor, _FILE_MODE)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _open_publication_lock(parent_descriptor: int, name: str) -> int:
    lock_name = f".{name}{_LOCK_SUFFIX}"
    create_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        return os.open(lock_name, create_flags, _FILE_MODE, dir_fd=parent_descriptor)
    except FileExistsError:
        return os.open(lock_name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent_descriptor)


def _publish_locked(parent_descriptor: int, name: str, payload: str) -> bool:
    existing = _require_existing(parent_descriptor, name, payload)
    if existing is not None:
        return existing
    _remove_orphan_staging(parent_descriptor, name)
    stage = f".{name}.{secrets.token_hex(12)}{_STAGING_SUFFIX}"
    descriptor = os.open(
        stage,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        _FILE_MODE,
        dir_fd=parent_descriptor,
    )
    try:
        with os.fdopen(os.dup(descriptor), "w", encoding="utf-8") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(
            stage,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        published = _open_private_file(parent_descriptor, name, (1, 2))
        try:
            _require_same_file(descriptor, published)
            os.fsync(parent_descriptor)
            os.unlink(stage, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            if os.fstat(descriptor).st_nlink != 1:
                raise InvalidPrivateImmutableFileError
            _require_final_file(parent_descriptor, name, descriptor)
            return True
        finally:
            os.close(published)
    except FileExistsError:
        existing = _require_existing(parent_descriptor, name, payload)
        if existing is None:
            raise InvalidPrivateImmutableFileError from None
        return existing
    finally:
        os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(stage, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)


def _require_same_file(left_descriptor: int, right_descriptor: int) -> None:
    require_same_file(left_descriptor, right_descriptor)


def _require_final_file(parent_descriptor: int, name: str, expected_descriptor: int) -> None:
    final = _open_private_file(parent_descriptor, name, (1,))
    try:
        _require_same_file(expected_descriptor, final)
    finally:
        os.close(final)


def _remove_orphan_staging(parent_descriptor: int, name: str) -> None:
    prefix = f".{name}."
    removed = False
    for candidate in os.listdir(parent_descriptor):
        if candidate.startswith(prefix) and candidate.endswith(_STAGING_SUFFIX):
            metadata = os.stat(candidate, dir_fd=parent_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
                or metadata.st_nlink != 1
            ):
                raise InvalidPrivateImmutableFileError
            os.unlink(candidate, dir_fd=parent_descriptor)
            removed = True
    if removed:
        os.fsync(parent_descriptor)
