from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from trading_agent.experiment_ledger_store import ExperimentLedgerReader


def experiment_ledger_v9_identity(path: Path) -> str:
    reader = ExperimentLedgerReader(path)
    if not reader.is_initialized():
        raise ValueError("experiment ledger v9 authority is invalid")
    connection = sqlite3.connect(f"file:{reader.path}?mode=ro", uri=True)
    try:
        _ = connection.execute("PRAGMA query_only = ON")
        version = connection.execute("PRAGMA user_version").fetchone()
        if version != (9,):
            raise ValueError("experiment ledger schema is not v9")
        dump = "\n".join(connection.iterdump()) + "\n"
    finally:
        connection.close()
    return hashlib.sha256(dump.encode()).hexdigest()


__all__ = ("experiment_ledger_v9_identity",)
