from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, override

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
    _require_current_schema,
)
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_open_directory_path,
    require_private_directory_query_only,
    require_same_file,
)

_FILE_MODE: Final = 0o600


class PrivateExperimentLedgerSnapshot(ExperimentLedgerReader):
    __slots__ = (
        "_connection",
        "_descriptor",
        "_metadata",
        "_parent_descriptor",
        "_sidecars",
    )

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)
        self._parent_descriptor = open_private_parent(self.path.parent, create=False)
        self._descriptor = -1
        self._connection: sqlite3.Connection | None = None
        self._metadata: tuple[int, int, int, int, int] | None = None
        self._sidecars: tuple[tuple[str, tuple[int, int, int, int, int] | None], ...] = ()
        try:
            require_private_directory_query_only(self._parent_descriptor)
            self._descriptor = _open_ledger(self._parent_descriptor, self.path.name)
            metadata = os.fstat(self._descriptor)
            self._metadata = _metadata_identity(metadata)
            self._connection = sqlite3.connect(":memory:")
            source = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            try:
                self._require_unchanged()
                source.backup(self._connection)
            finally:
                source.close()
            self._sidecars = _sidecar_states(self._parent_descriptor, self.path.name)
            _ = self._connection.execute("PRAGMA query_only = ON")
            _ = self._connection.execute("PRAGMA foreign_keys = ON")
            _require_current_schema(self._connection)
            self._require_unchanged()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            self.close()
            raise InvalidExperimentLedgerSourceError from None

    @contextmanager
    @override
    def _reader_connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection
        if connection is None:
            raise InvalidExperimentLedgerSourceError
        self._require_unchanged()
        try:
            yield connection
        finally:
            self._require_unchanged()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1
        if self._parent_descriptor >= 0:
            os.close(self._parent_descriptor)
            self._parent_descriptor = -1

    def _require_unchanged(self) -> None:
        if self._descriptor < 0 or self._metadata is None:
            raise InvalidExperimentLedgerSourceError
        if _metadata_identity(os.fstat(self._descriptor)) != self._metadata:
            raise InvalidExperimentLedgerSourceError
        if self._sidecars and _sidecar_states(self._parent_descriptor, self.path.name) != self._sidecars:
            raise InvalidExperimentLedgerSourceError
        confirmation = _open_ledger(self._parent_descriptor, self.path.name)
        try:
            require_same_file(self._descriptor, confirmation)
            if _metadata_identity(os.fstat(confirmation)) != self._metadata:
                raise InvalidExperimentLedgerSourceError
            require_open_directory_path(self.path.parent, self._parent_descriptor)
        finally:
            os.close(confirmation)


@contextmanager
def open_private_experiment_ledger_snapshot(path: Path) -> Iterator[PrivateExperimentLedgerSnapshot]:
    snapshot = PrivateExperimentLedgerSnapshot(path)
    try:
        yield snapshot
    finally:
        snapshot.close()


def _open_ledger(parent_descriptor: int, name: str) -> int:
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
            raise InvalidExperimentLedgerSourceError
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise


def _metadata_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _sidecar_states(
    parent_descriptor: int,
    name: str,
) -> tuple[tuple[str, tuple[int, int, int, int, int] | None], ...]:
    return tuple((suffix, _sidecar_state(parent_descriptor, f"{name}{suffix}")) for suffix in ("-wal", "-shm"))


def _sidecar_state(
    parent_descriptor: int,
    name: str,
) -> tuple[int, int, int, int, int] | None:
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
        or metadata.st_nlink != 1
    ):
        raise InvalidExperimentLedgerSourceError
    return _metadata_identity(metadata)
