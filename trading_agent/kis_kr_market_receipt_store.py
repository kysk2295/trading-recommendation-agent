from __future__ import annotations

import datetime as dt
import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Final, final, override

from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
)

_SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE kis_kr_market_receipts (
  receipt_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  symbol TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  content_type TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  raw_payload BLOB NOT NULL,
  UNIQUE(kind, symbol, received_at)
);
CREATE INDEX kis_kr_market_receipts_by_symbol_time
ON kis_kr_market_receipts(symbol, received_at, kind);
CREATE TRIGGER kis_kr_market_receipts_no_update
BEFORE UPDATE ON kis_kr_market_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER kis_kr_market_receipts_no_delete
BEFORE DELETE ON kis_kr_market_receipts BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
_OBJECTS: Final = frozenset(
    {
        "kis_kr_market_receipts",
        "kis_kr_market_receipts_by_symbol_time",
        "kis_kr_market_receipts_no_update",
        "kis_kr_market_receipts_no_delete",
    }
)
_ROW = tuple[str, str, str, str, int, str, str, bytes]


class InvalidKisKrMarketReceiptStoreError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR market receipt store is invalid"


@final
class KisKrMarketReceiptStore:
    __slots__ = ("path",)

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path.resolve(strict=False)

    def receipts(self) -> tuple[KisKrMarketReceipt, ...]:
        if self.path.is_symlink():
            raise InvalidKisKrMarketReceiptStoreError
        if not self.path.exists():
            return ()
        try:
            _require_private_file(self.path)
            with closing(sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)) as connection:
                _ = connection.execute("PRAGMA query_only = ON")
                _require_schema(connection)
                rows: list[_ROW] = connection.execute(
                    "SELECT receipt_id,kind,symbol,received_at,status_code,content_type,"
                    "payload_sha256,raw_payload FROM kis_kr_market_receipts "
                    "ORDER BY received_at,kind,receipt_id"
                ).fetchall()
            return tuple(_receipt_from_row(row) for row in rows)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKisKrMarketReceiptStoreError from None

    def append(self, receipt: KisKrMarketReceipt) -> bool:
        try:
            receipt = _validated_receipt(receipt)
            _ = self.receipts()
            if self.path.is_symlink():
                raise InvalidKisKrMarketReceiptStoreError
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            with closing(sqlite3.connect(self.path, timeout=0.0)) as connection:
                _prepare(connection)
                os.chmod(self.path, 0o600)
                connection.execute("BEGIN IMMEDIATE")
                row = _row(receipt)
                existing: _ROW | None = connection.execute(
                    "SELECT receipt_id,kind,symbol,received_at,status_code,content_type,"
                    "payload_sha256,raw_payload FROM kis_kr_market_receipts "
                    "WHERE kind=? AND symbol=? AND received_at=?",
                    (receipt.kind.value, receipt.symbol, receipt.received_at.isoformat()),
                ).fetchone()
                if existing is not None:
                    if existing != row:
                        raise InvalidKisKrMarketReceiptStoreError
                    connection.rollback()
                    return False
                _ = connection.execute(
                    "INSERT INTO kis_kr_market_receipts VALUES (?,?,?,?,?,?,?,?)",
                    row,
                )
                connection.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise InvalidKisKrMarketReceiptStoreError from None


def _row(receipt: KisKrMarketReceipt) -> _ROW:
    payload_sha = receipt.payload_sha256
    material = "|".join(
        (
            receipt.kind.value,
            receipt.symbol,
            receipt.received_at.isoformat(),
            str(receipt.status_code),
            receipt.content_type,
            payload_sha,
        )
    )
    return (
        hashlib.sha256(material.encode()).hexdigest(),
        receipt.kind.value,
        receipt.symbol,
        receipt.received_at.isoformat(),
        receipt.status_code,
        receipt.content_type,
        payload_sha,
        receipt.raw_payload,
    )


def _receipt_from_row(row: _ROW) -> KisKrMarketReceipt:
    _, kind, symbol, received_at, status_code, content_type, payload_sha, raw_payload = row
    receipt = KisKrMarketReceipt(
        kind=KisKrMarketReceiptKind(kind),
        symbol=symbol,
        received_at=dt.datetime.fromisoformat(received_at),
        status_code=status_code,
        content_type=content_type,
        raw_payload=raw_payload,
    )
    if row != _row(receipt) or payload_sha != hashlib.sha256(raw_payload).hexdigest():
        raise InvalidKisKrMarketReceiptStoreError
    return receipt


def _validated_receipt(receipt: KisKrMarketReceipt) -> KisKrMarketReceipt:
    return KisKrMarketReceipt(
        kind=receipt.kind,
        symbol=receipt.symbol,
        received_at=receipt.received_at,
        status_code=receipt.status_code,
        content_type=receipt.content_type,
        raw_payload=receipt.raw_payload,
    )


def _prepare(connection: sqlite3.Connection) -> None:
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version == (0,):
        connection.executescript(f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version={_SCHEMA_VERSION};COMMIT;")
    _require_schema(connection)


def _require_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA_VERSION,):
        raise InvalidKisKrMarketReceiptStoreError
    objects = frozenset(
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    )
    if objects != _OBJECTS:
        raise InvalidKisKrMarketReceiptStoreError


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidKisKrMarketReceiptStoreError
