from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import final

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


@final
class ExecutionStore(ExecutionStoreReader):
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    @contextmanager
    def writer(self) -> Iterator[ExecutionWriter]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WriterLeaseUnavailableError(lock_path) from error
            connection = sqlite3.connect(self.path, timeout=0.0)
            prepare_execution_writer_connection(connection, self.path)
            writer = ExecutionWriter(connection)
            try:
                yield writer
            finally:
                writer._close()
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
