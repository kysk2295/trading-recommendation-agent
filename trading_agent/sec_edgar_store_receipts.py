from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3

from trading_agent.sec_edgar_models import SecSubmissionRawResponse, SecSubmissionRun
from trading_agent.sec_edgar_store_projection import receipt_bounds_valid
from trading_agent.sec_edgar_store_types import InvalidSecEdgarStoreError, SecStoredReceipt


def receipt_from_connection(
    connection: sqlite3.Connection,
    collection_id: str,
    cik: str,
) -> SecStoredReceipt | None:
    row = connection.execute(
        "SELECT receipt_id,collection_id,cik,received_at,status_code,content_type,"
        "content_encoding,payload_sha256,raw_payload FROM sec_submission_receipts "
        "WHERE collection_id=? AND cik=?",
        (collection_id, cik),
    ).fetchone()
    if row is None:
        return None
    response = SecSubmissionRawResponse(
        collection_id=row[1],
        cik=row[2],
        received_at=dt.datetime.fromisoformat(row[3]),
        status_code=row[4],
        content_type=row[5],
        content_encoding=row[6],
        raw_payload=row[8],
    )
    if receipt_row(response) != tuple(row):
        raise InvalidSecEdgarStoreError
    return SecStoredReceipt(response)


def receipt_by_id_from_connection(
    connection: sqlite3.Connection,
    receipt_id: str,
) -> SecStoredReceipt | None:
    row = connection.execute(
        "SELECT collection_id,cik FROM sec_submission_receipts WHERE receipt_id=?",
        (receipt_id,),
    ).fetchone()
    if row is None:
        return None
    receipt = receipt_from_connection(connection, row[0], row[1])
    if receipt is None or receipt.response.receipt_id != receipt_id:
        raise InvalidSecEdgarStoreError
    return receipt


def require_receipt(connection: sqlite3.Connection, run: SecSubmissionRun) -> None:
    receipt = receipt_from_connection(connection, run.collection_id, run.cik)
    if (
        receipt is None
        or receipt.response.receipt_id != run.receipt_id
        or not receipt_bounds_valid(receipt.response, run)
    ):
        raise InvalidSecEdgarStoreError


def receipt_row(response: SecSubmissionRawResponse) -> tuple[str | int | bytes, ...]:
    return (
        response.receipt_id,
        response.collection_id,
        response.cik,
        response.received_at.isoformat(),
        response.status_code,
        response.content_type,
        response.content_encoding,
        hashlib.sha256(response.raw_payload).hexdigest(),
        response.raw_payload,
    )


def validated_response(response: SecSubmissionRawResponse) -> SecSubmissionRawResponse:
    return SecSubmissionRawResponse(
        collection_id=response.collection_id,
        cik=response.cik,
        received_at=response.received_at,
        status_code=response.status_code,
        content_type=response.content_type,
        raw_payload=response.raw_payload,
        content_encoding=response.content_encoding,
    )


__all__ = (
    "receipt_by_id_from_connection",
    "receipt_from_connection",
    "receipt_row",
    "require_receipt",
    "validated_response",
)
