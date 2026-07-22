from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, final

from trading_agent.execution_database import prepare_execution_writer_connection
from trading_agent.execution_errors import (
    AccountBindingConflictError as AccountBindingConflictError,
)
from trading_agent.execution_store_errors import (
    BrokerEventConflictError as BrokerEventConflictError,
)
from trading_agent.execution_store_errors import (
    InactiveExecutionWriterError as InactiveExecutionWriterError,
)
from trading_agent.execution_store_errors import (
    IntentConflictError as IntentConflictError,
)
from trading_agent.execution_store_errors import (
    WriterLeaseUnavailableError as WriterLeaseUnavailableError,
)
from trading_agent.execution_store_reader import ExecutionStoreReader
from trading_agent.execution_writer import (
    ExecutionLedgerGeneration as ExecutionLedgerGeneration,
)
from trading_agent.execution_writer import (
    ExecutionWriter as ExecutionWriter,
)

_PRIVATE_FILE_MODE: Final = 0o600


def _secure_private_file(path: Path, *, create: bool) -> None:
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    try:
        descriptor = os.open(path, flags, _PRIVATE_FILE_MODE)
    except FileNotFoundError:
        return
    try:
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)


def _secure_execution_sqlite_files(path: Path, *, create_database: bool) -> None:
    _secure_private_file(path, create=create_database)
    _secure_private_file(Path(f"{path}-wal"), create=False)
    _secure_private_file(Path(f"{path}-shm"), create=False)


@final
class ExecutionStore(ExecutionStoreReader):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    @contextmanager
    def writer(self) -> Iterator[ExecutionWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, _PRIVATE_FILE_MODE)
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WriterLeaseUnavailableError(lock_path) from error
            _secure_execution_sqlite_files(self.path, create_database=True)
            connection = sqlite3.connect(self.path, timeout=0.0)
            prepare_execution_writer_connection(connection, self.path)
            _secure_execution_sqlite_files(self.path, create_database=False)
            writer = ExecutionWriter(connection)
            try:
                yield writer
            finally:
                writer._close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
