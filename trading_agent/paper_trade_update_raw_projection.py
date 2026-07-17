from __future__ import annotations

import datetime as dt
import re
from typing import Final, override
from zoneinfo import ZoneInfo

from trading_agent.execution_store_reader import ExecutionStoreReader
from trading_agent.raw_object_manifest_models import (
    RawObjectPartitionManifest,
    RawReceipt,
    RawReceiptPayload,
)
from trading_agent.raw_receipt_projection import project_raw_receipt_partition
from trading_agent.trade_update_receipt_models import (
    TradeUpdateRawReceiptProjectionRecord,
    TradeUpdateReceiptProjectionSnapshot,
)

_SOURCE_ID: Final = "us.alpaca.paper.trade_updates"
_NEW_YORK = ZoneInfo("America/New_York")
_RECEIPT_ID = re.compile(r"^[0-9a-f]{64}$")


class InvalidPaperTradeUpdateRawReceiptProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "paper trade update raw receipt projection is invalid"


def project_paper_trade_update_receipts(
    reader: ExecutionStoreReader,
    *,
    market_date: dt.date,
) -> RawObjectPartitionManifest | None:
    try:
        if not isinstance(reader, ExecutionStoreReader) or type(market_date) is not dt.date:
            raise InvalidPaperTradeUpdateRawReceiptProjectionError
        snapshot = reader.trade_update_receipt_projection_snapshot(market_date=market_date)
        if (
            type(snapshot) is not TradeUpdateReceiptProjectionSnapshot
            or type(snapshot.receipts) is not tuple
            or type(snapshot.parent_ledger_generation) is not int
            or snapshot.parent_ledger_generation < 0
        ):
            raise InvalidPaperTradeUpdateRawReceiptProjectionError
        if not snapshot.receipts:
            if snapshot.parent_ledger_generation != 0:
                raise InvalidPaperTradeUpdateRawReceiptProjectionError
            return None
        if snapshot.parent_ledger_generation <= 0:
            raise InvalidPaperTradeUpdateRawReceiptProjectionError
        raw_receipts = tuple(
            sorted(
                (_raw_receipt(record, market_date=market_date) for record in snapshot.receipts),
                key=lambda receipt: receipt.receipt_id,
            )
        )
        return project_raw_receipt_partition(
            raw_receipts,
            source_id=_SOURCE_ID,
            market_date=market_date,
            parent_ledger_generation=snapshot.parent_ledger_generation,
        )
    except Exception:
        raise InvalidPaperTradeUpdateRawReceiptProjectionError from None


def _raw_receipt(
    record: TradeUpdateRawReceiptProjectionRecord,
    *,
    market_date: dt.date,
) -> RawReceipt:
    if (
        type(record) is not TradeUpdateRawReceiptProjectionRecord
        or type(record.receipt_id) is not str
        or type(record.received_at) is not dt.datetime
        or type(record.payload_sha256) is not str
        or type(record.raw_payload) is not bytes
        or _RECEIPT_ID.fullmatch(record.receipt_id) is None
        or not _is_aware(record.received_at)
        or record.received_at.astimezone(_NEW_YORK).date() != market_date
    ):
        raise InvalidPaperTradeUpdateRawReceiptProjectionError
    return RawReceipt.from_payload(
        receipt_id=record.receipt_id,
        source_id=_SOURCE_ID,
        market_date=market_date,
        received_at=record.received_at,
        payload_sha256=record.payload_sha256,
        payload=RawReceiptPayload(record.raw_payload),
    )


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
