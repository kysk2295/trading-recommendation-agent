from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_agent_family import AgentFamilyId

InteractionKind = Literal["conversation", "directed"]
InteractionClaimState = Literal["queued", "running", "completed", "failed", "uncertain"]
TerminalClaimState = Literal["completed", "failed", "uncertain"]

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS interactive_claims ("
    "interaction_id TEXT PRIMARY KEY,"
    "agent_family_id TEXT NOT NULL,"
    "kind TEXT NOT NULL CHECK(kind IN ('conversation','directed')),"
    "state TEXT NOT NULL CHECK(state IN ('queued','running','completed','failed','uncertain')),"
    "process_starts INTEGER NOT NULL DEFAULT 0 CHECK(process_starts BETWEEN 0 AND 1)"
    ");"
)


class InvalidInteractiveClaimStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InteractiveClaim:
    interaction_id: str
    agent_family_id: AgentFamilyId
    kind: InteractionKind
    state: InteractionClaimState
    process_starts: int


class InteractiveClaimStore:
    def __init__(self, database: Path) -> None:
        self._database = database
        try:
            database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            database.parent.chmod(0o700)
            if database.exists() or database.is_symlink():
                _require_private_file(database)
            else:
                descriptor = os.open(database, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
            with closing(self._connect()) as connection, connection:
                connection.executescript(_SCHEMA)
        except (OSError, sqlite3.Error) as error:
            raise InvalidInteractiveClaimStoreError("interactive_claim_store_invalid") from error

    def claim(
        self,
        interaction_id: str,
        family_id: AgentFamilyId,
        kind: InteractionKind,
    ) -> bool:
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO interactive_claims "
                    "(interaction_id,agent_family_id,kind,state) VALUES (?,?,?,'queued')",
                    (interaction_id, family_id, kind),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise InvalidInteractiveClaimStoreError("interactive_claim_failed") from error

    def mark_running(self, interaction_id: str, *, process_started: bool = True) -> bool:
        return self._transition(interaction_id, "queued", "running", process_start=process_started)

    def mark_terminal(self, interaction_id: str, state: TerminalClaimState) -> bool:
        return self._transition(interaction_id, "running", state, process_start=False)

    def recover_incomplete(self) -> int:
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute("UPDATE interactive_claims SET state='uncertain' WHERE state='running'")
                return cursor.rowcount
        except sqlite3.Error as error:
            raise InvalidInteractiveClaimStoreError("interactive_claim_recovery_failed") from error

    def get(self, interaction_id: str) -> InteractiveClaim | None:
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    "SELECT interaction_id,agent_family_id,kind,state,process_starts "
                    "FROM interactive_claims WHERE interaction_id=?",
                    (interaction_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise InvalidInteractiveClaimStoreError("interactive_claim_read_failed") from error
        if row is None:
            return None
        return InteractiveClaim(*row)

    def _transition(
        self,
        interaction_id: str,
        expected: InteractionClaimState,
        state: InteractionClaimState,
        *,
        process_start: bool,
    ) -> bool:
        increment = ", process_starts=process_starts+1" if process_start else ""
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    f"UPDATE interactive_claims SET state=?{increment} "
                    "WHERE interaction_id=? AND state=? AND process_starts<=1",
                    (state, interaction_id, expected),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as error:
            raise InvalidInteractiveClaimStoreError("interactive_claim_transition_failed") from error

    def _connect(self) -> sqlite3.Connection:
        _require_private_file(self._database)
        connection = sqlite3.connect(self._database, timeout=10, isolation_level="IMMEDIATE")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidInteractiveClaimStoreError("interactive_claim_file_identity_invalid")


__all__ = (
    "InteractiveClaim",
    "InteractiveClaimStore",
    "InvalidInteractiveClaimStoreError",
)
