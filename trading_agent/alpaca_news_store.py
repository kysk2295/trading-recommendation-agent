from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import final

from trading_agent.alpaca_news_models import (
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
    AlpacaNewsRun,
)
from trading_agent.alpaca_news_replay import (
    evaluate_alpaca_news_receipts,
    require_alpaca_news_run_projection,
)
from trading_agent.alpaca_news_store_codec import (
    AlpacaNewsReceiptRow,
    AlpacaNewsRunRow,
    news_receipt_from_row,
    news_receipt_row,
    news_run_from_row,
    news_run_row,
)
from trading_agent.alpaca_news_store_sql import (
    AlpacaNewsStoreError,
    news_reader,
    news_writer,
)
from trading_agent.private_directory_identity import (
    absolute_private_path,
    open_private_parent,
    require_private_directory_query_only,
)


@dataclass(frozen=True, slots=True)
class StoredAlpacaNewsReceipt:
    request: AlpacaNewsRequest = field(repr=False)
    response: AlpacaNewsRawResponse = field(repr=False)


@final
class AlpacaNewsStore:
    __slots__ = ("path",)

    def __init__(self, path: Path) -> None:
        self.path = absolute_private_path(path)

    def preflight_write(self) -> None:
        with news_writer(self.path) as connection:
            _require_all(connection)

    def append_receipt(
        self,
        request: AlpacaNewsRequest,
        response: AlpacaNewsRawResponse,
    ) -> bool:
        if response.request_id != request.request_id:
            raise AlpacaNewsStoreError
        row = news_receipt_row(request, response)
        with news_writer(self.path) as connection:
            _require_all(connection)
            if _run_from_connection(connection, request.request_id) is not None:
                raise AlpacaNewsStoreError
            existing = _receipt_row(connection, request.request_id, response.page_index)
            if existing is not None:
                if existing != row:
                    raise AlpacaNewsStoreError
                return False
            _ = connection.execute(
                "INSERT INTO alpaca_news_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
        return True

    def append_run(self, run: AlpacaNewsRun) -> bool:
        row = news_run_row(run)
        with news_writer(self.path) as connection:
            _require_all(connection)
            receipts = _receipts_from_connection(connection, run.request.request_id)
            _ = require_alpaca_news_run_projection(
                run,
                tuple(item.response for item in receipts),
            )
            existing = _run_row(connection, run.request.request_id)
            if existing is not None:
                if existing != row:
                    raise AlpacaNewsStoreError
                return False
            _ = connection.execute("INSERT INTO alpaca_news_runs VALUES (?,?,?,?)", row)
        return True

    def receipts(self, request_id: str) -> tuple[StoredAlpacaNewsReceipt, ...]:
        if not self._exists():
            return ()
        with news_reader(self.path) as connection:
            _require_all(connection)
            return _receipts_from_connection(connection, request_id)

    def run(self, request_id: str) -> AlpacaNewsRun | None:
        if not self._exists():
            return None
        with news_reader(self.path) as connection:
            _require_all(connection)
            return _run_from_connection(connection, request_id)

    def counts(self) -> tuple[int, int]:
        with news_reader(self.path) as connection:
            _require_all(connection)
            receipt_count = connection.execute("SELECT COUNT(*) FROM alpaca_news_receipts").fetchone()
            run_count = connection.execute("SELECT COUNT(*) FROM alpaca_news_runs").fetchone()
            if receipt_count is None or run_count is None:
                raise AlpacaNewsStoreError
            return int(receipt_count[0]), int(run_count[0])

    def _exists(self) -> bool:
        try:
            parent = open_private_parent(self.path.parent, create=False)
            try:
                require_private_directory_query_only(parent)
                metadata = os.stat(self.path.name, dir_fd=parent, follow_symlinks=False)
            finally:
                os.close(parent)
        except FileNotFoundError:
            return False
        except (OSError, TypeError, ValueError):
            raise AlpacaNewsStoreError from None
        if not stat.S_ISREG(metadata.st_mode):
            raise AlpacaNewsStoreError
        return True


def _require_all(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT request_id FROM alpaca_news_receipts UNION SELECT request_id FROM alpaca_news_runs"
    ).fetchall()
    for (request_id,) in rows:
        if not isinstance(request_id, str):
            raise AlpacaNewsStoreError
        receipts = _receipts_from_connection(connection, request_id)
        run = _run_from_connection(connection, request_id)
        if not receipts and run is None:
            raise AlpacaNewsStoreError
        if run is None:
            _ = evaluate_alpaca_news_receipts(
                receipts[0].request,
                tuple(item.response for item in receipts),
            )


def _receipts_from_connection(
    connection: sqlite3.Connection,
    request_id: str,
) -> tuple[StoredAlpacaNewsReceipt, ...]:
    rows: list[AlpacaNewsReceiptRow] = connection.execute(
        "SELECT receipt_id,request_id,request_payload_sha256,request_payload_json,page_index,"
        "page_token,received_at,status_code,content_type,content_encoding,payload_sha256,raw_payload "
        "FROM alpaca_news_receipts WHERE request_id=? ORDER BY page_index",
        (request_id,),
    ).fetchall()
    result: list[StoredAlpacaNewsReceipt] = []
    for row in rows:
        request, response = news_receipt_from_row(row)
        if request.request_id != request_id:
            raise AlpacaNewsStoreError
        result.append(StoredAlpacaNewsReceipt(request, response))
    if result and any(item.request != result[0].request for item in result):
        raise AlpacaNewsStoreError
    return tuple(result)


def _run_from_connection(
    connection: sqlite3.Connection,
    request_id: str,
) -> AlpacaNewsRun | None:
    row = _run_row(connection, request_id)
    if row is None:
        return None
    run = news_run_from_row(row)
    receipts = _receipts_from_connection(connection, request_id)
    _ = require_alpaca_news_run_projection(
        run,
        tuple(item.response for item in receipts),
    )
    return run


def _receipt_row(
    connection: sqlite3.Connection,
    request_id: str,
    page_index: int,
) -> AlpacaNewsReceiptRow | None:
    return connection.execute(
        "SELECT receipt_id,request_id,request_payload_sha256,request_payload_json,page_index,"
        "page_token,received_at,status_code,content_type,content_encoding,payload_sha256,raw_payload "
        "FROM alpaca_news_receipts WHERE request_id=? AND page_index=?",
        (request_id, page_index),
    ).fetchone()


def _run_row(connection: sqlite3.Connection, request_id: str) -> AlpacaNewsRunRow | None:
    return connection.execute(
        "SELECT run_id,request_id,payload_sha256,payload_json FROM alpaca_news_runs WHERE request_id=?",
        (request_id,),
    ).fetchone()


__all__ = ("AlpacaNewsStore", "AlpacaNewsStoreError", "StoredAlpacaNewsReceipt")
