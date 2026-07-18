from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import sqlite3
from itertools import pairwise
from pathlib import Path
from typing import final

from trading_agent.alpaca_models import BARS_ADAPTER
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipMinutePage,
    AlpacaSipMinutePageRequest,
    AlpacaSipRawPage,
    AlpacaSipRuntimeError,
    StoredAlpacaSipRawPage,
)
from trading_agent.alpaca_sip_runtime_schema import (
    ALPACA_SIP_RUNTIME_SCHEMA_VERSION,
    CREATE_ALPACA_SIP_RUNTIME_SCHEMA,
)


@final
class AlpacaSipRuntimeEvidenceStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def page_count(self) -> int:
        return self._count("alpaca_sip_raw_pages")

    def projection_count(self) -> int:
        return self._count("alpaca_sip_projections")

    def projection_directory(self, projection_key: str) -> Path | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[str] | None = connection.execute(
                    "SELECT dataset_directory FROM alpaca_sip_projections WHERE projection_key = ?",
                    (projection_key,),
                ).fetchone()
            return None if row is None else Path(row[0])
        except sqlite3.Error:
            raise AlpacaSipRuntimeError from None

    def load_page_set(
        self,
        request: AlpacaSipMinutePageRequest,
    ) -> AlpacaSipMinutePage | None:
        if not self.path.is_file():
            return None
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                rows = connection.execute(
                    "SELECT receipt_id,page_index,page_token,received_at,payload_sha256,raw_response "
                    "FROM alpaca_sip_raw_pages WHERE session_date=? AND symbol=? "
                    "AND request_start_at=? AND request_end_at=? ORDER BY page_index",
                    (
                        request.session_date.isoformat(),
                        request.symbol,
                        request.start_at.isoformat(),
                        request.end_at.isoformat(),
                    ),
                ).fetchall()
            if not rows:
                return None
            pages = tuple(_loaded_page(request, row) for row in rows)
            _validate_page_chain(pages)
            return AlpacaSipMinutePage(request, pages)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipRuntimeError from None

    def append_page(
        self,
        request: AlpacaSipMinutePageRequest,
        page: AlpacaSipRawPage,
    ) -> StoredAlpacaSipRawPage:
        try:
            receipt_id = _receipt_id(request, page)
            payload_sha256 = hashlib.sha256(page.raw_response).hexdigest()
            with _Writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT generation,receipt_id,page_index,received_at,payload_sha256,raw_response "
                    "FROM alpaca_sip_raw_pages WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
                if existing is None:
                    cursor = connection.execute(
                        "INSERT INTO alpaca_sip_raw_pages "
                        "(receipt_id,session_date,symbol,request_start_at,request_end_at,page_index,"
                        "page_token,received_at,payload_sha256,raw_response) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            receipt_id,
                            request.session_date.isoformat(),
                            request.symbol,
                            request.start_at.isoformat(),
                            request.end_at.isoformat(),
                            page.page_index,
                            page.page_token,
                            page.received_at.isoformat(),
                            payload_sha256,
                            page.raw_response,
                        ),
                    )
                    connection.commit()
                    generation = cursor.lastrowid
                    if type(generation) is not int:
                        raise AlpacaSipRuntimeError
                    return StoredAlpacaSipRawPage(
                        generation,
                        receipt_id,
                        page.page_index,
                        page.received_at,
                        payload_sha256,
                        page.raw_response,
                    )
                stored = _stored(existing)
                if stored.page_index != page.page_index or stored.raw_response != page.raw_response:
                    raise AlpacaSipRuntimeError
                return stored
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipRuntimeError from None

    def append_projection(
        self,
        dataset_id: str,
        projection_key: str,
        dataset_directory: Path,
        identity_scope: str,
        recorded_at: dt.datetime,
    ) -> None:
        try:
            row = (
                dataset_id,
                projection_key,
                str(dataset_directory),
                identity_scope,
                recorded_at.isoformat(),
            )
            with _Writer(self.path) as connection:
                existing = connection.execute(
                    "SELECT dataset_id,projection_key,dataset_directory,identity_scope,recorded_at "
                    "FROM alpaca_sip_projections WHERE dataset_id = ?",
                    (dataset_id,),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != row:
                        raise AlpacaSipRuntimeError
                    return
                _ = connection.execute("INSERT INTO alpaca_sip_projections VALUES (?, ?, ?, ?, ?)", row)
                connection.commit()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise AlpacaSipRuntimeError from None

    def _count(self, table: str) -> int:
        if not self.path.is_file():
            return 0
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as connection:
                _require_schema(connection)
                row: tuple[int] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            return row[0]
        except sqlite3.Error:
            raise AlpacaSipRuntimeError from None


class _Writer:
    __slots__ = ("_connection", "_handle", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None
        self._connection = None

    def __enter__(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(f"{self._path}.writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        self._handle = os.fdopen(descriptor, "a+", encoding="utf-8")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        try:
            connection = sqlite3.connect(self._path)
            os.chmod(self._path, 0o600)
            _prepare(connection)
            self._connection = connection
            return connection
        except (OSError, sqlite3.Error, ValueError):
            self._handle.close()
            self._handle = None
            raise

    def __exit__(self, *_args: object) -> None:
        if self._connection is not None:
            self._connection.close()
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()


def _receipt_id(request: AlpacaSipMinutePageRequest, page: AlpacaSipRawPage) -> str:
    if (
        type(request) is not AlpacaSipMinutePageRequest
        or type(page) is not AlpacaSipRawPage
        or type(page.page_index) is not int
        or page.page_index < 0
        or type(page.raw_response) is not bytes
        or not page.raw_response
    ):
        raise AlpacaSipRuntimeError
    identity = {
        "end": request.end_at.isoformat(),
        "page_index": page.page_index,
        "page_token": page.page_token,
        "payload_sha256": hashlib.sha256(page.raw_response).hexdigest(),
        "session_date": request.session_date.isoformat(),
        "start": request.start_at.isoformat(),
        "symbol": request.symbol,
    }
    encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _stored(row: tuple[int, str, int, str, str, bytes]) -> StoredAlpacaSipRawPage:
    return StoredAlpacaSipRawPage(row[0], row[1], row[2], dt.datetime.fromisoformat(row[3]), row[4], row[5])


def _loaded_page(
    request: AlpacaSipMinutePageRequest,
    row: tuple[str, int, str | None, str, str, bytes],
) -> AlpacaSipRawPage:
    page = AlpacaSipRawPage(
        row[1],
        row[2],
        dt.datetime.fromisoformat(row[3]),
        row[5],
        BARS_ADAPTER.validate_json(row[5]),
    )
    if row[0] != _receipt_id(request, page) or row[4] != hashlib.sha256(row[5]).hexdigest():
        raise AlpacaSipRuntimeError
    return page


def _validate_page_chain(pages: tuple[AlpacaSipRawPage, ...]) -> None:
    if (
        tuple(page.page_index for page in pages) != tuple(range(len(pages)))
        or pages[0].page_token is not None
        or pages[-1].payload.next_page_token is not None
    ):
        raise AlpacaSipRuntimeError
    for previous, current in pairwise(pages):
        if previous.payload.next_page_token != current.page_token:
            raise AlpacaSipRuntimeError


def _prepare(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(CREATE_ALPACA_SIP_RUNTIME_SCHEMA)
        _ = connection.execute(f"PRAGMA user_version = {ALPACA_SIP_RUNTIME_SCHEMA_VERSION}")
        connection.commit()
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (ALPACA_SIP_RUNTIME_SCHEMA_VERSION,):
        raise AlpacaSipRuntimeError


__all__ = ("AlpacaSipRuntimeEvidenceStore",)
