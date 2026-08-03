from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from trading_agent.research_agent_operations_models import (
    InvalidResearchAgentOperationsSourceError,
    OperationsAlertReason,
)


@dataclass(frozen=True, slots=True)
class _FileState:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _OpenedPrivateFile:
    path: Path
    descriptor: int
    before: _FileState


@contextmanager
def open_cycle_database_query_only(path: Path) -> Iterator[sqlite3.Connection]:
    sources = _open_database_sources(path)
    if len(sources) == 1:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
            _ = connection.execute("PRAGMA query_only=ON")
            yield connection
        finally:
            if connection is not None:
                connection.close()
            try:
                _confirm_sources(sources)
                _require_sidecars_absent(path)
            finally:
                _close_sources(sources)
        return
    try:
        with tempfile.TemporaryDirectory(prefix="research-agent-operations-") as temporary:
            snapshot = Path(temporary) / "snapshot.sqlite3"
            for source, destination in zip(sources, (snapshot, *_sidecar_paths(snapshot)), strict=True):
                _copy_private_file(source, destination)
            connection = sqlite3.connect(f"{snapshot.as_uri()}?mode=ro", uri=True)
            try:
                _ = connection.execute("PRAGMA query_only=ON")
                yield connection
            finally:
                connection.close()
                _confirm_sources(sources)
    finally:
        _close_sources(sources)


def cycle_database_storage_bytes(path: Path) -> int:
    sources = _open_database_sources(path)
    try:
        size = sum(source.before.size for source in sources)
        _confirm_sources(sources)
        if len(sources) == 1:
            _require_sidecars_absent(path)
        return size
    finally:
        _close_sources(sources)


def _open_database_sources(database: Path) -> tuple[_OpenedPrivateFile, ...]:
    main = _open_private_file(database)
    try:
        return (main, *_open_sidecars(database))
    except InvalidResearchAgentOperationsSourceError:
        os.close(main.descriptor)
        raise


def _open_sidecars(database: Path) -> tuple[_OpenedPrivateFile, ...]:
    paths = _sidecar_paths(database)
    present = tuple(os.path.lexists(path) for path in paths)
    if not any(present):
        return ()
    if not all(present):
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)
    opened: list[_OpenedPrivateFile] = []
    try:
        for path in paths:
            opened.append(_open_private_file(path))
    except InvalidResearchAgentOperationsSourceError:
        for source in opened:
            os.close(source.descriptor)
        raise
    return tuple(opened)


def _open_private_file(path: Path) -> _OpenedPrivateFile:
    try:
        expected = path.lstat()
        _require_private_file(expected)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        _raise_path_error(path)
    try:
        before = _private_state(descriptor)
        if (expected.st_dev, expected.st_ino) != (before.device, before.inode):
            raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)
        return _OpenedPrivateFile(path, descriptor, before)
    except InvalidResearchAgentOperationsSourceError:
        os.close(descriptor)
        raise


def _copy_private_file(source: _OpenedPrivateFile, destination: Path) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        offset = 0
        while offset < source.before.size:
            chunk = os.pread(source.descriptor, min(64 * 1024, source.before.size - offset), offset)
            if not chunk:
                raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)
            position = 0
            while position < len(chunk):
                written = os.write(descriptor, chunk[position:])
                if written < 1:
                    raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)
                position += written
            offset += len(chunk)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _confirm_private_file(source)


def _confirm_private_file(source: _OpenedPrivateFile) -> None:
    try:
        confirmation = os.open(source.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        _raise_path_error(source.path)
    try:
        current = _private_state(source.descriptor)
        confirmed = _private_state(confirmation)
        if current != source.before or (current.device, current.inode) != (confirmed.device, confirmed.inode):
            raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)
    finally:
        os.close(confirmation)


def _confirm_sources(sources: tuple[_OpenedPrivateFile, ...]) -> None:
    for source in sources:
        _confirm_private_file(source)


def _close_sources(sources: tuple[_OpenedPrivateFile, ...]) -> None:
    for source in sources:
        os.close(source.descriptor)


def _private_state(descriptor: int) -> _FileState:
    metadata = os.fstat(descriptor)
    _require_private_file(metadata)
    return _FileState(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _require_private_file(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_NONPRIVATE)
    if metadata.st_nlink > 1:
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_HARDLINK)
    if metadata.st_nlink != 1:
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)


def _raise_path_error(path: Path) -> NoReturn:
    try:
        metadata = path.lstat()
    except OSError:
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED) from None
    if stat.S_ISLNK(metadata.st_mode):
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_SYMLINK)
    _require_private_file(metadata)
    raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)


def _sidecar_paths(database: Path) -> tuple[Path, Path]:
    return Path(f"{database}-wal"), Path(f"{database}-shm")


def _require_sidecars_absent(database: Path) -> None:
    if any(os.path.lexists(candidate) for candidate in _sidecar_paths(database)):
        raise InvalidResearchAgentOperationsSourceError(OperationsAlertReason.CYCLE_STORE_MALFORMED)


__all__ = ("cycle_database_storage_bytes", "open_cycle_database_query_only")
