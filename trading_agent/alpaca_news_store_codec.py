from __future__ import annotations

import datetime as dt
import hashlib

from trading_agent.alpaca_news_models import (
    AlpacaNewsRawResponse,
    AlpacaNewsRequest,
    AlpacaNewsRun,
)
from trading_agent.alpaca_news_store_sql import AlpacaNewsStoreError
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

type AlpacaNewsReceiptRow = tuple[
    str,
    str,
    str,
    str,
    int,
    str | None,
    str,
    int,
    str,
    str,
    str,
    bytes,
]
type AlpacaNewsRunRow = tuple[str, str, str, str]


def news_receipt_row(
    request: AlpacaNewsRequest,
    response: AlpacaNewsRawResponse,
) -> AlpacaNewsReceiptRow:
    request_json = canonical_experiment_ledger_json(request)
    return (
        response.receipt_id,
        request.request_id,
        _sha(request_json.encode()),
        request_json,
        response.page_index,
        response.page_token,
        response.received_at.isoformat(),
        response.status_code,
        response.content_type,
        response.content_encoding,
        _sha(response.raw_payload),
        response.raw_payload,
    )


def news_receipt_from_row(
    row: AlpacaNewsReceiptRow,
) -> tuple[AlpacaNewsRequest, AlpacaNewsRawResponse]:
    try:
        request = AlpacaNewsRequest.model_validate_json(row[3])
        response = AlpacaNewsRawResponse(
            request_id=row[1],
            page_index=row[4],
            page_token=row[5],
            received_at=dt.datetime.fromisoformat(row[6]),
            status_code=row[7],
            content_type=row[8],
            content_encoding=row[9],
            raw_payload=row[11],
        )
        if row != news_receipt_row(request, response) or row[10] != _sha(response.raw_payload):
            raise AlpacaNewsStoreError
        return request, response
    except (TypeError, ValueError):
        raise AlpacaNewsStoreError from None


def news_run_row(run: AlpacaNewsRun) -> AlpacaNewsRunRow:
    payload = canonical_experiment_ledger_json(run)
    return run.run_id, run.request.request_id, _sha(payload.encode()), payload


def news_run_from_row(row: AlpacaNewsRunRow) -> AlpacaNewsRun:
    try:
        run = AlpacaNewsRun.model_validate_json(row[3])
        if row != news_run_row(run):
            raise AlpacaNewsStoreError
        return run
    except (TypeError, ValueError):
        raise AlpacaNewsStoreError from None


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
