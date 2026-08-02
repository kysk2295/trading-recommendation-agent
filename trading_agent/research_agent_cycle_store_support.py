from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, TextIO, final

from trading_agent.research_agent_cycle_schema import (
    RESEARCH_AGENT_CYCLE_SCHEMA,
    RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,
)

_DATABASE_SUFFIXES: Final = ("", "-shm", "-wal")


class InvalidResearchAgentCycleStoreError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


class ResearchAgentCycleWriterLeaseUnavailableError(RuntimeError):
    def __str__(self) -> str:
        return "research_agent_cycle_writer_lease_unavailable"


class InactiveResearchAgentCycleStoreError(RuntimeError):
    def __str__(self) -> str:
        return "research_agent_cycle_store_inactive"


@final
class ResearchAgentCycleDatabaseLease:
    __slots__ = ("_active", "_lock_handle", "path")

    path: Path
    _active: bool
    _lock_handle: TextIO

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()
        self._active = False
        _require_database_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = Path(f"{self.path}.writer.lock")
        _require_lock_path(lock_path)
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise InvalidResearchAgentCycleStoreError(reason="no_follow_unavailable")
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
            os.fchmod(descriptor, 0o600)
            lock_handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        except OSError:
            raise InvalidResearchAgentCycleStoreError(reason="writer_lock_invalid") from None
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            raise ResearchAgentCycleWriterLeaseUnavailableError from None
        self._lock_handle = lock_handle
        try:
            self._initialize()
        except (InvalidResearchAgentCycleStoreError, OSError, sqlite3.Error):
            self._release_lock()
            raise
        self._active = True

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        self._require_active()
        _require_private_file(self.path, "database_path_invalid")
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            _ = connection.execute("PRAGMA query_only=ON")
            _ = connection.execute("PRAGMA foreign_keys=ON")
            _require_schema(connection)
            yield connection
        finally:
            connection.close()

    @contextmanager
    def writer(self) -> Iterator[sqlite3.Connection]:
        self._require_active()
        _require_private_file(self.path, "database_path_invalid")
        connection = sqlite3.connect(self.path, timeout=0.0)
        try:
            _ = connection.execute("PRAGMA foreign_keys=ON")
            _ = connection.execute("PRAGMA synchronous=FULL")
            _require_schema(connection)
            yield connection
        finally:
            connection.close()

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        self._release_lock()

    def _initialize(self) -> None:
        _require_database_path(self.path)
        existed = self.path.exists()
        connection = sqlite3.connect(self.path, timeout=0.0)
        try:
            if not existed:
                os.chmod(self.path, 0o600)
            _require_private_file(self.path, "database_path_invalid")
            _ = connection.execute("PRAGMA foreign_keys=ON")
            _ = connection.execute("PRAGMA journal_mode=WAL")
            _ = connection.execute("PRAGMA synchronous=FULL")
            version = connection.execute("PRAGMA user_version").fetchone()
            if version == (0,):
                for statement in RESEARCH_AGENT_CYCLE_SCHEMA:
                    _ = connection.execute(statement)
                _ = connection.execute(f"PRAGMA user_version={RESEARCH_AGENT_CYCLE_SCHEMA_VERSION}")
                connection.commit()
            else:
                _require_schema(connection)
        finally:
            connection.close()
        _privatize_database_files(self.path)

    def _require_active(self) -> None:
        if not self._active:
            raise InactiveResearchAgentCycleStoreError

    def _release_lock(self) -> None:
        fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        self._lock_handle.close()


def _require_schema(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version != (RESEARCH_AGENT_CYCLE_SCHEMA_VERSION,):
        raise InvalidResearchAgentCycleStoreError(reason="schema_version_invalid")


def _require_database_path(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _require_private_file(path, "database_path_invalid")


def _require_lock_path(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _require_private_file(path, "writer_lock_invalid")


def _require_private_file(path: Path, reason: str) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidResearchAgentCycleStoreError(reason=reason)


def _privatize_database_files(path: Path) -> None:
    for suffix in _DATABASE_SUFFIXES:
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            os.chmod(candidate, 0o600)


__all__ = (
    "InactiveResearchAgentCycleStoreError",
    "InvalidResearchAgentCycleStoreError",
    "ResearchAgentCycleDatabaseLease",
    "ResearchAgentCycleWriterLeaseUnavailableError",
)
