from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

import pytest
from pydantic import ValidationError

import trading_agent.canonical_dataset_models as canonical_dataset_models
from trading_agent.canonical_dataset_models import (
    CanonicalDatasetBatch,
    CanonicalDatasetPartition,
    InvalidCanonicalDatasetBatchError,
)
from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.data_capability_models import DataSourceId
from trading_agent.raw_object_manifest_models import (
    RawObjectPartitionManifest,
    RawReceipt,
    RawReceiptPayload,
)
from trading_agent.raw_receipt_projection import project_raw_receipt_partition
from trading_agent.security_master_models import DataMarketDomain

MARKET_DATE = dt.date(2026, 7, 17)
RECEIVED_AT = dt.datetime(2026, 7, 17, 9, 30, tzinfo=dt.UTC)
RAW_SECRET = b"canonical-dataset-private-raw-payload"


def test_canonical_dataset_batch_binds_partition_events_and_lineage_manifest() -> None:
    batch = _batch()

    assert batch.schema_version == 1
    assert batch.partition == _partition()
    assert batch.events[0].event_id == "event-0001"
    assert batch.events[0].raw_receipt_ref == batch.raw_manifest.receipts[0].receipt_id
    assert batch.model_dump(mode="json")["partition"] == {
        "schema_version": 1,
        "source_id": {"schema_version": 1, "provider": "synthetic", "feed": "market"},
        "market_domain": "us_equities",
        "event_type": "minute_bar",
        "market_date": "2026-07-17",
        "canonical_event_schema_version": 1,
    }


def test_canonical_dataset_batch_public_exports_preserve_raw_manifest_redaction() -> None:
    batch = _batch()
    public_dump = batch.model_dump()

    assert RAW_SECRET.decode() not in repr(batch)
    assert RAW_SECRET.decode() not in repr(public_dump)
    assert RAW_SECRET.decode() not in batch.model_dump_json()
    assert "payload" not in public_dump["raw_manifest"]
    assert "payload_base64" not in public_dump["raw_manifest"]


def test_invalid_canonical_dataset_batch_error_has_a_fixed_sanitized_message() -> None:
    error = InvalidCanonicalDatasetBatchError("account-id=request-key")

    assert str(error) == "canonical dataset batch is invalid"
    assert repr(error) == "InvalidCanonicalDatasetBatchError()"


def test_partition_model_copy_validates_updates_and_preserves_deep_copy_behavior() -> None:
    partition = _partition()
    shallow = partition.model_copy()
    copied = partition.model_copy(update={"event_type": "quote"}, deep=True)

    assert shallow.source_id is partition.source_id
    assert copied.event_type == "quote"
    assert copied.source_id == partition.source_id
    assert copied.source_id is not partition.source_id
    with pytest.raises(ValidationError):
        _ = partition.model_copy(update={"schema_version": True})
    with pytest.raises(ValidationError):
        _ = partition.model_copy(update={"market_date": dt.datetime(2026, 7, 17, tzinfo=dt.UTC)})


def test_partition_rejects_tampered_nested_source_from_constructor_and_model_copy() -> None:
    partition = _partition()
    tampered_source = partition.source_id.model_copy(update={"schema_version": True})

    with pytest.raises(ValidationError):
        _ = CanonicalDatasetPartition(
            source_id=tampered_source,
            market_domain=partition.market_domain,
            event_type=partition.event_type,
            market_date=partition.market_date,
        )
    with pytest.raises(ValidationError):
        _ = partition.model_copy(update={"source_id": tampered_source})


def test_batch_inherits_the_installed_pydantic_model_validate_contract() -> None:
    assert "model_validate" not in CanonicalDatasetBatch.__dict__


@pytest.mark.parametrize("entrypoint", ("constructor", "model_validate", "model_copy"))
def test_batch_entrypoints_validate_each_valid_event_once(
    entrypoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = _batch()
    manifest = _manifest()
    event = _event(raw_receipt_ref=manifest.receipts[0].receipt_id)
    calls = 0
    original = canonical_dataset_models._event_is_valid

    def tracked_event_validation(value: CanonicalEventEnvelope) -> bool:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(canonical_dataset_models, "_event_is_valid", tracked_event_validation)

    if entrypoint == "constructor":
        _ = CanonicalDatasetBatch(partition=_partition(), raw_manifest=manifest, events=(event,))
    elif entrypoint == "model_validate":
        _ = CanonicalDatasetBatch.model_validate(
            {
                "schema_version": 1,
                "partition": _partition(),
                "raw_manifest": manifest,
                "events": (event,),
            }
        )
    else:
        _ = batch.model_copy()

    assert calls == 1


def test_batch_sanitizes_an_augmented_exact_event_with_raw_payload() -> None:
    manifest = _manifest()
    event = _event(raw_receipt_ref=manifest.receipts[0].receipt_id)
    vars(event)["payload"] = RAW_SECRET

    with pytest.raises(ValidationError) as captured:
        _ = CanonicalDatasetBatch(partition=_partition(), raw_manifest=manifest, events=(event,))

    _assert_sanitized_error(captured.value)


@pytest.mark.parametrize("entrypoint", ("constructor", "model_validate", "model_copy"))
def test_batch_sanitizes_an_augmented_exact_raw_manifest_across_public_entrypoints(entrypoint: str) -> None:
    batch = _batch()
    manifest = batch.raw_manifest.model_copy(update={"payload": RAW_SECRET})

    with pytest.raises(ValidationError) as captured:
        if entrypoint == "constructor":
            _ = CanonicalDatasetBatch(
                partition=batch.partition,
                raw_manifest=manifest,
                events=batch.events,
            )
        elif entrypoint == "model_validate":
            _ = CanonicalDatasetBatch.model_validate(
                {
                    "schema_version": 1,
                    "partition": batch.partition,
                    "raw_manifest": manifest,
                    "events": batch.events,
                }
            )
        else:
            _ = batch.model_copy(update={"raw_manifest": manifest})

    _assert_sanitized_error(captured.value)


def test_batch_model_copy_validates_empty_events_and_mismatched_manifest_date() -> None:
    batch = _batch()
    mismatched_manifest = batch.raw_manifest.model_copy(
        update={"market_date": MARKET_DATE + dt.timedelta(days=1)}
    )

    with pytest.raises(ValidationError):
        _ = batch.model_copy(update={"events": ()})
    with pytest.raises(ValidationError):
        _ = batch.model_copy(update={"raw_manifest": mismatched_manifest})


def test_batch_model_copy_validates_nested_partition_schema_versions_and_deep_copy_behavior() -> None:
    batch = _batch()
    copied = batch.model_copy(deep=True)
    invalid_partition = CanonicalDatasetPartition.model_construct(
        schema_version=1,
        source_id=batch.partition.source_id,
        market_domain=batch.partition.market_domain,
        event_type=batch.partition.event_type,
        market_date=batch.partition.market_date,
        canonical_event_schema_version=True,
    )

    assert copied == batch
    assert copied.partition is not batch.partition
    assert copied.raw_manifest is not batch.raw_manifest
    assert copied.events[0] is not batch.events[0]
    with pytest.raises(ValidationError):
        _ = batch.model_copy(update={"partition": invalid_partition})


@pytest.mark.parametrize("unsafe_field", ("raw_manifest", "events"))
def test_batch_constructor_sanitizes_untrusted_raw_inputs(unsafe_field: str) -> None:
    batch = _batch()
    unsafe_values: Any = {
        "raw_manifest": batch.raw_manifest,
        "events": batch.events,
    }
    unsafe_values[unsafe_field] = (
        {"payload": RAW_SECRET} if unsafe_field == "raw_manifest" else ({"payload": RAW_SECRET},)
    )

    with pytest.raises(ValidationError) as captured:
        _ = CanonicalDatasetBatch(partition=batch.partition, **unsafe_values)

    _assert_sanitized_error(captured.value)


@pytest.mark.parametrize("unsafe_field", ("raw_manifest", "events"))
def test_batch_model_validate_sanitizes_untrusted_raw_inputs(unsafe_field: str) -> None:
    batch = _batch()
    unsafe_values: dict[str, object] = {
        "schema_version": 1,
        "partition": batch.partition,
        "raw_manifest": batch.raw_manifest,
        "events": batch.events,
    }
    unsafe_values[unsafe_field] = (
        {"payload": RAW_SECRET} if unsafe_field == "raw_manifest" else ({"payload": RAW_SECRET},)
    )

    with pytest.raises(ValidationError) as captured:
        _ = CanonicalDatasetBatch.model_validate(unsafe_values)

    _assert_sanitized_error(captured.value)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("event_type", "Minute_Bar"),
        ("event_type", "minute/bar"),
        ("market_date", dt.datetime(2026, 7, 17, tzinfo=dt.UTC)),
        ("market_date", "2026-07-17"),
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("canonical_event_schema_version", True),
        ("canonical_event_schema_version", "1"),
        ("canonical_event_schema_version", 2),
    ),
)
def test_partition_requires_canonical_event_type_exact_date_and_version_ints(
    field: str,
    value: object,
) -> None:
    payload = _partition().model_dump(mode="python")
    payload[field] = value

    with pytest.raises(ValidationError):
        _ = CanonicalDatasetPartition.model_validate(payload)


@pytest.mark.parametrize("schema_version", (True, "1", 2))
def test_batch_requires_an_exact_current_schema_version(schema_version: object) -> None:
    batch = _batch()

    with pytest.raises(ValidationError):
        _ = CanonicalDatasetBatch.model_validate(
            {
                "schema_version": schema_version,
                "partition": batch.partition,
                "raw_manifest": batch.raw_manifest,
                "events": batch.events,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (("schema_version", True), ("canonical_event_schema_version", True)),
)
def test_partition_model_copy_rejects_tampered_schema_versions(field: str, value: object) -> None:
    batch = _batch()

    with pytest.raises(ValidationError):
        _ = batch.partition.model_copy(update={field: value})


@pytest.mark.parametrize(
    "events_or_manifest",
    (
        lambda batch: (
            batch.events[0].model_copy(update={"source_id": DataSourceId(provider="alternate", feed="market")}),
        ),
        lambda batch: (
            batch.events[0].model_copy(update={"event_type": "quote"}),
        ),
        lambda batch: (
            batch.events[0].model_copy(update={"schema_version": 2}),
        ),
        lambda batch: (
            batch.events[0].model_copy(update={"schema_version": True}),
        ),
        lambda batch: (
            batch.events[0].model_copy(update={"raw_receipt_ref": "z" * 64}),
        ),
    ),
)
def test_batch_rejects_event_values_outside_its_partition_or_manifest(
    events_or_manifest: object,
) -> None:
    batch = _batch()
    events = events_or_manifest(batch)  # type: ignore[operator]

    with pytest.raises(ValidationError):
        _ = CanonicalDatasetBatch(
            partition=batch.partition,
            raw_manifest=batch.raw_manifest,
            events=events,
        )


def test_batch_rejects_a_raw_manifest_for_a_different_market_date() -> None:
    batch = _batch()
    mismatched_manifest = batch.raw_manifest.model_copy(
        update={"market_date": MARKET_DATE + dt.timedelta(days=1)}
    )

    with pytest.raises(ValidationError):
        _ = CanonicalDatasetBatch(
            partition=batch.partition,
            raw_manifest=mismatched_manifest,
            events=batch.events,
        )


@pytest.mark.parametrize(
    "events",
    (
        lambda manifest: (
            _event(event_id="event-0002", raw_receipt_ref=manifest.receipts[1].receipt_id),
            _event(event_id="event-0001", raw_receipt_ref=manifest.receipts[0].receipt_id),
        ),
        lambda manifest: (
            _event(event_id="event-0001", raw_receipt_ref=manifest.receipts[0].receipt_id),
            _event(event_id="event-0001", raw_receipt_ref=manifest.receipts[1].receipt_id),
        ),
    ),
)
def test_batch_requires_nonempty_strictly_sorted_unique_event_ids(events: object) -> None:
    manifest = _manifest(receipt_ids=("a" * 64, "b" * 64))
    event_values = events(manifest)  # type: ignore[operator]

    with pytest.raises(ValidationError):
        _ = CanonicalDatasetBatch(
            partition=_partition(),
            raw_manifest=manifest,
            events=event_values,
        )
    with pytest.raises(ValidationError):
        _ = CanonicalDatasetBatch(
            partition=_partition(),
            raw_manifest=manifest,
            events=(),
        )


def test_batch_rejects_raw_manifest_and_event_subclasses() -> None:
    batch = _batch()
    manifest_subclass = _RawManifestSubclass.model_validate(batch.raw_manifest.model_dump(mode="python"))
    event_subclass = _EventSubclass.model_validate(batch.events[0].model_dump(mode="python"))

    with pytest.raises(ValidationError):
        _ = CanonicalDatasetBatch(
            partition=batch.partition,
            raw_manifest=manifest_subclass,
            events=batch.events,
        )
    with pytest.raises(ValidationError):
        _ = CanonicalDatasetBatch(
            partition=batch.partition,
            raw_manifest=batch.raw_manifest,
            events=(event_subclass,),
        )


def test_partition_and_batch_forbid_extra_fields() -> None:
    partition_payload = _partition().model_dump(mode="python") | {"ticker": "AAPL"}
    batch = _batch()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ = CanonicalDatasetPartition.model_validate(partition_payload)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ = CanonicalDatasetBatch.model_validate(
            {
                "schema_version": 1,
                "partition": batch.partition,
                "raw_manifest": batch.raw_manifest,
                "events": batch.events,
                "raw_payload": RAW_SECRET,
            }
        )


class _RawManifestSubclass(RawObjectPartitionManifest):
    pass


class _EventSubclass(CanonicalEventEnvelope):
    pass


def _batch() -> CanonicalDatasetBatch:
    manifest = _manifest()
    return CanonicalDatasetBatch(
        partition=_partition(),
        raw_manifest=manifest,
        events=(_event(raw_receipt_ref=manifest.receipts[0].receipt_id),),
    )


def _partition() -> CanonicalDatasetPartition:
    return CanonicalDatasetPartition(
        source_id=DataSourceId(provider="synthetic", feed="market"),
        market_domain=DataMarketDomain.US_EQUITIES,
        event_type="minute_bar",
        market_date=MARKET_DATE,
    )


def _manifest(*, receipt_ids: tuple[str, ...] = ("a" * 64,)) -> RawObjectPartitionManifest:
    receipts = tuple(
        RawReceipt.from_payload(
            receipt_id=receipt_id,
            source_id="synthetic.market",
            market_date=MARKET_DATE,
            received_at=RECEIVED_AT + dt.timedelta(seconds=index),
            payload_sha256=hashlib.sha256(RAW_SECRET + receipt_id.encode()).hexdigest(),
            payload=RawReceiptPayload(RAW_SECRET + receipt_id.encode()),
        )
        for index, receipt_id in enumerate(receipt_ids)
    )
    return project_raw_receipt_partition(
        receipts,
        source_id="synthetic.market",
        market_date=MARKET_DATE,
        parent_ledger_generation=3,
    )


def _event(*, event_id: str = "event-0001", raw_receipt_ref: str) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_id=event_id,
        source_id=DataSourceId(provider="synthetic", feed="market"),
        entity_refs=(
            CanonicalEntityRef(
                entity_type=CanonicalEntityType.INSTRUMENT,
                entity_id="us-eq-fixture-0001",
            ),
        ),
        event_type="minute_bar",
        received_at=RECEIVED_AT,
        normalized_at=RECEIVED_AT,
        operation=CanonicalEventOperation.ORIGINAL,
        raw_receipt_ref=raw_receipt_ref,
        content_hash="c" * 64,
    )


def _assert_sanitized_error(error: Exception) -> None:
    assert RAW_SECRET.decode() not in str(error)
    assert RAW_SECRET.decode() not in repr(error)
