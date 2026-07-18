from __future__ import annotations

import datetime as dt

import pytest

from tests.test_research_evidence_read_model import AS_OF, _event, _extraction
from trading_agent.canonical_event_models import CanonicalEventEnvelope, CanonicalEventOperation
from trading_agent.research_evidence_models import (
    ClaimStance,
    ResearchClaimExtraction,
    ResearchEvidenceReadModel,
)
from trading_agent.research_evidence_read_model import (
    ResearchEvidenceReadModelError,
    build_research_evidence_read_model,
)


@pytest.mark.parametrize(
    "successor_operation",
    (CanonicalEventOperation.CORRECTION, CanonicalEventOperation.TOMBSTONE),
)
def test_superseded_original_extraction_is_invalidated(
    successor_operation: CanonicalEventOperation,
) -> None:
    original = _event(
        "event-original",
        "fixture",
        "news",
        minutes_ago=10,
        digest="a" * 64,
    )
    successor = _successor(original, successor_operation)

    with pytest.raises(ResearchEvidenceReadModelError):
        _ = _build((original, successor), (_extraction(original, stance=ClaimStance.REPORTS),))


def test_correction_requires_new_exact_extraction() -> None:
    original = _event(
        "event-original",
        "fixture",
        "news",
        minutes_ago=10,
        digest="a" * 64,
    )
    correction = _successor(original, CanonicalEventOperation.CORRECTION)

    model = _build(
        (original, correction),
        (_extraction(correction, stance=ClaimStance.REPORTS),),
    )

    assert model.claims[0].evidence_ids == (_extraction(correction, stance=ClaimStance.REPORTS).evidence_id,)


def test_future_correction_does_not_invalidate_original_early() -> None:
    original = _event(
        "event-original",
        "fixture",
        "news",
        minutes_ago=10,
        digest="a" * 64,
    )
    correction = _successor(
        original,
        CanonicalEventOperation.CORRECTION,
        normalized_at=AS_OF + dt.timedelta(minutes=1),
    )

    model = _build(
        (original, correction),
        (_extraction(original, stance=ClaimStance.REPORTS),),
    )

    assert model.claims[0].latest_observed_at == original.received_at
    assert model.source_event_count == 1


def _successor(
    original: CanonicalEventEnvelope,
    operation: CanonicalEventOperation,
    *,
    normalized_at: dt.datetime = AS_OF - dt.timedelta(minutes=5),
) -> CanonicalEventEnvelope:
    return CanonicalEventEnvelope(
        event_id=f"event-{operation.value}",
        source_id=original.source_id,
        provider_event_id=original.provider_event_id,
        entity_refs=original.entity_refs,
        event_type=original.event_type,
        event_time=original.event_time,
        published_at=original.published_at,
        provider_time=normalized_at,
        received_at=normalized_at,
        normalized_at=normalized_at,
        sequence_or_offset=None,
        operation=operation,
        correction_of=original.event_id,
        raw_receipt_ref=f"receipt:{operation.value}",
        content_hash="b" * 64,
        quality_flags=("complete",),
    )


def _build(
    events: tuple[CanonicalEventEnvelope, ...],
    extractions: tuple[ResearchClaimExtraction, ...],
) -> ResearchEvidenceReadModel:
    return build_research_evidence_read_model(
        events,
        extractions,
        as_of=AS_OF,
        current_window=dt.timedelta(hours=1),
        baseline_window=dt.timedelta(hours=6),
        burst_threshold_bps=20_000,
    )
