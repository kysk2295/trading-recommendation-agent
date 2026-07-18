from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import final

from trading_agent.us_subscription_policy_state import (
    SubscriptionPolicyRuntimeState,
    SubscriptionPolicyStateError,
    state_bytes,
    state_from_bytes,
)

_SCHEMA = """
CREATE TABLE subscription_policy_state (
  generation INTEGER PRIMARY KEY AUTOINCREMENT,
  state_id TEXT NOT NULL UNIQUE,
  evaluated_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json BLOB NOT NULL
);
CREATE TRIGGER subscription_policy_state_no_update BEFORE UPDATE ON subscription_policy_state
BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER subscription_policy_state_no_delete BEFORE DELETE ON subscription_policy_state
BEGIN SELECT RAISE(ABORT, 'append only'); END;
"""


@final
class SubscriptionPolicyStateStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().absolute()

    def append(self, state: SubscriptionPolicyRuntimeState) -> bool:
        try:
            payload = state_bytes(state)
            row = (
                state.state_id,
                state.evaluated_at.isoformat(),
                hashlib.sha256(payload).hexdigest(),
                payload,
            )
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT state_id,evaluated_at,payload_sha256,payload_json "
                    "FROM subscription_policy_state WHERE state_id=?",
                    (state.state_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise SubscriptionPolicyStateError
                    return False
                connection.execute(
                    "INSERT INTO subscription_policy_state "
                    "(state_id,evaluated_at,payload_sha256,payload_json) VALUES (?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise SubscriptionPolicyStateError from None

    def latest(self) -> SubscriptionPolicyRuntimeState | None:
        if not self.path.is_file():
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                row: tuple[str, str, str, bytes] | None = connection.execute(
                    "SELECT state_id,evaluated_at,payload_sha256,payload_json "
                    "FROM subscription_policy_state ORDER BY generation DESC LIMIT 1"
                ).fetchone()
            if row is None or hashlib.sha256(row[3]).hexdigest() != row[2]:
                raise SubscriptionPolicyStateError
            state = state_from_bytes(row[3])
            if state.state_id != row[0] or state.evaluated_at.isoformat() != row[1]:
                raise SubscriptionPolicyStateError
            return state
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise SubscriptionPolicyStateError from None

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise SubscriptionPolicyStateError
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            if existed:
                _require_private_file(self.path)
            connection = sqlite3.connect(self.path)
            if not existed:
                os.chmod(self.path, 0o600)
            _require_private_file(self.path)
            if connection.execute("PRAGMA user_version").fetchone() == (0,):
                connection.executescript(_SCHEMA)
                connection.execute("PRAGMA user_version=1")
                connection.commit()
        else:
            _require_private_file(self.path)
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA user_version").fetchone() != (1,):
            connection.close()
            raise SubscriptionPolicyStateError
        return connection


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SubscriptionPolicyStateError


__all__ = ("SubscriptionPolicyStateStore",)
