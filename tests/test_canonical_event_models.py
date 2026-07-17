from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from trading_agent.canonical_event_models import (
    CanonicalEntityRef,
    CanonicalEntityType,
    CanonicalEventEnvelope,
    CanonicalEventOperation,
)
from trading_agent.data_capability_models import DataSourceId

EVENT_TIME = dt.datetime(2026, 7, 17, 13, 59, tzinfo=dt.UTC)
RECEIVED_AT = EVENT_TIME + dt.timedelta(seconds=1)
NORMALIZED_AT = RECEIVED_AT + dt.timedelta(milliseconds=10)


def test_event_envelope_preserves_distinct_and_inapplicable_timestamps() -> None:
    event = _event()

    payload = event.model_dump(mode="json")
    assert payload["event_time"] == "2026-07-17T13:59:00Z"
    assert payload["published_at"] is None
    assert payload["provider_time"] is None
    assert payload["received_at"] == "2026-07-17T13:59:01Z"
    assert payload["normalized_at"] == "2026-07-17T13:59:01.010000Z"
    assert event.entity_refs[0].canonical_id == "instrument:us-eq-fixture-0001"


@pytest.mark.parametrize(
    "override",
    (
        {"event_time": EVENT_TIME.replace(tzinfo=None)},
        {"published_at": EVENT_TIME.replace(tzinfo=None)},
        {"provider_time": EVENT_TIME.replace(tzinfo=None)},
        {"received_at": RECEIVED_AT.replace(tzinfo=None)},
        {"normalized_at": NORMALIZED_AT.replace(tzinfo=None)},
        {"normalized_at": RECEIVED_AT - dt.timedelta(microseconds=1)},
    ),
)
def test_event_rejects_naive_or_reverse_processing_timestamps(
    override: dict[str, object],
) -> None:
    payload = _event().model_dump(mode="python")
    payload.update(override)

    with pytest.raises(ValidationError):
        CanonicalEventEnvelope.model_validate(payload)


def test_original_event_forbids_correction_reference() -> None:
    with pytest.raises(ValidationError):
        _event(correction_of="fixture-event-0000")


@pytest.mark.parametrize(
    "operation",
    (CanonicalEventOperation.CORRECTION, CanonicalEventOperation.TOMBSTONE),
)
def test_correction_and_tombstone_require_another_event(operation: CanonicalEventOperation) -> None:
    corrected = _event(operation=operation, correction_of="fixture-event-0000")

    assert corrected.correction_of == "fixture-event-0000"
    with pytest.raises(ValidationError):
        _event(operation=operation)
    with pytest.raises(ValidationError):
        _event(operation=operation, correction_of="fixture-event-0001")


@pytest.mark.parametrize(
    "override",
    (
        {
            "entity_refs": (
                CanonicalEntityRef(
                    entity_type=CanonicalEntityType.INSTRUMENT,
                    entity_id="us-eq-fixture-0001",
                ),
                CanonicalEntityRef(
                    entity_type=CanonicalEntityType.INSTRUMENT,
                    entity_id="us-eq-fixture-0001",
                ),
            )
        },
        {"quality_flags": ("z_flag", "a_flag")},
        {"raw_receipt_ref": "/private/raw/event.bin"},
        {"raw_receipt_ref": "../raw-event"},
        {"content_hash": "A" * 64},
        {"content_hash": "a" * 63},
        {"provider_event_id": "event\nsecret"},
        {"unexpected": "field"},
    ),
)
def test_event_rejects_noncanonical_identity_or_raw_reference(
    override: dict[str, object],
) -> None:
    payload = _event().model_dump(mode="python")
    payload.update(override)

    with pytest.raises(ValidationError):
        CanonicalEventEnvelope.model_validate(payload)


def test_event_effective_interval_is_half_open() -> None:
    event = _event(
        effective_from=EVENT_TIME,
        effective_to=EVENT_TIME + dt.timedelta(minutes=1),
    )

    assert event.effective_to == EVENT_TIME + dt.timedelta(minutes=1)
    with pytest.raises(ValidationError):
        _event(effective_from=None, effective_to=EVENT_TIME)
    with pytest.raises(ValidationError):
        _event(effective_from=EVENT_TIME, effective_to=EVENT_TIME)


def _event(
    *,
    operation: CanonicalEventOperation = CanonicalEventOperation.ORIGINAL,
    correction_of: str | None = None,
    effective_from: dt.datetime | None = None,
    effective_to: dt.datetime | None = None,
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_id="fixture-event-0001",
        source_id=DataSourceId(provider="fixture", feed="sip"),
        provider_event_id="provider-event-1",
        entity_refs=(
            CanonicalEntityRef(
                entity_type=CanonicalEntityType.INSTRUMENT,
                entity_id="us-eq-fixture-0001",
            ),
        ),
        event_type="minute_bar",
        event_time=EVENT_TIME,
        published_at=None,
        provider_time=None,
        received_at=RECEIVED_AT,
        normalized_at=NORMALIZED_AT,
        effective_from=effective_from,
        effective_to=effective_to,
        sequence_or_offset="1",
        operation=operation,
        correction_of=correction_of,
        raw_receipt_ref="spool:fixture:0001",
        content_hash="a" * 64,
        quality_flags=("fixture",),
    )
