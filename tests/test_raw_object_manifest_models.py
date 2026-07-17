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
    RawReceiptProjectionFixtureReceipt,
)

RECEIVED_AT = dt.datetime(2026, 7, 17, 9, 30, tzinfo=dt.UTC)
PAYLOAD = b"synthetic-private-payload"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def test_raw_receipt_hides_payload_from_repr_and_rejects_noncanonical_values() -> None:
    receipt = RawReceipt.from_payload(
        receipt_id="a" * 64,
        source_id="synthetic.market",
        market_date=dt.date(2026, 7, 17),
        received_at=RECEIVED_AT,
        payload_sha256=PAYLOAD_SHA256,
        payload=RawReceiptPayload(PAYLOAD),
    )

    assert PAYLOAD.decode() not in repr(receipt)
    assert PAYLOAD.decode() not in repr(receipt.payload)
    with pytest.raises((ValidationError, ValueError), match="invalid raw receipt"):
        _ = RawReceipt.from_payload(
            receipt_id="request-key-should-not-be-an-id",
            source_id="Synthetic.Market",
            market_date=dt.date(2026, 7, 17),
            received_at=RECEIVED_AT.replace(tzinfo=None),
            payload_sha256=PAYLOAD_SHA256.upper(),
            payload=RawReceiptPayload(PAYLOAD),
        )
    with pytest.raises((ValidationError, ValueError), match="invalid raw receipt"):
        _ = RawReceipt.from_payload(
            receipt_id="a" * 64,
            source_id="synthetic.market",
            market_date=dt.date(2026, 7, 17),
            received_at=RECEIVED_AT,
            payload_sha256="0" * 64,
            payload=RawReceiptPayload(PAYLOAD),
        )


def test_raw_receipt_public_pydantic_exports_hide_payload_bytes() -> None:
    raw_secret = b"distinctive-raw-secret-for-export-test"
    receipt = RawReceipt.from_payload(
        receipt_id="a" * 64,
        source_id="synthetic.market",
        market_date=dt.date(2026, 7, 17),
        received_at=RECEIVED_AT,
        payload_sha256=hashlib.sha256(raw_secret).hexdigest(),
        payload=RawReceiptPayload(raw_secret),
    )

    dumped = receipt.model_dump()

    assert "payload" not in dumped
    assert raw_secret.decode() not in repr(receipt)
    assert raw_secret.decode() not in repr(dumped)
    assert raw_secret.decode() not in receipt.model_dump_json()
    assert "payload" not in receipt.model_dump(include={"payload"})
    assert raw_secret.decode() not in receipt.model_dump_json(include={"payload"})


def test_raw_receipt_constructor_validates_required_payload_and_is_frozen() -> None:
    raw_secret = b"distinctive-raw-secret-for-constructor-test"
    payload_sha256 = hashlib.sha256(raw_secret).hexdigest()
    receipt = RawReceipt(
        receipt_id="a" * 64,
        source_id="synthetic.market",
        market_date=dt.date(2026, 7, 17),
        received_at=RECEIVED_AT,
        payload_sha256=payload_sha256,
        payload=RawReceiptPayload(raw_secret),
    )

    assert receipt.payload.value == raw_secret
    with pytest.raises(ValidationError, match="invalid raw receipt"):
        _ = RawReceipt(
            receipt_id="a" * 64,
            source_id="synthetic.market",
            market_date=dt.date(2026, 7, 17),
            received_at=RECEIVED_AT,
            payload_sha256="0" * 64,
            payload=RawReceiptPayload(raw_secret),
        )
    with pytest.raises(ValidationError):
        _ = RawReceipt(  # type: ignore[call-arg]
            receipt_id="a" * 64,
            source_id="synthetic.market",
            market_date=dt.date(2026, 7, 17),
            received_at=RECEIVED_AT,
            payload_sha256=payload_sha256,
        )
    with pytest.raises(ValidationError):
        receipt.payload = RawReceiptPayload(b"replacement")


def test_fixture_receipt_excludes_reversible_base64_from_public_exports() -> None:
    payload_base64 = "ZGlzdGluY3RpdmUtcmV2ZXJzaWJsZS1maXh0dXJlLXBheWxvYWQ="
    receipt = RawReceiptProjectionFixtureReceipt.model_validate(
        {
            "receipt_id": "a" * 64,
            "received_at": "2026-07-17T09:30:00Z",
            "payload_sha256": PAYLOAD_SHA256,
            "payload_base64": payload_base64,
        }
    )

    assert "payload_base64" not in receipt.model_dump()
    assert payload_base64 not in receipt.model_dump_json()
    assert "payload_base64" not in receipt.model_dump(include={"payload_base64"})
    assert payload_base64 not in receipt.model_dump_json(include={"payload_base64"})


@pytest.mark.parametrize(
    "timestamp",
    (
        "2026-07-17T09:30:00+00:00",
        "2026-07-17T18:30:00+09:00",
        "2026-07-17T09:30:00.000Z",
    ),
)
def test_fixture_receipt_rejects_normalized_timestamp_variants(timestamp: str) -> None:
    with pytest.raises(ValidationError, match="invalid raw receipt projection fixture"):
        _ = RawReceiptProjectionFixtureReceipt.model_validate(
            {
                "receipt_id": "a" * 64,
                "received_at": timestamp,
                "payload_sha256": PAYLOAD_SHA256,
                "payload_base64": "ZGlzdGluY3RpdmUtcmV2ZXJzaWJsZS1maXh0dXJlLXBheWxvYWQ=",
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("receipt_id", b"a" * 64),
        ("payload_sha256", b"a" * 64),
        ("market_date", "2026-07-17T00:00:00+00:00"),
        ("received_at", "2026-07-17T09:30:00+00:00"),
    ),
)
def test_raw_receipt_rejects_values_before_pydantic_coercion(field: str, value: object) -> None:
    values: dict[str, object] = {
        "receipt_id": "a" * 64,
        "source_id": "synthetic.market",
        "market_date": dt.date(2026, 7, 17),
        "received_at": RECEIVED_AT,
        "payload_sha256": PAYLOAD_SHA256,
        "payload": RawReceiptPayload(PAYLOAD),
    }
    values[field] = value

    with pytest.raises(ValidationError, match="invalid raw receipt"):
        _ = RawReceipt.from_payload(**values)


def test_raw_object_reference_rejects_negative_byte_size() -> None:
    with pytest.raises(ValidationError, match="invalid raw object receipt reference"):
        _ = RawObjectReceiptReference(
            receipt_id="a" * 64,
            received_at=RECEIVED_AT,
            payload_sha256=PAYLOAD_SHA256,
            byte_size=-1,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("receipt_id", b"a" * 64),
        ("payload_sha256", b"a" * 64),
        ("received_at", "2026-07-17T09:30:00+00:00"),
        ("byte_size", True),
    ),
)
def test_raw_object_reference_rejects_coerced_types(field: str, value: object) -> None:
    values: dict[str, object] = {
        "receipt_id": "a" * 64,
        "received_at": RECEIVED_AT,
        "payload_sha256": PAYLOAD_SHA256,
        "byte_size": len(PAYLOAD),
    }
    values[field] = value

    with pytest.raises(ValidationError, match="invalid raw object receipt reference"):
        _ = RawObjectReceiptReference.model_validate(values)


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
