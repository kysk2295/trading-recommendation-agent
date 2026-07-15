from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from pydantic import ValidationError

from trading_agent.kr_source_collection_models import (
    KrCatalystObservationReceipt,
    KrSourceCollectionRun,
    KrSourceReceipt,
    StoredKrSourceReceipt,
)
from trading_agent.kr_theme_models import (
    KrCatalystSource,
    KrCoverageStatus,
)

OBSERVED_AT = dt.datetime(2026, 7, 15, 9, 1, tzinfo=dt.timezone(dt.timedelta(hours=9)))
PAYLOAD = b'{"status":"013","message":"no data"}'
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def test_source_receipt_identity_is_deterministic_and_raw_repr_is_private() -> None:
    receipt = _receipt()
    restored = KrSourceReceipt.model_validate(receipt.model_dump(mode="python"))
    stored = StoredKrSourceReceipt(receipt=receipt, raw_payload=PAYLOAD)

    assert restored.receipt_id == receipt.receipt_id
    assert len(receipt.receipt_id) == 64
    assert PAYLOAD.decode() not in repr(stored)
    assert "raw_payload" not in repr(stored)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_run_id", "bad/run"),
        ("request_key", "opendart:list:key=secret"),
        ("received_at", dt.datetime(2026, 7, 15, 0, 1)),
        ("http_status", 99),
        ("content_type", "application/json; charset=utf-8"),
        ("payload_sha256", "0" * 63),
    ],
)
def test_source_receipt_rejects_noncanonical_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _ = KrSourceReceipt.model_validate(
            _receipt().model_dump(mode="python") | {field: value}
        )


def test_observation_receipt_requires_exact_lineage_shape() -> None:
    link = KrCatalystObservationReceipt(
        collection_cycle_id="kr-cycle-001",
        catalyst_id="1" * 64,
        receipt_id="2" * 64,
        item_index=0,
        item_payload_sha256="3" * 64,
    )

    assert link.item_index == 0
    with pytest.raises(ValidationError):
        _ = KrCatalystObservationReceipt.model_validate(
            link.model_dump(mode="python") | {"item_index": -1}
        )


def test_source_run_requires_canonical_receipts_and_status_semantics() -> None:
    success = _run()

    assert success.status is KrCoverageStatus.SUCCESS
    with pytest.raises(ValidationError):
        _ = KrSourceCollectionRun.model_validate(
            success.model_dump(mode="python") | {"failure_code": "http_500"}
        )
    with pytest.raises(ValidationError):
        _ = KrSourceCollectionRun.model_validate(
            success.model_dump(mode="python")
            | {
                "status": KrCoverageStatus.FAILED,
                "failure_code": None,
            }
        )
    with pytest.raises(ValidationError):
        _ = KrSourceCollectionRun.model_validate(
            success.model_dump(mode="python")
            | {"receipt_ids": ("f" * 64, "a" * 64)}
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_count", -1),
        ("adapter_version", "opendart/list/v1"),
        ("completed_at", OBSERVED_AT - dt.timedelta(seconds=1)),
        ("failure_code", "HTTP 500"),
    ],
)
def test_source_run_rejects_invalid_terminal_metadata(field: str, value: object) -> None:
    failed = _run(status=KrCoverageStatus.FAILED, failure_code="transport_error")
    with pytest.raises(ValidationError):
        _ = KrSourceCollectionRun.model_validate(
            failed.model_dump(mode="python") | {field: value}
        )


def _receipt() -> KrSourceReceipt:
    return KrSourceReceipt(
        source_run_id="kr-cycle-001:dart",
        source=KrCatalystSource.DART,
        request_key="opendart:list:20260715:page:1",
        received_at=OBSERVED_AT,
        http_status=200,
        content_type="application/json",
        payload_sha256=PAYLOAD_SHA256,
    )


def _run(
    *,
    status: KrCoverageStatus = KrCoverageStatus.SUCCESS,
    failure_code: str | None = None,
) -> KrSourceCollectionRun:
    return KrSourceCollectionRun(
        source_run_id="kr-cycle-001:dart",
        collection_cycle_id="kr-cycle-001",
        source=KrCatalystSource.DART,
        adapter_version="opendart-list-v1",
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT,
        status=status,
        record_count=0,
        failure_code=failure_code,
        receipt_ids=(_receipt().receipt_id,),
    )
