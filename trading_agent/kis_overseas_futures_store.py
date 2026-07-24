from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kis_overseas_futures_models import (
    KisFuturesQuoteRawResponse,
    KisFuturesQuoteRequest,
    KisFuturesQuoteRun,
)
from trading_agent.kis_overseas_futures_schema import (
    CREATE_KIS_OVERSEAS_FUTURES_SCHEMA,
    KIS_OVERSEAS_FUTURES_SCHEMA_VERSION,
)
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)

type ReceiptRow = tuple[str, bytes, str, str, str, int, str, str, bytes]
type RunRow = tuple[str, str, bytes]


class KisOverseasFuturesStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS overseas futures quote store is invalid"


@final
class KisOverseasFuturesStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def preflight_write(self) -> None:
        with closing(self._connection(write=True)):
            pass

    def append_receipt(
        self,
        request: KisFuturesQuoteRequest,
        response: KisFuturesQuoteRawResponse,
    ) -> bool:
        if (
            response.request_id != request.request_id
            or response.symbol not in request.symbols
        ):
            raise KisOverseasFuturesStoreError
        row = (
            request.request_id,
            canonical_experiment_ledger_json(request).encode(),
            response.symbol,
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
                    raise KisOverseasFuturesStoreError
                existing = self._receipt_row(
                    connection,
                    request.request_id,
                    response.symbol,
                )
                if existing is not None:
                    if tuple(existing) != row:
                        raise KisOverseasFuturesStoreError
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT INTO kis_futures_quote_receipts "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise KisOverseasFuturesStoreError from None

    def append_run(self, run: KisFuturesQuoteRun) -> bool:
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
                receipt_ids = tuple(
                    item[0]
                    for item in connection.execute(
                        "SELECT receipt_id FROM kis_futures_quote_receipts "
                        "WHERE request_id=? ORDER BY symbol",
                        (run.request.request_id,),
                    ).fetchall()
                )
                if receipt_ids != run.receipt_ids:
                    raise KisOverseasFuturesStoreError
                existing = self._run_row(
                    connection,
                    run.request.request_id,
                )
                if existing is not None:
                    if tuple(existing) != row[1:]:
                        raise KisOverseasFuturesStoreError
                    connection.rollback()
                    return False
                connection.execute(
                    "INSERT INTO kis_futures_quote_runs VALUES (?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise KisOverseasFuturesStoreError from None

    def receipt(
        self,
        request_id: str,
        symbol: str,
    ) -> KisFuturesQuoteRawResponse | None:
        if not self.path.exists():
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                row = self._receipt_row(connection, request_id, symbol)
            if row is None:
                return None
            request = KisFuturesQuoteRequest.model_validate_json(row[1])
            response = KisFuturesQuoteRawResponse(
                request_id=row[0],
                symbol=row[2],
                received_at=dt.datetime.fromisoformat(row[4]),
                status_code=row[5],
                content_type=row[6],
                raw_payload=row[8],
            )
            if (
                request.request_id != request_id
                or request_id != response.request_id
                or symbol != response.symbol
                or row[3] != response.receipt_id
                or row[7] != hashlib.sha256(response.raw_payload).hexdigest()
            ):
                raise KisOverseasFuturesStoreError
            return response
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise KisOverseasFuturesStoreError from None

    def run(self, request_id: str) -> KisFuturesQuoteRun | None:
        if not self.path.exists():
            return None
        try:
            with closing(self._connection(write=False)) as connection:
                row = self._run_row(connection, request_id)
            if row is None:
                return None
            run = KisFuturesQuoteRun.model_validate_json(row[2])
            payload = canonical_experiment_ledger_json(run).encode()
            if (
                run.request.request_id != request_id
                or row[0] != run.run_id
                or row[1] != hashlib.sha256(payload).hexdigest()
                or row[2] != payload
            ):
                raise KisOverseasFuturesStoreError
            return run
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise KisOverseasFuturesStoreError from None

    def counts(self) -> tuple[int, int]:
        try:
            with closing(self._connection(write=False)) as connection:
                receipts = connection.execute(
                    "SELECT COUNT(*) FROM kis_futures_quote_receipts"
                ).fetchone()
                runs = connection.execute(
                    "SELECT COUNT(*) FROM kis_futures_quote_runs"
                ).fetchone()
            if receipts is None or runs is None:
                raise KisOverseasFuturesStoreError
            return int(receipts[0]), int(runs[0])
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise KisOverseasFuturesStoreError from None

    @staticmethod
    def _receipt_row(
        connection: sqlite3.Connection,
        request_id: str,
        symbol: str,
    ) -> ReceiptRow | None:
        return connection.execute(
            "SELECT request_id,request_payload,symbol,receipt_id,received_at,"
            "status_code,content_type,payload_sha256,raw_payload "
            "FROM kis_futures_quote_receipts WHERE request_id=? AND symbol=?",
            (request_id, symbol),
        ).fetchone()

    @staticmethod
    def _run_row(
        connection: sqlite3.Connection,
        request_id: str,
    ) -> RunRow | None:
        return connection.execute(
            "SELECT run_id,run_sha256,run_payload "
            "FROM kis_futures_quote_runs WHERE request_id=?",
            (request_id,),
        ).fetchone()

    def _connection(self, *, write: bool) -> sqlite3.Connection:
        try:
            if self.path.is_symlink():
                raise KisOverseasFuturesStoreError
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(self.path.parent, 0o700)
                connection = sqlite3.connect(self.path)
                os.chmod(self.path, 0o600)
                if connection.execute("PRAGMA user_version").fetchone() == (0,):
                    connection.executescript(
                        CREATE_KIS_OVERSEAS_FUTURES_SCHEMA
                    )
                    connection.execute(
                        "PRAGMA user_version="
                        f"{KIS_OVERSEAS_FUTURES_SCHEMA_VERSION}"
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
            if connection.execute("PRAGMA user_version").fetchone() != (
                KIS_OVERSEAS_FUTURES_SCHEMA_VERSION,
            ):
                connection.close()
                raise KisOverseasFuturesStoreError
            return connection
        except (OSError, sqlite3.Error):
            raise KisOverseasFuturesStoreError from None


__all__ = (
    "KisOverseasFuturesStore",
    "KisOverseasFuturesStoreError",
)
