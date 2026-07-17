from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from pydantic import ValidationError

from trading_agent.raw_object_manifest_models import (
    RawObjectPartitionManifest,
    RawObjectReceiptReference,
    RawReceipt,
    RawReceiptPayload,
)

RECEIVED_AT = dt.datetime(2026, 7, 17, 9, 30, tzinfo=dt.UTC)
PAYLOAD = b"synthetic-private-payload"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def test_raw_receipt_hides_payload_from_repr_and_rejects_noncanonical_values() -> None:
    receipt = RawReceipt(
        receipt_id="a" * 64,
        source_id="synthetic.market",
        market_date=dt.date(2026, 7, 17),
        received_at=RECEIVED_AT,
        payload_sha256=PAYLOAD_SHA256,
        payload=RawReceiptPayload(PAYLOAD),
    )

    assert PAYLOAD.decode() not in repr(receipt)
    assert PAYLOAD.decode() not in repr(receipt.payload)
    with pytest.raises(ValidationError, match="invalid raw receipt"):
        _ = RawReceipt(
            receipt_id="request-key-should-not-be-an-id",
            source_id="Synthetic.Market",
            market_date=dt.date(2026, 7, 17),
            received_at=RECEIVED_AT.replace(tzinfo=None),
            payload_sha256=PAYLOAD_SHA256.upper(),
            payload=RawReceiptPayload(PAYLOAD),
        )
    with pytest.raises(ValidationError, match="invalid raw receipt"):
        _ = RawReceipt(
            receipt_id="a" * 64,
            source_id="synthetic.market",
            market_date=dt.date(2026, 7, 17),
            received_at=RECEIVED_AT,
            payload_sha256="0" * 64,
            payload=RawReceiptPayload(PAYLOAD),
        )


def test_raw_object_reference_rejects_negative_byte_size() -> None:
    with pytest.raises(ValidationError, match="invalid raw object receipt reference"):
        _ = RawObjectReceiptReference(
            receipt_id="a" * 64,
            received_at=RECEIVED_AT,
            payload_sha256=PAYLOAD_SHA256,
            byte_size=-1,
        )


def test_manifest_contract_rejects_extra_fields_and_noncanonical_hashes() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ = RawObjectPartitionManifest.model_validate(
            {
                "schema_version": 1,
                "manifest_id": "a" * 64,
                "content_sha256": "a" * 64,
                "source_id": "synthetic.market",
                "market_date": "2026-07-17",
                "received_at_start": "2026-07-17T09:30:00Z",
                "received_at_end": "2026-07-17T09:30:00Z",
                "receipt_count": 1,
                "total_byte_size": 1,
                "parent_ledger_generation": 0,
                "receipts": [],
                "payload_path": "/tmp/raw-payload",
            }
        )
