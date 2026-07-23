from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)
from trading_agent.treasury_yield_models import (
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
    TreasuryYieldRun,
)
from trading_agent.treasury_yield_schema import (
    CREATE_TREASURY_YIELD_SCHEMA,
    TREASURY_YIELD_SCHEMA_VERSION,
)

type ReceiptRow = tuple[str, bytes, str, str, int, str, str, bytes]
type RunRow = tuple[str, str, bytes]


class TreasuryYieldStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "Treasury yield store is invalid"


@final
class TreasuryYieldStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def preflight_write(self) -> None:
        with closing(self._connection(write=True)):
            pass

    def append_receipt(
        self,
        request: TreasuryYieldRequest,
        response: TreasuryYieldRawResponse,
    ) -> bool:
        if response.request_id != request.request_id:
            raise TreasuryYieldStoreError
        row = (
            request.request_id,
            canonical_experiment_ledger_json(request).encode(),
            response.receipt_id,
            response.received_at.isoformat(),
            response.status_code,
            response.content_type,
            hashlib.sha256(response.raw_payload).hexdigest(),
            response.raw_payload,
        )
        try:
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if self._run_row(connection, request.request_id) is not None:
                    raise TreasuryYieldStoreError
                existing = self._receipt_row(connection, request.request_id)
                if existing is not None:
                    if tuple(existing) != row:
                        raise TreasuryYieldStoreError
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT INTO treasury_yield_receipts VALUES (?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise TreasuryYieldStoreError from None

    def append_run(self, run: TreasuryYieldRun) -> bool:
        payload = canonical_experiment_ledger_json(run).encode()
        row = (
            run.request.request_id,
            run.run_id,
            hashlib.sha256(payload).hexdigest(),
            payload,
        )
        try:
            with closing(self._connection(write=True)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                receipt = self._receipt_row(
                    connection,
                    run.request.request_id,
                )
                receipt_id = None if receipt is None else receipt[2]
                if receipt_id != run.receipt_id:
                    raise TreasuryYieldStoreError
                existing = self._run_row(connection, run.request.request_id)
                if existing is not None:
                    if tuple(existing) != row[1:]:
                        raise TreasuryYieldStoreError
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT INTO treasury_yield_runs VALUES (?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise TreasuryYieldStoreError from None

    def receipt(
        self,
        request_id: str,
    ) -> TreasuryYieldRawResponse | None:
        if not self.path.exists():
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                row = self._receipt_row(connection, request_id)
            if row is None:
                return None
            request = TreasuryYieldRequest.model_validate_json(row[1])
            response = TreasuryYieldRawResponse(
                request_id=row[0],
                received_at=dt.datetime.fromisoformat(row[3]),
                status_code=row[4],
                content_type=row[5],
                raw_payload=row[7],
            )
            if (
                request.request_id != request_id
                or row[2] != response.receipt_id
                or row[6] != hashlib.sha256(response.raw_payload).hexdigest()
            ):
                raise TreasuryYieldStoreError
            return response
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise TreasuryYieldStoreError from None

    def run(self, request_id: str) -> TreasuryYieldRun | None:
        if not self.path.exists():
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                row = self._run_row(connection, request_id)
            if row is None:
                return None
            run = TreasuryYieldRun.model_validate_json(row[2])
            payload = canonical_experiment_ledger_json(run).encode()
            if (
                run.request.request_id != request_id
                or row[0] != run.run_id
                or row[1] != hashlib.sha256(payload).hexdigest()
                or row[2] != payload
            ):
                raise TreasuryYieldStoreError
            return run
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise TreasuryYieldStoreError from None

    def counts(self) -> tuple[int, int]:
        try:
            with closing(self._connection(write=False)) as connection:
                receipts = connection.execute(
                    "SELECT COUNT(*) FROM treasury_yield_receipts",
                ).fetchone()
                runs = connection.execute(
                    "SELECT COUNT(*) FROM treasury_yield_runs",
                ).fetchone()
            if receipts is None or runs is None:
                raise TreasuryYieldStoreError
            return int(receipts[0]), int(runs[0])
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise TreasuryYieldStoreError from None

    @staticmethod
    def _receipt_row(
        connection: sqlite3.Connection,
        request_id: str,
    ) -> ReceiptRow | None:
        return connection.execute(
            "SELECT request_id,request_payload,receipt_id,received_at,"
            "status_code,content_type,payload_sha256,raw_payload "
            "FROM treasury_yield_receipts WHERE request_id=?",
            (request_id,),
        ).fetchone()

    @staticmethod
    def _run_row(
        connection: sqlite3.Connection,
        request_id: str,
    ) -> RunRow | None:
        return connection.execute(
            "SELECT run_id,run_sha256,run_payload FROM treasury_yield_runs WHERE request_id=?",
            (request_id,),
        ).fetchone()

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        try:
            if self.path.is_symlink():
                raise TreasuryYieldStoreError
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self.path.parent, 0o700)
                connection = sqlite3.connect(self.path)
                if self.path.stat().st_mode & 0o077:
                    os.chmod(self.path, 0o600)
                if connection.execute("PRAGMA user_version").fetchone() == (0,):
                    connection.executescript(CREATE_TREASURY_YIELD_SCHEMA)
                    connection.execute(
                        f"PRAGMA user_version={TREASURY_YIELD_SCHEMA_VERSION}",
                    )
                    connection.commit()
            else:
                parent = open_private_parent(self.path.parent, create=False)
                try:
                    require_private_directory_query_only(parent)
                    connection = sqlite3.connect(
                        f"file:{self.path}?mode=ro",
                        uri=True,
                    )
                    connection.execute("PRAGMA query_only=ON")
                finally:
                    os.close(parent)
            if connection.execute("PRAGMA user_version").fetchone() != (TREASURY_YIELD_SCHEMA_VERSION,):
                connection.close()
                raise TreasuryYieldStoreError
            return connection
        except (OSError, sqlite3.Error):
            raise TreasuryYieldStoreError from None


__all__ = ("TreasuryYieldStore", "TreasuryYieldStoreError")
