from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Sequence
from typing import override

from pydantic import ValidationError

from trading_agent.raw_object_manifest_models import (
    RawObjectPartitionManifest,
    RawObjectReceiptReference,
    RawReceipt,
    RawReceiptPayload,
)

_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class InvalidRawReceiptProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "raw receipt partition is invalid"


def project_raw_receipt_partition(
    receipts: Sequence[RawReceipt],
    *,
    source_id: str,
    market_date: dt.date,
    parent_ledger_generation: int,
) -> RawObjectPartitionManifest:
    """Build a content-addressed manifest without changing the supplied receipts."""
    try:
        canonical_receipts = tuple(receipts)
        receipt_ids = tuple(receipt.receipt_id for receipt in canonical_receipts)
        if (
            _SOURCE_ID.fullmatch(source_id) is None
            or isinstance(market_date, dt.datetime)
            or parent_ledger_generation < 0
            or not canonical_receipts
            or receipt_ids != tuple(sorted(set(receipt_ids)))
            or any(
                receipt.source_id != source_id or receipt.market_date != market_date for receipt in canonical_receipts
            )
            or any(
                not isinstance(receipt.payload, RawReceiptPayload)
                or hashlib.sha256(receipt.payload.value).hexdigest() != receipt.payload_sha256
                for receipt in canonical_receipts
            )
        ):
            raise InvalidRawReceiptProjectionError
        references = tuple(
            RawObjectReceiptReference(
                receipt_id=receipt.receipt_id,
                received_at=receipt.received_at,
                payload_sha256=receipt.payload_sha256,
                byte_size=len(receipt.payload.value),
            )
            for receipt in canonical_receipts
        )
        received_at_start = min(item.received_at for item in references)
        received_at_end = max(item.received_at for item in references)
        total_byte_size = sum(item.byte_size for item in references)
        content_sha256 = _content_sha256(
            source_id=source_id,
            market_date=market_date,
            received_at_start=received_at_start,
            received_at_end=received_at_end,
            receipt_count=len(references),
            total_byte_size=total_byte_size,
            parent_ledger_generation=parent_ledger_generation,
            receipts=references,
        )
        return RawObjectPartitionManifest(
            manifest_id=content_sha256,
            content_sha256=content_sha256,
            source_id=source_id,
            market_date=market_date,
            received_at_start=received_at_start,
            received_at_end=received_at_end,
            receipt_count=len(references),
            total_byte_size=total_byte_size,
            parent_ledger_generation=parent_ledger_generation,
            receipts=references,
        )
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidRawReceiptProjectionError from None


def _content_sha256(
    *,
    source_id: str,
    market_date: dt.date,
    received_at_start: dt.datetime,
    received_at_end: dt.datetime,
    receipt_count: int,
    total_byte_size: int,
    parent_ledger_generation: int,
    receipts: tuple[RawObjectReceiptReference, ...],
) -> str:
    provisional = RawObjectPartitionManifest.model_construct(
        manifest_id="0" * 64,
        content_sha256="0" * 64,
        source_id=source_id,
        market_date=market_date,
        received_at_start=received_at_start,
        received_at_end=received_at_end,
        receipt_count=receipt_count,
        total_byte_size=total_byte_size,
        parent_ledger_generation=parent_ledger_generation,
        receipts=receipts,
    )
    from trading_agent.raw_object_manifest_models import _manifest_content_sha256

    return _manifest_content_sha256(provisional)
